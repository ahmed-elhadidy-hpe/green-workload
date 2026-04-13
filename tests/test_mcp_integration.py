"""Unit tests for the MCP integration layer in GreenWorkloadAgent.

All external I/O (MCP servers, LLM, DB) is mocked so these tests run
entirely in-process without a database or running MCP subprocesses.
"""

import asyncio
import contextlib
import json
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

import pytest

from src.agent.agent import GreenWorkloadAgent, _LLM_TOOL_ALLOWLIST


# ── Fixtures ─────────────────────────────────────────────────────────


@pytest.fixture
def agent(monkeypatch):
    """GreenWorkloadAgent instance with all external dependencies mocked out."""
    monkeypatch.setattr("src.agent.agent.GreenWorkloadRepository", lambda: MagicMock())
    monkeypatch.setattr(
        "src.agent.agent.SafetyValidator", lambda repo, settings: MagicMock()
    )
    a = object.__new__(GreenWorkloadAgent)
    a.repo = MagicMock()
    a.safety = MagicMock()
    a.llm = MagicMock()
    a.model = "test-model"
    a.url = "http://localhost:9999"
    # MCP state
    a._mcp_sessions = {}
    a._llm_tools = []
    a._all_tools = []
    a._tool_server_map = {}
    return a


def _make_tool(name, description="A tool", schema=None):
    """Return a fake MCP tool object."""
    t = MagicMock()
    t.name = name
    t.description = description
    t.inputSchema = schema or {"type": "object", "properties": {}}
    return t


def _make_tool_result(text: str):
    """Return a fake MCP CallToolResult with a single TextContent item."""
    content_item = MagicMock()
    content_item.text = text
    result = MagicMock()
    result.content = [content_item]
    return result


# ── _LLM_TOOL_ALLOWLIST ───────────────────────────────────────────────


class TestAllowlist:
    def test_contains_read_only_tools(self):
        assert "get_all_zones_energy_status" in _LLM_TOOL_ALLOWLIST
        assert "get_migratable_workloads" in _LLM_TOOL_ALLOWLIST
        assert "get_cluster_topology" in _LLM_TOOL_ALLOWLIST
        assert "get_migration_history" in _LLM_TOOL_ALLOWLIST

    def test_excludes_write_tools(self):
        assert "execute_migration" not in _LLM_TOOL_ALLOWLIST
        assert "record_migration_event" not in _LLM_TOOL_ALLOWLIST
        assert "create_agent_run" not in _LLM_TOOL_ALLOWLIST
        assert "rollback_migration" not in _LLM_TOOL_ALLOWLIST


# ── _connect_mcp_servers ──────────────────────────────────────────────


class TestConnectMcpServers:
    @pytest.mark.asyncio
    async def test_registers_tools_correctly(self, agent, monkeypatch):
        """All tools returned by list_tools() are registered; only allowlisted ones go to _llm_tools."""
        session = AsyncMock()
        session.initialize = AsyncMock()
        tools_response = MagicMock()
        tools_response.tools = [
            _make_tool("get_all_zones_energy_status"),   # in allowlist
            _make_tool("execute_migration"),             # NOT in allowlist
        ]
        session.list_tools = AsyncMock(return_value=tools_response)

        # Patch stdio_client and ClientSession to return our mock session
        async def fake_stdio(params):
            return AsyncMock(), AsyncMock()

        @contextlib.asynccontextmanager
        async def fake_client_session(read, write):
            yield session

        monkeypatch.setattr("src.agent.agent.stdio_client", contextlib.asynccontextmanager(lambda p: fake_stdio(p)))
        monkeypatch.setattr("src.agent.agent.ClientSession", fake_client_session)

        stack = contextlib.AsyncExitStack()
        async with stack:
            await agent._connect_mcp_servers(stack)

        assert "get_all_zones_energy_status" in agent._tool_server_map
        assert "execute_migration" in agent._tool_server_map
        assert len(agent._all_tools) == 2
        # Only the allowlisted tool goes to _llm_tools
        llm_tool_names = [t["function"]["name"] for t in agent._llm_tools]
        assert "get_all_zones_energy_status" in llm_tool_names
        assert "execute_migration" not in llm_tool_names

    @pytest.mark.asyncio
    async def test_failed_server_is_skipped_gracefully(self, agent, monkeypatch):
        """A server that raises on connect should not crash the whole setup."""
        call_count = {"n": 0}

        @contextlib.asynccontextmanager
        async def failing_stdio(params):
            call_count["n"] += 1
            raise RuntimeError("connection refused")
            yield  # pragma: no cover

        monkeypatch.setattr("src.agent.agent.stdio_client", failing_stdio)

        stack = contextlib.AsyncExitStack()
        async with stack:
            # Should not raise
            await agent._connect_mcp_servers(stack)

        assert agent._mcp_sessions == {}
        assert agent._llm_tools == []


