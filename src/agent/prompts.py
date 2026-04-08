import json

SYSTEM_PROMPT = """You are an autonomous green workload migration agent for Kubernetes clusters.

Your goal is to migrate workloads from nodes running on high-carbon energy zones to nodes on green/renewable energy zones, reducing the carbon footprint of the infrastructure.

## Your responsibilities:
1. Analyze current energy data for all zones
2. Identify workloads on high-carbon nodes that should be migrated
3. Select optimal destination nodes in green energy zones
4. Ensure migrations are safe (capacity, PDB compliance, StatefulSet opt-in)
5. Avoid cascading failures by limiting concurrent migrations

## Hard rules (never violate):
- **NEVER migrate StatefulSets** unless their annotations include "green-workload/migration-allowed: true". If a StatefulSet does NOT have this exact annotation, do NOT include it in actions. Skip it entirely.
- Never exceed MAX_CONCURRENT_MIGRATIONS in-flight migrations
- Never migrate to a node that is NotReady or cordoned
- Never migrate if destination node CPU > 80% or memory > 80%
- Skip migration if source and destination zones have similar carbon intensity (< 20% difference)
- Never migrate DaemonSets — they run on every node by design

## Decision types:
- "migrate": When there are eligible Deployment workloads on high-carbon zones AND green destination nodes have capacity (CPU < 80%, Memory < 80%)
- "skip": When no migration is needed (carbon gap < 20%, or all workloads already on green zones, or no eligible workloads)
- "wait": When migration would be beneficial but conditions aren't right yet (e.g., all green destination nodes are at capacity, or too many concurrent migrations are in-flight)

## Output format (respond ONLY with valid JSON, no markdown):
IMPORTANT: Use ONLY human-readable NAMES — never include any UUIDs or IDs.
{
  "decision_type": "migrate" | "skip" | "wait",
  "reasoning": "Brief explanation of the decision",
  "actions": [
    {
      "workload_name": "string",
      "namespace": "string",
      "workload_type": "Deployment",
      "source_node_name": "string",
      "destination_node_name": "string",
      "reason": "Why this specific migration"
    }
  ]
}

If decision_type is "skip" or "wait", actions should be an empty array.
Always output valid JSON. Do not include any text outside the JSON object.
"""


def _strip_ids(obj):
    """Recursively remove keys ending in '_id' or equal to 'id' from dicts/lists,
    so the LLM only sees human-readable names."""
    if isinstance(obj, dict):
        return {
            k: _strip_ids(v) for k, v in obj.items()
            if k != "id" and not k.endswith("_id")
        }
    if isinstance(obj, list):
        return [_strip_ids(item) for item in obj]
    return obj


def build_user_prompt(
    energy_status: dict,
    topology: dict,
    workloads: list,
    history: list,
    timestamp: str,
) -> str:
    """Build the user prompt for the LLM evaluation cycle."""

    # Annotate workloads with migration eligibility notes
    annotated = []
    for w in workloads:
        wl = dict(w)  # shallow copy
        wtype = wl.get("workload_type", "Deployment")
        annotations = wl.get("annotations") or {}
        if isinstance(annotations, str):
            import json as _json
            try:
                annotations = _json.loads(annotations)
            except Exception:
                annotations = {}
        if wtype.lower() == "statefulset":
            has_opt_in = annotations.get("green-workload/migration-allowed") == "true"
            wl["_migration_note"] = (
                "ELIGIBLE — has opt-in annotation" if has_opt_in
                else "BLOCKED — StatefulSet without required annotation 'green-workload/migration-allowed: true'. DO NOT migrate."
            )
        elif wtype.lower() == "daemonset":
            wl["_migration_note"] = "BLOCKED — DaemonSets cannot be migrated."
        annotated.append(wl)

    # Strip all IDs so the LLM only works with human-readable names
    clean_energy = _strip_ids(energy_status)
    clean_topology = _strip_ids(topology)
    clean_workloads = _strip_ids(annotated)
    clean_history = _strip_ids(history)

    # Build an explicit allowlist of workload names for the prompt
    eligible_names = [w.get("workload_name", w.get("name", "")) for w in annotated
                      if w.get("_migration_note", "").startswith("ELIGIBLE") or "_migration_note" not in w]
    eligible_list = ", ".join(f'"{n}"' for n in eligible_names) if eligible_names else "(none)"

    return f"""Current evaluation timestamp: {timestamp}

## Energy Status
{json.dumps(clean_energy, indent=2, default=str)}

## Cluster Topology (nodes, zones, current metrics)
{json.dumps(clean_topology, indent=2, default=str)}

## Migratable Workloads (on non-green zones, migration_allowed=True)
**CRITICAL**: Your actions list MUST ONLY contain workloads from this section.
Do NOT include workloads you see in the topology or history — only the ones listed below are eligible for migration.
Eligible workload names: {eligible_list}
Skip any workload marked BLOCKED.
{json.dumps(clean_workloads, indent=2, default=str)}

## Recent Migration History (last 2 hours)
The history below is for context only — do NOT re-migrate workloads from history.
{json.dumps(clean_history, indent=2, default=str)}

Based on the above data, decide what migrations to perform. Remember:
- **ONLY include workloads listed in the "Migratable Workloads" section above** — never invent or add workloads from topology or history
- Only migrate workloads from HIGH-CARBON zones to GREEN zones (>= 50% renewable)
- DO NOT migrate any workload with _migration_note containing "BLOCKED"
- Prioritize workloads on the highest-carbon zones first
- Check that destination nodes have capacity (CPU < 80%, memory < 80%)
- Limit to {5} concurrent migrations
- Skip if already migrated recently (within 1 hour)
- If carbon intensity difference between source and destination is < 20%, use decision_type "skip"
- If workloads need migration but no green node has capacity, use decision_type "wait"
- Use ONLY names in your response — do NOT include any UUIDs or IDs

Respond with valid JSON only.
"""
