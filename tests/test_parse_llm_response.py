"""Unit tests for GreenWorkloadAgent._parse_llm_response and _repair_truncated_json."""

import json
import pytest

from src.agent.agent import GreenWorkloadAgent


@pytest.fixture
def agent(monkeypatch):
    """Create a GreenWorkloadAgent without touching the DB or LLM client."""
    monkeypatch.setattr("src.agent.agent.GreenWorkloadRepository", lambda: None)
    monkeypatch.setattr("src.agent.agent.SafetyValidator", lambda repo, settings: None)
    a = object.__new__(GreenWorkloadAgent)
    a.repo = None
    a.safety = None
    a.llm = None
    return a


# ── Direct JSON parsing ──────────────────────────────────────────────


class TestDirectJSON:
    def test_valid_migrate(self, agent):
        raw = json.dumps(
            {
                "decision_type": "migrate",
                "reasoning": "High carbon detected",
                "actions": [
                    {
                        "workload_name": "api-gw",
                        "namespace": "prod",
                        "workload_type": "Deployment",
                        "source_node_name": "dirty-1",
                        "destination_node_name": "green-1",
                        "reason": "carbon gap",
                    }
                ],
            }
        )
        result = agent._parse_llm_response(raw)
        assert result["decision_type"] == "migrate"
        assert len(result["actions"]) == 1
        assert result["actions"][0]["workload_name"] == "api-gw"

    def test_valid_skip(self, agent):
        raw = json.dumps(
            {
                "decision_type": "skip",
                "reasoning": "No gap",
                "actions": [],
            }
        )
        result = agent._parse_llm_response(raw)
        assert result["decision_type"] == "skip"
        assert result["actions"] == []

    def test_valid_wait(self, agent):
        raw = json.dumps(
            {
                "decision_type": "wait",
                "reasoning": "Green nodes at capacity",
                "actions": [],
            }
        )
        result = agent._parse_llm_response(raw)
        assert result["decision_type"] == "wait"

    def test_whitespace_padded(self, agent):
        raw = (
            "  \n "
            + json.dumps({"decision_type": "skip", "reasoning": "ok", "actions": []})
            + " \n "
        )
        result = agent._parse_llm_response(raw)
        assert result["decision_type"] == "skip"

    def test_multiple_actions(self, agent):
        actions = [
            {
                "workload_name": f"wl-{i}",
                "namespace": "ns",
                "workload_type": "Deployment",
                "source_node_name": "src",
                "destination_node_name": "dst",
                "reason": "r",
            }
            for i in range(5)
        ]
        raw = json.dumps(
            {"decision_type": "migrate", "reasoning": "go", "actions": actions}
        )
        result = agent._parse_llm_response(raw)
        assert len(result["actions"]) == 5


# ── Markdown code fence stripping ────────────────────────────────────


class TestCodeFenceStripping:
    def test_json_code_fence(self, agent):
        inner = json.dumps({"decision_type": "skip", "reasoning": "ok", "actions": []})
        raw = f"```json\n{inner}\n```"
        result = agent._parse_llm_response(raw)
        assert result["decision_type"] == "skip"

    def test_plain_code_fence(self, agent):
        inner = json.dumps(
            {"decision_type": "wait", "reasoning": "busy", "actions": []}
        )
        raw = f"```\n{inner}\n```"
        result = agent._parse_llm_response(raw)
        assert result["decision_type"] == "wait"

    def test_triple_backtick_no_newline(self, agent):
        inner = json.dumps({"decision_type": "skip", "reasoning": "x", "actions": []})
        raw = f"```{inner}```"
        # inner won't start with ``` after stripping lines that start with ```, so it should still work
        result = agent._parse_llm_response(raw)
        assert result["decision_type"] == "skip"


# ── JSON extraction (text before/after JSON) ─────────────────────────


class TestJSONExtraction:
    def test_text_before_json(self, agent):
        inner = json.dumps(
            {"decision_type": "migrate", "reasoning": "go", "actions": []}
        )
        raw = f"Here is my analysis:\n{inner}"
        result = agent._parse_llm_response(raw)
        assert result["decision_type"] == "migrate"

    def test_text_after_json(self, agent):
        inner = json.dumps({"decision_type": "skip", "reasoning": "no", "actions": []})
        raw = f"{inner}\nHope that helps!"
        result = agent._parse_llm_response(raw)
        assert result["decision_type"] == "skip"

    def test_text_surrounding_json(self, agent):
        inner = json.dumps(
            {"decision_type": "wait", "reasoning": "waiting", "actions": []}
        )
        raw = f"Sure, here is the decision:\n{inner}\nLet me know if you need more."
        result = agent._parse_llm_response(raw)
        assert result["decision_type"] == "wait"

    def test_text_surrounding_two_json_first_one_are_wrong(self, agent):
        inner_malform = "{\"decision_type\": \"migrate\",\"decision_type\": \"wait\",\"reasoning\": \"waiting\",\"actions\": []}"
        inner = json.dumps(
            {"decision_type": "wait 2", "reasoning": "waiting", "actions": []}
        )
        raw = f"Sure, {inner_malform} here is the decision:\n{inner}\nLet me know if you need more."
        result = agent._parse_llm_response(raw)
        assert result["decision_type"] == "wait 2"

    def test_nested_braces_in_reason(self, agent):
        """JSON with braces inside string values should still parse."""
        payload = {
            "decision_type": "migrate",
            "reasoning": "Gap is {large}",
            "actions": [
                {
                    "workload_name": "svc",
                    "namespace": "ns",
                    "workload_type": "Deployment",
                    "source_node_name": "src",
                    "destination_node_name": "dst",
                    "reason": "test {nested} braces",
                }
            ],
        }
        raw = "Analysis:\n" + json.dumps(payload) + "\nDone."
        result = agent._parse_llm_response(raw)
        assert result["decision_type"] == "migrate"
        assert len(result["actions"]) == 1