# ── _call_mcp_tool ────────────────────────────────────────────────────


class TestCallMcpTool:
    @pytest.mark.asyncio
    async def test_dispatches_to_correct_server(self, agent):
        session = AsyncMock()
        session.call_tool = AsyncMock(return_value=_make_tool_result('{"zones": []}'))
        agent._mcp_sessions = {"green_energy": session}
        agent._tool_server_map = {"get_all_zones_energy_status": "green_energy"}

        result = await agent._call_mcp_tool("get_all_zones_energy_status", {})
        assert json.loads(result) == {"zones": []}
        session.call_tool.assert_awaited_once_with("get_all_zones_energy_status", {})

    @pytest.mark.asyncio
    async def test_unknown_tool_returns_error(self, agent):
        result = await agent._call_mcp_tool("nonexistent_tool", {})
        parsed = json.loads(result)
        assert "error" in parsed
        assert "nonexistent_tool" in parsed["error"]

    @pytest.mark.asyncio
    async def test_disconnected_server_returns_error(self, agent):
        agent._tool_server_map = {"some_tool": "missing_server"}
        agent._mcp_sessions = {}  # server not connected

        result = await agent._call_mcp_tool("some_tool", {})
        parsed = json.loads(result)
        assert "error" in parsed

    @pytest.mark.asyncio
    async def test_tool_exception_returns_error_json(self, agent):
        session = AsyncMock()
        session.call_tool = AsyncMock(side_effect=RuntimeError("network error"))
        agent._mcp_sessions = {"db": session}
        agent._tool_server_map = {"get_cluster_topology": "db"}

        result = await agent._call_mcp_tool("get_cluster_topology", {})
        parsed = json.loads(result)
        assert "error" in parsed
        assert "network error" in parsed["error"]

    @pytest.mark.asyncio
    async def test_multi_content_items_joined_as_array(self, agent):
        item1, item2 = MagicMock(), MagicMock()
        item1.text = '{"a": 1}'
        item2.text = '{"b": 2}'
        result_obj = MagicMock()
        result_obj.content = [item1, item2]
        session = AsyncMock()
        session.call_tool = AsyncMock(return_value=result_obj)
        agent._mcp_sessions = {"s": session}
        agent._tool_server_map = {"my_tool": "s"}

        result = await agent._call_mcp_tool("my_tool", {})
        parsed = json.loads(result)
        assert isinstance(parsed, list)
        assert len(parsed) == 2

    @pytest.mark.asyncio
    async def test_empty_content_returns_success(self, agent):
        result_obj = MagicMock()
        result_obj.content = []
        session = AsyncMock()
        session.call_tool = AsyncMock(return_value=result_obj)
        agent._mcp_sessions = {"s": session}
        agent._tool_server_map = {"empty_tool": "s"}

        result = await agent._call_mcp_tool("empty_tool", {})
        parsed = json.loads(result)
        assert parsed == {"result": "success"}


# ── _call_llm_with_tools ──────────────────────────────────────────────


def _make_llm_response(content=None, tool_calls=None):
    """Build a fake OpenAI chat completion response."""
    msg = MagicMock()
    msg.content = content
    msg.tool_calls = tool_calls or []
    choice = MagicMock()
    choice.message = msg
    choice.finish_reason = "stop" if not tool_calls else "tool_calls"
    usage = MagicMock()
    usage.prompt_tokens = 10
    usage.completion_tokens = 20
    response = MagicMock()
    response.choices = [choice]
    response.usage = usage
    return response


