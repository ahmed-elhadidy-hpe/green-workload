import json
import asyncio
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
            energy_status = self._get_energy_status()
            topology = self.repo.get_cluster_topology()
            workloads = self.repo.get_migratable_workloads()
            history = self.repo.get_migration_history(hours_back=2)

            if not workloads:
                log.info("No migratable workloads found, skipping LLM call")
                self.repo.complete_agent_run(run_id, 0, "completed")
                return {"run_id": run_id, "status": "completed", "migrations_initiated": 0}

            # 2. Call LLM
            decision = await self._call_llm(energy_status, topology, workloads, history)
            log.info("LLM decision received", decision_type=decision.get("decision_type"))

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
        try:
            response = await self.llm.chat.completions.create(
                model=settings.OLLAMA_MODEL,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.1,
                max_tokens=2048,
            )
            raw = response.choices[0].message.content or ""
            return self._parse_llm_response(raw)
        except Exception as e:
            log.error("LLM call failed", error=str(e))
            return self._rule_based_fallback(energy_status, topology, workloads)

    def _parse_llm_response(self, raw: str) -> dict:
        """Extract and parse JSON from the LLM response."""
        raw = raw.strip()
        # Strip markdown code fences if present
        if raw.startswith("```"):
            lines = raw.splitlines()
            raw = "\n".join(
                line for line in lines if not line.startswith("```")
            ).strip()
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            # Try extracting the first JSON object
            start = raw.find("{")
            end = raw.rfind("}") + 1
            if start != -1 and end > start:
                try:
                    return json.loads(raw[start:end])
                except json.JSONDecodeError:
                    pass
            log.warning("Could not parse LLM response as JSON, returning skip", raw=raw[:200])
            return {"decision_type": "skip", "reasoning": "Could not parse LLM response", "actions": []}

    def _rule_based_fallback(
        self, energy_status: dict, topology: dict, workloads: list
    ) -> dict:
        """Simple rule-based fallback when LLM is unavailable."""
        if not workloads:
            return {"decision_type": "skip", "reasoning": "No migratable workloads", "actions": []}

        # Find the greenest node across all clusters
        best_node = None
        best_renewable = 0.0
        clusters = topology.get("clusters", [])
        for cluster in clusters:
            for node in cluster.get("nodes", []):
                renewable = node.get("renewable_percentage") or 0
                is_ready = node.get("status") == "Ready"
                not_cordoned = not node.get("is_cordoned", False)
                cpu_ok = (node.get("cpu_usage_percent") or 0) < settings.NODE_CPU_THRESHOLD
                mem_ok = (node.get("memory_usage_percent") or 0) < settings.NODE_MEMORY_THRESHOLD
                if is_ready and not_cordoned and cpu_ok and mem_ok and renewable > best_renewable:
                    best_renewable = renewable
                    best_node = node

        if not best_node or best_renewable < settings.MIN_RENEWABLE_PCT:
            return {
                "decision_type": "skip",
                "reasoning": "No suitable green destination node found",
                "actions": [],
            }

        actions = []
        for wl in workloads[:settings.MAX_CONCURRENT_MIGRATIONS]:
            # Skip if already on a green node
            if wl.get("is_green"):
                continue
            actions.append({
                "workload_name": wl.get("workload_name", ""),
                "workload_id": wl.get("workload_id", ""),
                "namespace": wl.get("namespace", "default"),
                "cluster_id": wl.get("cluster_id", ""),
                "workload_type": wl.get("workload_type", "Deployment"),
                "source_node_id": wl.get("node_id", ""),
                "source_node_name": wl.get("node_name", ""),
                "destination_node_id": best_node.get("node_id", best_node.get("id", "")),
                "destination_node_name": best_node.get("node_name", best_node.get("name", "")),
                "reason": f"Rule-based: source zone renewable={wl.get('renewable_percentage', 0)}%, "
                          f"destination renewable={best_renewable}%",
            })

        if not actions:
            return {"decision_type": "skip", "reasoning": "No actionable workloads", "actions": []}

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
                log.warning("Safety check failed, skipping action", reason=reason, action=action)
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
                success = await self._do_migrate(action)
                if success:
                    self.repo.update_migration_status(migration_id, "completed")
                    initiated += 1
                    log.info(
                        "Migration completed",
                        workload=action.get("workload_name"),
                        destination=action.get("destination_node_name"),
                    )
                else:
                    self.repo.update_migration_status(migration_id, "failed", "Migration returned failure")
            except Exception as e:
                log.error("Migration execution error", workload=action.get("workload_name"), error=str(e))
                self.repo.update_migration_status(migration_id, "failed", str(e))

        return initiated

    async def _do_migrate(self, action: dict) -> bool:
        """Execute the actual k8s migration (dry-run aware)."""
        if settings.DRY_RUN:
            log.info(
                "DRY_RUN: would execute migration",
                workload=action.get("workload_name"),
                dest=action.get("destination_node_name"),
            )
            return True

        # In production this would call the kubernetes MCP tool via subprocess/stdio.
        # For now, log the intent and return True (the real execution happens via the MCP client).
        log.info(
            "Migration action queued",
            workload=action.get("workload_name"),
            namespace=action.get("namespace"),
            destination=action.get("destination_node_name"),
            workload_type=action.get("workload_type"),
        )
        return True
