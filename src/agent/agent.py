import json
import asyncio
import contextlib
import os
import random
from datetime import datetime, timezone
from typing import Optional

import structlog
from openai import AsyncOpenAI
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from config.settings import settings
from src.database.repository import GreenWorkloadRepository
from src.agent.prompts import SYSTEM_PROMPT, AGENTIC_SYSTEM_PROMPT, build_user_prompt
from src.agent.safety import SafetyValidator

log = structlog.get_logger()

# Read-only MCP tools exposed to the LLM for data gathering.
# Execution/write tools are called directly by the agent code, not the LLM.
_LLM_TOOL_ALLOWLIST = {
    "get_zone_energy_status",
    "get_all_zones_energy_status",
    "get_greenest_zones",
    "get_zone_energy_forecast",
    "get_cluster_topology",
    "get_migratable_workloads",
    "get_migration_history",
    "get_all_zones_with_energy",
    "list_nodes",
    "get_node_metrics",
}


class GreenWorkloadAgent:
    """
    Autonomous agent that evaluates zone energy data, queries cluster topology,
    calls the LLM for migration decisions, validates safety, and executes actions.
    """

    def __init__(self):
        self.repo = GreenWorkloadRepository()
        self.safety = SafetyValidator(self.repo, settings)
        if settings.LLM_PROVIDER == "ollama":
            self.llm = AsyncOpenAI(
                base_url=settings.OLLAMA_BASE_URL,
                api_key="ollama",  # Ollama ignores the key but the SDK requires it
            )
            self.model = settings.OLLAMA_MODEL
            self.url = settings.OLLAMA_BASE_URL
        elif settings.LLM_PROVIDER == "copilot":
            self.llm = AsyncOpenAI(
                base_url=settings.COPILOT_BASE_URL,
                api_key="dummy",  # Copilot ignores the key but the SDK requires it
            )
            self.model = settings.COPILOT_MODEL
            self.url = settings.COPILOT_BASE_URL

        # MCP state — populated by _connect_mcp_servers during each run cycle
        self._mcp_sessions: dict[str, ClientSession] = {}
        self._llm_tools: list[dict] = []       # read-only tools exposed to the LLM
        self._all_tools: list[dict] = []        # all registered MCP tools
        self._tool_server_map: dict[str, str] = {}  # tool_name -> server_name

    # ------------------------------------------------------------------
    # MCP connection management
    # ------------------------------------------------------------------

    async def _connect_mcp_servers(self, exit_stack: contextlib.AsyncExitStack) -> None:
        """Spawn MCP server subprocesses and establish client sessions.

        All sessions are registered with *exit_stack* so they are cleaned up
        automatically when the caller's ``async with`` block exits.
        """
        server_configs = {
            "green_energy": (settings.GREEN_ENERGY_MCP_CMD, settings.GREEN_ENERGY_MCP_ARGS),
            "internal_db":  (settings.DB_MCP_CMD,            settings.DB_MCP_ARGS),
            "kubernetes":   (settings.K8S_MCP_CMD,            settings.K8S_MCP_ARGS),
        }

        self._mcp_sessions = {}
        self._llm_tools = []
        self._all_tools = []
        self._tool_server_map = {}

        for server_name, (cmd, args_str) in server_configs.items():
            try:
                params = StdioServerParameters(
                    command=cmd,
                    args=args_str.split(),
                    env={**os.environ},  # pass full parent env so servers can reach the DB
                )
                read, write = await exit_stack.enter_async_context(stdio_client(params))
                session = await exit_stack.enter_async_context(ClientSession(read, write))
                await session.initialize()
                self._mcp_sessions[server_name] = session

                tools_response = await session.list_tools()
                for tool in tools_response.tools:
                    self._tool_server_map[tool.name] = server_name
                    tool_def = {
                        "type": "function",
                        "function": {
                            "name": tool.name,
                            "description": tool.description or "",
                            "parameters": tool.inputSchema,
                        },
                    }
                    self._all_tools.append(tool_def)
                    if tool.name in _LLM_TOOL_ALLOWLIST:
                        self._llm_tools.append(tool_def)

                log.info(
                    "MCP server connected",
                    server=server_name,
                    tools=[t.name for t in tools_response.tools],
                )
            except Exception as e:
                log.warning(
                    "Failed to connect MCP server — will use fallback",
                    server=server_name,
                    error=str(e),
                )

        log.info(
            "MCP setup complete",
            servers_connected=len(self._mcp_sessions),
            llm_tools=len(self._llm_tools),
            total_tools=len(self._all_tools),
        )

    async def _call_mcp_tool(self, tool_name: str, arguments: dict) -> str:
        """Execute a tool via its MCP server and return the result as a JSON string."""
        server_name = self._tool_server_map.get(tool_name)
        if not server_name:
            return json.dumps({"error": f"Unknown MCP tool: {tool_name}"})

        session = self._mcp_sessions.get(server_name)
        if not session:
            return json.dumps({"error": f"MCP server '{server_name}' not connected"})

        try:
            result = await session.call_tool(tool_name, arguments)
            if result.content:
                parts = [
                    c.text if hasattr(c, "text") else str(c)
                    for c in result.content
                ]
                return parts[0] if len(parts) == 1 else json.dumps(parts)
            return json.dumps({"result": "success"})
        except Exception as e:
            log.error("MCP tool call failed", tool=tool_name, error=str(e))
            return json.dumps({"error": str(e)})

    # ------------------------------------------------------------------
    # Main evaluation cycle
    # ------------------------------------------------------------------

    async def run_cycle(self) -> dict:
        """Run a single evaluation cycle. Returns a summary dict."""
        run_id = self.repo.create_agent_run()
        migrations_initiated = 0
        status = "completed"

        try:
            log.info("Agent cycle started", run_id=run_id)

            async with contextlib.AsyncExitStack() as mcp_stack:
                # Connect to all MCP servers; sessions stay open for the full cycle
                await self._connect_mcp_servers(mcp_stack)

                # 1. Quick workload check via DB (avoids a full LLM call when idle)
                workloads = self.repo.get_migratable_workloads()

                for wl in workloads:
                    log.info(
                        "  Migratable workload",
                        name=wl.get("workload_name"),
                        node=wl.get("node_name"),
                        zone=wl.get("zone_name"),
                        renewable_pct=wl.get("renewable_percentage"),
                        carbon_intensity=wl.get("carbon_intensity"),
                    )

                if not workloads:
                    log.info("No migratable workloads found, skipping LLM call")
                    self.repo.complete_agent_run(run_id, 0, "completed")
                    return {
                        "run_id": run_id,
                        "status": "completed",
                        "migrations_initiated": 0,
                    }

                log.info("Migratable workloads found", count=len(workloads))

                # 2. LLM agentic loop — the LLM calls MCP tools to gather context,
                #    then outputs a JSON migration decision.
                decision = await self._call_llm_with_tools(workloads)

                # 2b. Filter out hallucinated workloads not in the migratable list
                allowed_names = {wl.get("workload_name") for wl in workloads}
                raw_actions = decision.get("actions", [])
                if raw_actions:
                    filtered = [
                        a for a in raw_actions if a.get("workload_name") in allowed_names
                    ]
                    hallucinated = [
                        a.get("workload_name")
                        for a in raw_actions
                        if a.get("workload_name") not in allowed_names
                    ]
                    if hallucinated:
                        log.warning(
                            "Dropped hallucinated workloads from LLM response",
                            hallucinated=hallucinated,
                            kept=len(filtered),
                            dropped=len(hallucinated),
                        )
                    decision["actions"] = filtered

                # 3. Record decision
                topology = self.repo.get_cluster_topology()
                decision_id = self.repo.record_ai_decision(
                    agent_run_id=run_id,
                    decision_type=decision.get("decision_type", "skip"),
                    reasoning=decision.get("reasoning", ""),
                    recommended_actions=decision.get("actions", []),
                    safety_check_passed=True,
                    model_name=self.model,
                )

                # 4. Validate and execute actions (MCP sessions still open)
                if decision.get("decision_type") == "migrate":
                    log.info(
                        "Executing migrations",
                        action_count=len(decision.get("actions", [])),
                    )
                    migrations_initiated = await self._execute_actions(
                        decision.get("actions", []), topology, decision_id
                    )

        except Exception as e:
            log.error("Agent cycle failed", run_id=run_id, error=str(e))
            status = "failed"
            self.repo.complete_agent_run(run_id, migrations_initiated, status)
            raise

        self.repo.complete_agent_run(run_id, migrations_initiated, status)
        log.info(
            "Agent cycle complete",
            run_id=run_id,
            migrations_initiated=migrations_initiated,
        )
        return {
            "run_id": run_id,
            "status": status,
            "migrations_initiated": migrations_initiated,
        }

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _get_energy_status(self) -> dict:
        """Pull latest energy data from DB."""
        try:
            zones = self.repo.get_all_zones_with_energy()
            return {"zones": zones, "count": len(zones)}
        except Exception as e:
            log.warning("Could not fetch energy status", error=str(e))
            return {"zones": [], "count": 0}

    async def _call_llm_with_tools(self, workloads: list) -> dict:
        """Agentic tool-calling loop: the LLM calls read-only MCP tools to gather context,
        then outputs a JSON migration decision.

        Falls back to ``_call_llm`` (prompt-based) when no MCP tools are available.
        """
        if not self._llm_tools:
            log.warning("No MCP tools available for LLM — falling back to prompt-based call")
            energy_status = self._get_energy_status()
            topology = self.repo.get_cluster_topology()
            history = self.repo.get_migration_history(hours_back=2)
            return await self._call_llm(energy_status, topology, workloads, history)

        messages: list[dict] = [
            {"role": "system", "content": AGENTIC_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"Current time: {datetime.now(timezone.utc).isoformat()}\n\n"
                    "Please evaluate the current state of all energy zones and workloads, "
                    "then decide which migrations (if any) should be performed."
                ),
            },
        ]

        log.info(
            "LLM agentic loop started",
            model=self.model,
            base_url=self.url,
            available_tools=[t["function"]["name"] for t in self._llm_tools],
        )

        max_iterations = 15
        for iteration in range(max_iterations):
            log.info("LLM tool-calling iteration", iteration=iteration + 1)

            try:
                response = await self.llm.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    tools=self._llm_tools,
                    tool_choice="auto",
                    temperature=0.1,
                    max_tokens=4096,
                )
            except Exception as e:
                log.error("LLM call failed in agentic loop — falling back to rule-based", error=str(e))
                energy_status = self._get_energy_status()
                topology = self.repo.get_cluster_topology()
                return self._rule_based_fallback(energy_status, topology, workloads)

            msg = response.choices[0].message
            usage = response.usage

            log.debug(
                "LLM agentic response",
                iteration=iteration + 1,
                finish_reason=response.choices[0].finish_reason,
                tool_calls=len(msg.tool_calls) if msg.tool_calls else 0,
                prompt_tokens=usage.prompt_tokens if usage else None,
                completion_tokens=usage.completion_tokens if usage else None,
            )

            # Append assistant message (serialise tool_calls for the message history)
            assistant_msg: dict = {"role": "assistant", "content": msg.content}
            if msg.tool_calls:
                assistant_msg["tool_calls"] = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }
                    for tc in msg.tool_calls
                ]
            messages.append(assistant_msg)

            if not msg.tool_calls:
                # LLM finished — parse the JSON decision from the final message
                log.info("LLM agentic loop complete", total_iterations=iteration + 1)
                log.info("LLM raw response", raw_response=msg.content)
                return self._parse_llm_response(msg.content or "")

            # Execute each tool call via the appropriate MCP server
            for tc in msg.tool_calls:
                tool_name = tc.function.name
                try:
                    arguments = json.loads(tc.function.arguments)
                except json.JSONDecodeError:
                    arguments = {}

                log.info("LLM calling MCP tool", tool=tool_name, args=arguments)
                tool_result = await self._call_mcp_tool(tool_name, arguments)
                log.debug("MCP tool result", tool=tool_name, result=tool_result[:300])

                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": tool_result,
                })

        log.warning("LLM agentic loop hit max iterations — falling back to manual context")
        energy_status = self._get_energy_status()
        topology = self.repo.get_cluster_topology()
        history = self.repo.get_migration_history(hours_back=2)
        return await self._call_llm(energy_status, topology, workloads, history)

    async def _call_llm(
        self,
        energy_status: dict,
        topology: dict,
        workloads: list,
        history: list,
    ) -> dict:
        """Call the LLM and parse the JSON response."""
        user_prompt = build_user_prompt(
            energy_status=energy_status,
            topology=topology,
            workloads=workloads,
            history=history,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

        log.info(
            "LLM request — sending prompt to model",
            model=self.model,
            base_url=self.url,
            system_prompt_length=len(SYSTEM_PROMPT),
            user_prompt_length=len(user_prompt),
            workloads_count=len(workloads),
        )
        log.debug("LLM system prompt", prompt=SYSTEM_PROMPT)
        log.debug("LLM user prompt", prompt=user_prompt)

        try:
            response = await self.llm.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.1,
                max_tokens=4096,
            )
            raw = response.choices[0].message.content or ""

            # Log full LLM response
            usage = response.usage
            log.debug(
                "LLM response received",
                model=response.model,
                finish_reason=response.choices[0].finish_reason,
                response_length=len(raw),
                prompt_tokens=usage.prompt_tokens if usage else None,
                completion_tokens=usage.completion_tokens if usage else None,
                total_tokens=usage.total_tokens if usage else None,
            )
            log.info("LLM raw response", raw_response=raw)

            finish_reason = response.choices[0].finish_reason
            decision = self._parse_llm_response(
                raw, truncated=(finish_reason == "length")
            )

            log.info(
                "LLM decision parsed",
                decision_type=decision.get("decision_type"),
                reasoning=decision.get("reasoning"),
                action_count=len(decision.get("actions", [])),
            )
            for i, act in enumerate(decision.get("actions", [])):
                log.info(
                    f"LLM action [{i+1}]",
                    workload=act.get("workload_name"),
                    source_node=act.get("source_node_name"),
                    destination_node=act.get("destination_node_name"),
                    reason=act.get("reason"),
                )

            return decision
        except Exception as e:
            log.error(
                "LLM call failed — falling back to rule-based engine", error=str(e)
            )
            decision = self._rule_based_fallback(energy_status, topology, workloads)
            log.info(
                "Rule-based fallback decision",
                decision_type=decision.get("decision_type"),
                reasoning=decision.get("reasoning"),
                action_count=len(decision.get("actions", [])),
            )
            return decision

    def _parse_llm_response(self, raw: str, truncated: bool = False) -> dict:
        """Extract and parse JSON from the LLM response.

        If truncated=True (finish_reason was 'length'), attempt to repair
        the JSON by closing open arrays/objects.
        """
        raw = raw.strip()
        # Strip markdown code fences if present
        if raw.startswith("```"):
            lines = raw.splitlines()
            raw = "\n".join(
                line for line in lines if not line.startswith("```")
            ).strip()
        try:
            parsed = json.loads(raw)
            log.debug("LLM response parsed as direct JSON")
            return parsed
        except json.JSONDecodeError:
            # Try extracting the first JSON object
            start = raw.find("{")
            end = raw.rfind("}") + 1
            if start != -1 and end > start:
                try:
                    parsed = json.loads(raw[start:end])
                    log.info(
                        "LLM response parsed via JSON extraction",
                        extracted_from=f"chars {start}–{end}",
                    )
                    return parsed
                except json.JSONDecodeError:
                    pass

            # Try to find multiple JSON objects via brace-counting
            json_candidates = []
            depth = 0
            obj_start = None
            in_string = False
            escape_next = False
            for i, ch in enumerate(raw):
                if escape_next:
                    escape_next = False
                    continue
                if ch == "\\" and in_string:
                    escape_next = True
                    continue
                if ch == '"' and not escape_next:
                    in_string = not in_string
                    continue
                if in_string:
                    continue
                if ch == "{":
                    if depth == 0:
                        obj_start = i
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0 and obj_start is not None:
                        json_candidates.append(raw[obj_start : i + 1])
                        obj_start = None

            # reverse candidates to prioritize the last complete JSON object
            # as LLMs append its fixes to the end of the response
            for i, candidate in enumerate(reversed(json_candidates)):
                try:
                    parsed = json.loads(candidate)
                    if isinstance(parsed, dict) and "decision_type" in parsed:
                        log.info(
                            "LLM response parsed via brace-balanced extraction",
                            extracted_from=f"candidate #{i+1} of {len(json_candidates)}",
                        )
                        return parsed
                except json.JSONDecodeError:
                    continue

            # If response was truncated, try to repair by closing brackets
            if truncated and start != -1:
                fragment = raw[start:]
                repaired = self._repair_truncated_json(fragment)
                if repaired:
                    log.info(
                        "Repaired truncated LLM JSON response",
                        original_len=len(fragment),
                        actions_recovered=len(repaired.get("actions", [])),
                    )
                    return repaired

            log.warning(
                "Could not parse LLM response as JSON, returning skip",
                raw_response=raw[:500],
            )
            return {
                "decision_type": "skip",
                "reasoning": "Could not parse LLM response",
                "actions": [],
            }

    @staticmethod
    def _repair_truncated_json(fragment: str) -> dict | None:
        """Try to repair a truncated JSON response by progressively
        removing trailing incomplete elements and closing brackets."""
        # Strategy 1: try trimming back to the last complete }, or ] boundary
        for boundary in ["},", "}", "],", "]", '"']:
            idx = fragment.rfind(boundary)
            if idx > 0:
                candidate = fragment[: idx + len(boundary)]
                open_braces = candidate.count("{") - candidate.count("}")
                open_brackets = candidate.count("[") - candidate.count("]")
                candidate = candidate.rstrip().rstrip(",")
                candidate += "]" * open_brackets + "}" * open_braces
                try:
                    parsed = json.loads(candidate)
                    if isinstance(parsed, dict) and "decision_type" in parsed:
                        return parsed
                except json.JSONDecodeError:
                    pass

        # Strategy 2: brute-force trim with increasing chunk sizes
        for trim_chars in [0, 1, 2, 5, 10, 20, 50, 100, 200, 500, 1000]:
            candidate = fragment if trim_chars == 0 else fragment[:-trim_chars]
            if not candidate:
                continue
            open_braces = candidate.count("{") - candidate.count("}")
            open_brackets = candidate.count("[") - candidate.count("]")
            candidate = candidate.rstrip().rstrip(",")
            candidate += "]" * open_brackets + "}" * open_braces
            try:
                parsed = json.loads(candidate)
                if isinstance(parsed, dict) and "decision_type" in parsed:
                    return parsed
            except json.JSONDecodeError:
                continue
        return None

    def _rule_based_fallback(
        self, energy_status: dict, topology: dict, workloads: list
    ) -> dict:
        """Simple rule-based fallback when LLM is unavailable."""
        log.info("Rule-based fallback — scanning for green destination nodes")

        if not workloads:
            return {
                "decision_type": "skip",
                "reasoning": "No migratable workloads",
                "actions": [],
            }

        # Find the greenest node across all clusters
        best_node = None
        best_renewable = 0.0
        clusters = topology.get("clusters", [])
        for cluster in clusters:
            for node in cluster.get("nodes", []):
                renewable = node.get("renewable_percentage") or 0
                is_ready = node.get("status") == "Ready"
                not_cordoned = not node.get("is_cordoned", False)
                cpu_ok = (
                    node.get("cpu_usage_percent") or 0
                ) < settings.NODE_CPU_THRESHOLD
                mem_ok = (
                    node.get("memory_usage_percent") or 0
                ) < settings.NODE_MEMORY_THRESHOLD
                eligible = is_ready and not_cordoned and cpu_ok and mem_ok
                log.debug(
                    "  Node candidate",
                    node=node.get("node_name", node.get("name")),
                    renewable_pct=renewable,
                    cpu_pct=node.get("cpu_usage_percent"),
                    mem_pct=node.get("memory_usage_percent"),
                    eligible=eligible,
                )
                if eligible and renewable > best_renewable:
                    best_renewable = renewable
                    best_node = node

        if not best_node or best_renewable < settings.MIN_RENEWABLE_PCT:
            log.info(
                "No suitable green destination node found",
                best_renewable=best_renewable,
                min_required=settings.MIN_RENEWABLE_PCT,
            )
            return {
                "decision_type": "skip",
                "reasoning": "No suitable green destination node found",
                "actions": [],
            }

        log.info(
            "Best green destination node selected",
            node=best_node.get("node_name", best_node.get("name")),
            renewable_pct=best_renewable,
            cpu_pct=best_node.get("cpu_usage_percent"),
            mem_pct=best_node.get("memory_usage_percent"),
        )

        actions = []
        for wl in workloads[: settings.MAX_CONCURRENT_MIGRATIONS]:
            # Skip if already on a green node
            if wl.get("is_green"):
                log.info(
                    "  Skipping workload — already on green zone",
                    workload=wl.get("workload_name"),
                )
                continue
            actions.append(
                {
                    "workload_name": wl.get("workload_name", ""),
                    "namespace": wl.get("namespace", "default"),
                    "workload_type": wl.get("workload_type", "Deployment"),
                    "source_node_name": wl.get("node_name", ""),
                    "destination_node_name": best_node.get(
                        "node_name", best_node.get("name", "")
                    ),
                    "reason": f"Rule-based: source zone renewable={wl.get('renewable_percentage', 0)}%, "
                    f"destination renewable={best_renewable}%",
                }
            )
            log.info(
                "  Migration action planned",
                workload=wl.get("workload_name"),
                source=wl.get("node_name"),
                destination=best_node.get("node_name", best_node.get("name")),
                source_renewable=wl.get("renewable_percentage"),
                dest_renewable=best_renewable,
            )

        if not actions:
            return {
                "decision_type": "skip",
                "reasoning": "No actionable workloads",
                "actions": [],
            }

        return {
            "decision_type": "migrate",
            "reasoning": f"Rule-based fallback: migrating {len(actions)} workload(s) to greener node",
            "actions": actions,
        }

    async def _execute_actions(
        self, actions: list, topology: dict, decision_id: str
    ) -> int:
        """Validate and execute each migration action. Returns count of initiated migrations."""
        initiated = 0
        for action in actions:
            # Resolve human-readable names to database IDs
            action = self.repo.resolve_action_names(action)
            log.info(
                "Action resolved",
                workload=action.get("workload_name"),
                workload_id=action.get("workload_id", "")[:12],
                source_node=action.get("source_node_name"),
                destination_node=action.get("destination_node_name"),
            )

            valid, reason = self.safety.validate_action(action, topology)
            if not valid:
                log.warning(
                    "Safety check failed, skipping action", reason=reason, action=action
                )
                continue

            workload_id = action.get("workload_id", "")
            source_node_id = action.get("source_node_id", "")
            dest_node_id = action.get("destination_node_id", "")

            if not workload_id:
                log.warning(
                    "Skipping action — workload could not be resolved",
                    workload_name=action.get("workload_name"),
                )
                continue
            if not dest_node_id:
                log.warning(
                    "Skipping action — destination node could not be resolved",
                    destination_node_name=action.get("destination_node_name"),
                )
                continue

            migration_id = self.repo.record_migration_event(
                workload_id=workload_id,
                ai_decision_id=decision_id,
                source_node_id=source_node_id or None,
                destination_node_id=dest_node_id or None,
                status="in_progress",
                trigger_reason=action.get("reason", ""),
            )

            try:
                started_at = datetime.utcnow()
                success = await self._do_migrate(action)
                duration = int((datetime.utcnow() - started_at).total_seconds())

                if success:
                    self.repo.complete_migration(
                        migration_id=migration_id,
                        workload_id=workload_id,
                        destination_node_id=dest_node_id,
                        duration_seconds=duration,
                    )
                    initiated += 1
                    log.info(
                        "Migration completed — topology updated",
                        workload=action.get("workload_name"),
                        source_node=action.get("source_node_name"),
                        destination_node=action.get("destination_node_name"),
                        duration_s=duration,
                    )
                else:
                    self.repo.update_migration_status(
                        migration_id, "failed", "Migration returned failure"
                    )
            except Exception as e:
                log.error(
                    "Migration execution error",
                    workload=action.get("workload_name"),
                    error=str(e),
                )
                self.repo.update_migration_status(migration_id, "failed", str(e))

        return initiated

    async def _do_migrate(self, action: dict) -> bool:
        """Execute the K8s migration via the Kubernetes MCP server.

        Calls ``validate_migration_feasibility`` first, then ``execute_migration``.
        Falls back to a simulated delay when the kubernetes MCP server is not connected.
        """
        workload_name = action.get("workload_name", "unknown")
        dest_node = action.get("destination_node_name", "unknown")
        namespace = action.get("namespace", "default")
        workload_type = action.get("workload_type", "Deployment")
        cluster_id = action.get("cluster_id", "")

        if settings.DRY_RUN:
            log.info("DRY_RUN: would execute migration", workload=workload_name, dest=dest_node)
            return True

        log.info(
            "Migration started — calling Kubernetes MCP",
            workload=workload_name,
            namespace=namespace,
            destination=dest_node,
            workload_type=workload_type,
        )

        # Use the K8s MCP server when connected; otherwise fall back to simulation
        if "kubernetes" in self._mcp_sessions:
            # 1. Validate feasibility
            feasibility_raw = await self._call_mcp_tool(
                "validate_migration_feasibility",
                {
                    "cluster_id": cluster_id,
                    "namespace": namespace,
                    "workload_name": workload_name,
                    "workload_type": workload_type,
                    "destination_node_name": dest_node,
                },
            )
            try:
                feasibility = json.loads(feasibility_raw)
            except json.JSONDecodeError:
                feasibility = {}

            if not feasibility.get("feasible", True):
                log.warning(
                    "K8s MCP feasibility check failed — skipping migration",
                    workload=workload_name,
                    checks=feasibility.get("checks"),
                )
                return False

            # 2. Execute via K8s MCP
            exec_raw = await self._call_mcp_tool(
                "execute_migration",
                {
                    "cluster_id": cluster_id,
                    "namespace": namespace,
                    "workload_name": workload_name,
                    "workload_type": workload_type,
                    "destination_node_name": dest_node,
                },
            )
            try:
                result = json.loads(exec_raw)
            except json.JSONDecodeError:
                result = {}

            success = result.get("success", False)
            if success:
                log.info(
                    "Migration executed via K8s MCP",
                    workload=workload_name,
                    destination=dest_node,
                    dry_run=result.get("dry_run", False),
                )
            else:
                log.error(
                    "K8s MCP migration failed",
                    workload=workload_name,
                    error=result.get("error"),
                )
            return success

        # Fallback: simulate migration latency when K8s MCP is unavailable
        log.warning(
            "Kubernetes MCP not connected — simulating migration",
            workload=workload_name,
            destination=dest_node,
        )
        from_sec, to_sec = settings.SIMULATED_MIGRATION_EXEC_TIME_BETWEEN_SEC
        delay = random.uniform(from_sec, to_sec)
        await asyncio.sleep(delay)
        log.info(
            "Simulated migration finished",
            workload=workload_name,
            destination=dest_node,
            simulated_delay_s=round(delay, 1),
        )
        return True