# ── Truncated JSON repair ────────────────────────────────────────────


class TestTruncatedRepair:
    def _make_truncated(self, full_json: str, cut_chars: int) -> str:
        return full_json[:-cut_chars]

    def test_repair_truncated_action_mid_object(self, agent):
        """Simulate token limit cutting mid-way through an action object."""
        payload = {
            "decision_type": "migrate",
            "reasoning": "carbon gap",
            "actions": [
                {
                    "workload_name": "wl-1",
                    "namespace": "prod",
                    "workload_type": "Deployment",
                    "source_node_name": "dirty",
                    "destination_node_name": "green",
                    "reason": "high carbon",
                },
                {
                    "workload_name": "wl-2",
                    "namespace": "prod",
                    "workload_type": "Deployment",
                    "source_node_name": "dirty",
                    "destination_node_name": "green",
                    "reason": "high carbon",
                },
            ],
        }
        full = json.dumps(payload)
        # Cut 50 chars off the end (mid-second action)
        truncated = self._make_truncated(full, 50)
        result = agent._parse_llm_response(truncated, truncated=True)
        assert result["decision_type"] == "migrate"
        # Should recover at least the first action
        assert len(result["actions"]) >= 1

    def test_repair_truncated_at_closing_bracket(self, agent):
        """Truncated right before the final }}"""
        full = json.dumps(
            {
                "decision_type": "skip",
                "reasoning": "No workloads",
                "actions": [],
            }
        )
        # Remove last }
        truncated = full[:-1]
        result = agent._parse_llm_response(truncated, truncated=True)
        assert result["decision_type"] == "skip"

    def test_repair_not_attempted_when_not_truncated(self, agent):
        """Garbage input without truncated=True should return skip, not attempt repair."""
        result = agent._parse_llm_response("this is not json at all", truncated=False)
        assert result["decision_type"] == "skip"
        assert "Could not parse" in result["reasoning"]

    def test_repair_with_trailing_comma(self, agent):
        """Truncated right after a comma in the actions array."""
        payload = {
            "decision_type": "migrate",
            "reasoning": "go",
            "actions": [
                {
                    "workload_name": "wl-1",
                    "namespace": "prod",
                    "workload_type": "Deployment",
                    "source_node_name": "s",
                    "destination_node_name": "d",
                    "reason": "r",
                },
            ],
        }
        full = json.dumps(payload)
        # Inject a trailing comma after the action, then remove closing ]}
        # Simulates LLM writing: ...,"reason":"r"},  (then cut)
        truncated = full.replace("}]}", "},]}")  # add trailing comma in array
        truncated = truncated[:-2]  # remove ]}
        result = agent._parse_llm_response(truncated, truncated=True)
        assert result["decision_type"] == "migrate"


# ── _repair_truncated_json static method ─────────────────────────────


class TestRepairTruncatedJSON:
    def test_missing_closing_brace(self):
        fragment = '{"decision_type": "skip", "reasoning": "x", "actions": []'
        result = GreenWorkloadAgent._repair_truncated_json(fragment)
        assert result is not None
        assert result["decision_type"] == "skip"

    def test_missing_closing_bracket_and_brace(self):
        fragment = '{"decision_type": "migrate", "reasoning": "go", "actions": [{"workload_name": "wl"}'
        result = GreenWorkloadAgent._repair_truncated_json(fragment)
        assert result is not None
        assert result["decision_type"] == "migrate"
        assert len(result["actions"]) == 1

    def test_deeply_truncated_returns_none(self):
        """Completely mangled fragment should return None."""
        fragment = '{"decision_ty'
        result = GreenWorkloadAgent._repair_truncated_json(fragment)
        assert result is None

    def test_valid_json_passes_through(self):
        fragment = json.dumps(
            {"decision_type": "wait", "reasoning": "busy", "actions": []}
        )
        result = GreenWorkloadAgent._repair_truncated_json(fragment)
        assert result is not None
        assert result["decision_type"] == "wait"


# ── Unparseable / garbage input ──────────────────────────────────────


class TestUnparseable:
    def test_empty_string(self, agent):
        result = agent._parse_llm_response("")
        assert result["decision_type"] == "skip"
        assert result["actions"] == []

    def test_plain_text(self, agent):
        result = agent._parse_llm_response("I cannot help with that request.")
        assert result["decision_type"] == "skip"

    def test_partial_json_no_truncated_flag(self, agent):
        result = agent._parse_llm_response(
            '{"decision_type": "migrate", "actions": [', truncated=False
        )
        assert result["decision_type"] == "skip"

    def test_xml_response(self, agent):
        result = agent._parse_llm_response("<response><type>skip</type></response>")
        assert result["decision_type"] == "skip"

    def test_only_whitespace(self, agent):
        result = agent._parse_llm_response("   \n\t  ")
        assert result["decision_type"] == "skip"