def _make_tool_call(tc_id, name, arguments_dict):
    """Build a fake OpenAI tool_call object."""
    tc = MagicMock()
    tc.id = tc_id
    tc.function.name = name
    tc.function.arguments = json.dumps(arguments_dict)
    return tc


class TestCallLlmWithTools:
    # ── Fallback when no tools are available ─────────────────────────

    @pytest.mark.asyncio
    async def test_falls_back_to_call_llm_when_no_tools(self, agent):
        """With no MCP tools registered, should call _call_llm with manually gathered context."""
        agent._llm_tools = []
        agent.repo.get_cluster_topology.return_value = {"clusters": []}
        agent.repo.get_migration_history.return_value = []
        agent.repo.get_all_zones_with_energy.return_value = []

        expected_decision = {"decision_type": "skip", "reasoning": "no workloads", "actions": []}

        with patch.object(agent, "_call_llm", new=AsyncMock(return_value=expected_decision)) as mock_call_llm:
            result = await agent._call_llm_with_tools([])

        assert result == expected_decision
        mock_call_llm.assert_awaited_once()

    # ── Direct final response (no tool calls) ────────────────────────

    @pytest.mark.asyncio
    async def test_direct_json_response_no_tools_called(self, agent):
        """LLM returns JSON directly without calling any tools."""
        agent._llm_tools = [
            {"type": "function", "function": {"name": "get_all_zones_energy_status", "description": "", "parameters": {}}}
        ]

        final_decision = {"decision_type": "skip", "reasoning": "all green", "actions": []}
        agent.llm.chat.completions.create = AsyncMock(
            return_value=_make_llm_response(content=json.dumps(final_decision))
        )

        result = await agent._call_llm_with_tools([])
        assert result["decision_type"] == "skip"

    # ── Single tool call then final answer ───────────────────────────

    @pytest.mark.asyncio
    async def test_single_tool_call_then_decision(self, agent):
        """LLM calls one MCP tool, then outputs the final JSON decision."""
        agent._llm_tools = [
            {"type": "function", "function": {"name": "get_all_zones_energy_status", "description": "", "parameters": {}}}
        ]
        agent._tool_server_map = {"get_all_zones_energy_status": "green_energy"}
        energy_data = {"zones": [{"zone_name": "us-east", "renewable_percentage": 85}]}
        session = AsyncMock()
        session.call_tool = AsyncMock(return_value=_make_tool_result(json.dumps(energy_data)))
        agent._mcp_sessions = {"green_energy": session}

        final_decision = {"decision_type": "skip", "reasoning": "everything is green", "actions": []}
        tool_call = _make_tool_call("tc1", "get_all_zones_energy_status", {})

        responses = [
            _make_llm_response(tool_calls=[tool_call]),        # first: call tool
            _make_llm_response(content=json.dumps(final_decision)),  # second: final answer
        ]
        agent.llm.chat.completions.create = AsyncMock(side_effect=responses)

        result = await agent._call_llm_with_tools([])
        assert result["decision_type"] == "skip"
        assert agent.llm.chat.completions.create.await_count == 2
        session.call_tool.assert_awaited_once_with("get_all_zones_energy_status", {})

    # ── Multiple tool calls across iterations ────────────────────────

    @pytest.mark.asyncio
    async def test_multiple_tool_calls_then_decision(self, agent):
        """LLM calls several tools across multiple iterations before deciding."""
        tool_names = ["get_all_zones_energy_status", "get_migratable_workloads", "get_cluster_topology"]
        agent._llm_tools = [
            {"type": "function", "function": {"name": n, "description": "", "parameters": {}}}
            for n in tool_names
        ]
        agent._tool_server_map = {n: "test_server" for n in tool_names}
        session = AsyncMock()
        session.call_tool = AsyncMock(return_value=_make_tool_result("{}"))
        agent._mcp_sessions = {"test_server": session}

        final_decision = {
            "decision_type": "migrate",
            "reasoning": "high carbon",
            "actions": [{"workload_name": "api", "namespace": "default", "workload_type": "Deployment",
                          "source_node_name": "n1", "destination_node_name": "n2", "reason": "carbon"}],
        }

        responses = [
            _make_llm_response(tool_calls=[_make_tool_call("t1", "get_all_zones_energy_status", {})]),
            _make_llm_response(tool_calls=[_make_tool_call("t2", "get_migratable_workloads", {})]),
            _make_llm_response(tool_calls=[_make_tool_call("t3", "get_cluster_topology", {})]),
            _make_llm_response(content=json.dumps(final_decision)),
        ]
        agent.llm.chat.completions.create = AsyncMock(side_effect=responses)

        result = await agent._call_llm_with_tools([])
        assert result["decision_type"] == "migrate"
        assert agent.llm.chat.completions.create.await_count == 4
        assert session.call_tool.await_count == 3

    # ── LLM call failure → rule-based fallback ───────────────────────

    @pytest.mark.asyncio
    async def test_llm_exception_falls_back_to_rule_based(self, agent):
        agent._llm_tools = [
            {"type": "function", "function": {"name": "get_all_zones_energy_status", "description": "", "parameters": {}}}
        ]
        agent.llm.chat.completions.create = AsyncMock(side_effect=RuntimeError("LLM timeout"))
        agent.repo.get_all_zones_with_energy.return_value = []
        agent.repo.get_cluster_topology.return_value = {"clusters": []}

        with patch.object(agent, "_rule_based_fallback", return_value={"decision_type": "skip", "reasoning": "fallback", "actions": []}) as mock_rb:
            result = await agent._call_llm_with_tools([])

        assert result["decision_type"] == "skip"
        mock_rb.assert_called_once()

    # ── Max iterations fallback ──────────────────────────────────────

    @pytest.mark.asyncio
    async def test_max_iterations_falls_back_to_call_llm(self, agent):
        """After 15 tool-calling rounds with no final answer, fall back to _call_llm."""
        agent._llm_tools = [
            {"type": "function", "function": {"name": "get_all_zones_energy_status", "description": "", "parameters": {}}}
        ]
        agent._tool_server_map = {"get_all_zones_energy_status": "s"}
        session = AsyncMock()
        session.call_tool = AsyncMock(return_value=_make_tool_result("{}"))
        agent._mcp_sessions = {"s": session}

        # Always return a tool call (never terminates naturally)
        infinite_tool_call = _make_tool_call("tx", "get_all_zones_energy_status", {})
        agent.llm.chat.completions.create = AsyncMock(
            return_value=_make_llm_response(tool_calls=[infinite_tool_call])
        )

        fallback_decision = {"decision_type": "skip", "reasoning": "manual", "actions": []}
        agent.repo.get_all_zones_with_energy.return_value = []
        agent.repo.get_cluster_topology.return_value = {"clusters": []}
        agent.repo.get_migration_history.return_value = []

        with patch.object(agent, "_call_llm", new=AsyncMock(return_value=fallback_decision)) as mock_fallback:
            result = await agent._call_llm_with_tools([])

        assert result == fallback_decision
        mock_fallback.assert_awaited_once()
        # Should have run exactly 15 iterations
        assert agent.llm.chat.completions.create.await_count == 15

    # ── Bad JSON in tool arguments ───────────────────────────────────

    @pytest.mark.asyncio
    async def test_malformed_tool_arguments_treated_as_empty(self, agent):
        """If the LLM outputs non-JSON tool arguments, use empty dict gracefully."""
        agent._llm_tools = [
            {"type": "function", "function": {"name": "get_cluster_topology", "description": "", "parameters": {}}}
        ]
        agent._tool_server_map = {"get_cluster_topology": "db"}
        session = AsyncMock()
        session.call_tool = AsyncMock(return_value=_make_tool_result("{}"))
        agent._mcp_sessions = {"db": session}

        bad_tc = MagicMock()
        bad_tc.id = "tc_bad"
        bad_tc.function.name = "get_cluster_topology"
        bad_tc.function.arguments = "NOT_VALID_JSON"

        final = {"decision_type": "skip", "reasoning": "ok", "actions": []}
        responses = [
            _make_llm_response(tool_calls=[bad_tc]),
            _make_llm_response(content=json.dumps(final)),
        ]
        agent.llm.chat.completions.create = AsyncMock(side_effect=responses)

        result = await agent._call_llm_with_tools([])
        assert result["decision_type"] == "skip"
        # Tool was called with empty dict due to JSON parse failure
        session.call_tool.assert_awaited_once_with("get_cluster_topology", {})


