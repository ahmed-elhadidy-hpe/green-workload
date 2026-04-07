import json
import asyncio
import random
from datetime import datetime, timezone
from typing import Optional

import structlog
from openai import AsyncOpenAI

from config.settings import settings
from src.database.repository import GreenWorkloadRepository
from src.agent.prompts import SYSTEM_PROMPT, build_user_prompt
from src.agent.safety import SafetyValidator

log = structlog.get_logger()


class GreenWorkloadAgent:
    """
    Autonomous agent that evaluates zone energy data, queries cluster topology,
    calls the LLM for migration decisions, validates safety, and executes actions.
    """

    def __init__(self):
        self.repo = GreenWorkloadRepository()
        self.safety = SafetyValidator(self.repo, settings)
        self.llm = AsyncOpenAI(
            base_url=settings.OLLAMA_BASE_URL,
            api_key="ollama",  # Ollama ignores the key but the SDK requires it
        )

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

            # 1. Collect context
            log.info("Collecting context — energy status, topology, workloads, history")
            energy_status = self._get_energy_status()
            topology = self.repo.get_cluster_topology()
            workloads = self.repo.get_migratable_workloads()
            history = self.repo.get_migration_history(hours_back=2)

            log.info(
                "Context collected",
                zones=energy_status.get("count", 0),
                clusters=len(topology.get("clusters", [])),
                migratable_workloads=len(workloads),
                recent_migrations=len(history),
            )
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

            # 2. Call LLM
            decision = await self._call_llm(energy_status, topology, workloads, history)
            
            # 3. Record decision
            decision_id = self.repo.record_ai_decision(
                agent_run_id=run_id,
                decision_type=decision.get("decision_type", "skip"),
                reasoning=decision.get("reasoning", ""),
                recommended_actions=decision.get("actions", []),
                safety_check_passed=True,
                model_name=settings.OLLAMA_MODEL,
            )

            # 4. Validate and execute actions
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
            model=settings.OLLAMA_MODEL,
            base_url=settings.OLLAMA_BASE_URL,
            system_prompt_length=len(SYSTEM_PROMPT),
            user_prompt_length=len(user_prompt),
            workloads_count=len(workloads),
        )
        log.debug("LLM system prompt", prompt=SYSTEM_PROMPT)
        log.debug("LLM user prompt", prompt=user_prompt)

        try:
            response = await self.llm.chat.completions.create(
                model=settings.OLLAMA_MODEL,
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
            decision = self._parse_llm_response(raw, truncated=(finish_reason == "length"))

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
        # Try closing with increasingly aggressive truncation
        for trim_chars in [0, 1, 5, 50, 200, 500, 1000]:
            candidate = fragment if trim_chars == 0 else fragment[:-trim_chars]
            # Count open/close brackets
            open_braces = candidate.count("{") - candidate.count("}")
            open_brackets = candidate.count("[") - candidate.count("]")
            # Remove any trailing comma after trimming
            candidate = candidate.rstrip().rstrip(",")
            # Close the open brackets
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
                    "workload_id": wl.get("workload_id", ""),
                    "namespace": wl.get("namespace", "default"),
                    "cluster_id": wl.get("cluster_id", ""),
                    "workload_type": wl.get("workload_type", "Deployment"),
                    "source_node_id": wl.get("node_id", ""),
                    "source_node_name": wl.get("node_name", ""),
                    "destination_node_id": best_node.get(
                        "node_id", best_node.get("id", "")
                    ),
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
            valid, reason = self.safety.validate_action(action, topology)
            if not valid:
                log.warning(
                    "Safety check failed, skipping action", reason=reason, action=action
                )
                continue

            workload_id = action.get("workload_id", "")
            source_node_id = action.get("source_node_id", "")
            dest_node_id = action.get("destination_node_id", "")

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
        """
        Execute the actual K8s migration.

        TODO: Call the Kubernetes MCP server to cordon, drain, and
              reschedule the workload on the destination node.

        Currently simulates the operation with a random delay (5-60s)
        to mimic real-world migration latency.
        """
        workload_name = action.get("workload_name", "unknown")
        dest_node = action.get("destination_node_name", "unknown")

        if settings.DRY_RUN:
            log.info(
                "DRY_RUN: would execute migration",
                workload=workload_name,
                dest=dest_node,
            )
            return True

        log.info(
            "Migration started — executing K8s migration",
            workload=workload_name,
            namespace=action.get("namespace"),
            destination=dest_node,
            workload_type=action.get("workload_type"),
        )

        # Simulate K8s migration latency (5–60 seconds)
        from_sec, to_sec = settings.SIMULATED_MIGRATION_EXEC_TIME_BETWEEN_SEC
        delay = random.uniform(from_sec, to_sec)
        await asyncio.sleep(delay)

        # TODO: Replace the sleep above with actual K8s MCP call, e.g.:
        #   result = await self.k8s_mcp.migrate_workload(
        #       workload_name=workload_name,
        #       namespace=action.get("namespace"),
        #       destination_node=dest_node,
        #       workload_type=action.get("workload_type"),
        #   )
        #   return result.success

        log.info(
            "Migration K8s operation finished",
            workload=workload_name,
            destination=dest_node,
            simulated_delay_s=round(delay, 1),
        )
        return True