# ── _do_migrate ───────────────────────────────────────────────────────


class TestDoMigrate:
    @pytest.mark.asyncio
    async def test_dry_run_returns_true_immediately(self, agent, monkeypatch):
        monkeypatch.setattr("src.agent.agent.settings.DRY_RUN", True)
        result = await agent._do_migrate({
            "workload_name": "api", "destination_node_name": "n2",
            "namespace": "default", "workload_type": "Deployment", "cluster_id": "",
        })
        assert result is True

    @pytest.mark.asyncio
    async def test_uses_k8s_mcp_when_connected(self, agent, monkeypatch):
        monkeypatch.setattr("src.agent.agent.settings.DRY_RUN", False)

        agent._tool_server_map = {
            "validate_migration_feasibility": "kubernetes",
            "execute_migration": "kubernetes",
        }
        k8s_session = AsyncMock()

        def call_tool_side_effect(name, args):
            if name == "validate_migration_feasibility":
                return _make_tool_result(json.dumps({"feasible": True, "checks": {}}))
            if name == "execute_migration":
                return _make_tool_result(json.dumps({"success": True, "dry_run": False}))

        k8s_session.call_tool = AsyncMock(side_effect=call_tool_side_effect)
        agent._mcp_sessions = {"kubernetes": k8s_session}

        result = await agent._do_migrate({
            "workload_name": "api", "destination_node_name": "n2",
            "namespace": "default", "workload_type": "Deployment", "cluster_id": "c1",
        })

        assert result is True
        assert k8s_session.call_tool.await_count == 2

    @pytest.mark.asyncio
    async def test_infeasible_migration_returns_false(self, agent, monkeypatch):
        monkeypatch.setattr("src.agent.agent.settings.DRY_RUN", False)

        agent._tool_server_map = {"validate_migration_feasibility": "kubernetes"}
        session = AsyncMock()
        session.call_tool = AsyncMock(
            return_value=_make_tool_result(json.dumps({"feasible": False, "checks": {"node_ready": False}}))
        )
        agent._mcp_sessions = {"kubernetes": session}

        result = await agent._do_migrate({
            "workload_name": "api", "destination_node_name": "n2",
            "namespace": "default", "workload_type": "Deployment", "cluster_id": "",
        })

        assert result is False
        # execute_migration should NOT have been called
        calls = [str(c) for c in session.call_tool.call_args_list]
        assert all("execute_migration" not in c for c in calls)

    @pytest.mark.asyncio
    async def test_simulates_when_k8s_not_connected(self, agent, monkeypatch):
        monkeypatch.setattr("src.agent.agent.settings.DRY_RUN", False)
        monkeypatch.setattr(
            "src.agent.agent.settings.SIMULATED_MIGRATION_EXEC_TIME_BETWEEN_SEC", (0, 0)
        )
        agent._mcp_sessions = {}  # kubernetes not connected

        result = await agent._do_migrate({
            "workload_name": "api", "destination_node_name": "n2",
            "namespace": "default", "workload_type": "Deployment", "cluster_id": "",
        })
        assert result is True

    @pytest.mark.asyncio
    async def test_k8s_mcp_execute_failure_returns_false(self, agent, monkeypatch):
        monkeypatch.setattr("src.agent.agent.settings.DRY_RUN", False)

        agent._tool_server_map = {
            "validate_migration_feasibility": "kubernetes",
            "execute_migration": "kubernetes",
        }
        session = AsyncMock()

        def side_effect(name, args):
            if name == "validate_migration_feasibility":
                return _make_tool_result(json.dumps({"feasible": True}))
            return _make_tool_result(json.dumps({"success": False, "error": "node unreachable"}))

        session.call_tool = AsyncMock(side_effect=side_effect)
        agent._mcp_sessions = {"kubernetes": session}

        result = await agent._do_migrate({
            "workload_name": "api", "destination_node_name": "n2",
            "namespace": "default", "workload_type": "Deployment", "cluster_id": "",
        })
        assert result is False
