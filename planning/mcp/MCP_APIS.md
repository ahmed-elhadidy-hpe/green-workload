# MCP APIs — Green Workload AI

## Overview

The system uses three MCP servers:

| MCP Server | Status | Purpose |
|------------|--------|---------|
| **Green Energy MCP** | 🔨 Custom Build | Wraps carbon intensity APIs; provides live energy source data per zone |
| **Kubernetes MCP** | ♻️ Reuse + Extend | Manages K8s workload operations; extends existing `kubernetes-mcp-server` |
| **Internal DB MCP** | 🔨 Custom Build | Exposes DB topology/metadata to AI agent; records decisions and events |

---

## 1. Green Energy MCP (Custom Build)

### Why Build Custom?
No existing general-purpose MCP wraps both Electricity Maps and WattTime for Kubernetes zone-level greenness data. Some research MCPs exist (e.g., experimental Carbon Aware SDK integrations) but none provide the structured zone-mapping needed for this system.

### Upstream APIs
- [Electricity Maps API](https://electricitymap.org/api) — free tier available (72 zones, live carbon intensity)
- [WattTime API](https://www.watttime.org/api-documentation/) — free tier available (marginal emissions data)

### Tools Exposed

#### `get_zone_energy_status`
Get the current energy source breakdown and carbon intensity for a single zone.

```json
{
  "name": "get_zone_energy_status",
  "description": "Get current energy source and carbon intensity for a zone",
  "inputSchema": {
    "zone_id": { "type": "string", "description": "DB zone UUID or electricitymap_zone code" }
  },
  "outputSchema": {
    "zone_id": "string",
    "zone_name": "string",
    "carbon_intensity": "number (gCO2eq/kWh)",
    "renewable_percentage": "number (0-100)",
    "is_green": "boolean",
    "energy_sources": { "solar": "number", "wind": "number", "hydro": "number", "gas": "number", "coal": "number" },
    "data_freshness_seconds": "number",
    "data_quality": "live | estimated | historical"
  }
}
```

#### `get_all_zones_energy_status`
Fetch current energy status for all configured zones. Used by the AI agent at the start of each evaluation cycle.

```json
{
  "name": "get_all_zones_energy_status",
  "description": "Get current energy status for all managed zones",
  "inputSchema": {},
  "outputSchema": {
    "zones": [
      {
        "zone_id": "string",
        "zone_name": "string",
        "region": "string",
        "carbon_intensity": "number",
        "renewable_percentage": "number",
        "is_green": "boolean",
        "last_updated": "ISO8601 datetime"
      }
    ],
    "stale_zones": ["zone_ids where data > 15 min old"]
  }
}
```

#### `get_greenest_zones`
Return zones ranked by renewable percentage (best first), filtered to zones with available node capacity.

```json
{
  "name": "get_greenest_zones",
  "description": "Return zones ranked by greenness (highest renewable %) with optional capacity filter",
  "inputSchema": {
    "min_renewable_pct": { "type": "number", "default": 50 },
    "cluster_id": { "type": "string", "optional": true }
  },
  "outputSchema": {
    "zones": [
      { "zone_id": "string", "zone_name": "string", "renewable_percentage": "number", "carbon_intensity": "number" }
    ]
  }
}
```

#### `get_zone_energy_forecast`
Get 1–6 hour forecast for a zone (if supported by the upstream provider).

```json
{
  "name": "get_zone_energy_forecast",
  "description": "Get short-term carbon intensity forecast for a zone",
  "inputSchema": {
    "zone_id": "string",
    "hours_ahead": { "type": "integer", "default": 2, "max": 6 }
  },
  "outputSchema": {
    "zone_id": "string",
    "forecast": [
      { "timestamp": "ISO8601", "carbon_intensity": "number", "renewable_percentage": "number" }
    ]
  }
}
```

#### `backfill_energy_history`
Admin tool to seed historical energy readings.

```json
{
  "name": "backfill_energy_history",
  "description": "Backfill historical energy readings for a zone",
  "inputSchema": {
    "zone_ids": ["string"],
    "lookback_hours": { "type": "integer", "default": 24 }
  },
  "outputSchema": { "readings_inserted": "number" }
}
```

---

## 2. Kubernetes MCP (Reuse + Extend)

### Existing MCPs to Evaluate

| Project | Repo | Notes |
|---------|------|-------|
| `kubernetes-mcp-server` | github.com/strowk/mcp-k8s-go | Go-based, good coverage of K8s resources |
| `mcp-server-kubernetes` | github.com/Flux159/mcp-server-kubernetes | TypeScript, kubectl wrapper |
| `kubectl-mcp-tool` | Experimental | Thin kubectl wrapper |

**Recommendation**: Start with `mcp-k8s-go` (most mature) and add custom tools for migration-specific operations.

### Built-in Tools to Reuse (from existing MCPs)

| Tool | Description |
|------|-------------|
| `list_nodes` | List nodes in a cluster with status and labels |
| `get_node` | Get full details of a specific node |
| `list_namespaces` | List all namespaces |
| `list_deployments` | List Deployments with replica status |
| `list_statefulsets` | List StatefulSets |
| `get_pod_logs` | Retrieve pod logs |
| `apply_manifest` | Apply a K8s YAML/JSON manifest |
| `delete_resource` | Delete a K8s resource |
| `get_events` | Get K8s events for a resource |

### Custom Tools to Build (Extensions)

#### `get_node_metrics`
Fetch live CPU/memory utilization from `kubectl top nodes`.

```json
{
  "name": "get_node_metrics",
  "description": "Get live CPU and memory utilization for all nodes in a cluster",
  "inputSchema": {
    "cluster_id": "string",
    "node_names": { "type": "array", "items": "string", "optional": true }
  },
  "outputSchema": {
    "nodes": [
      {
        "node_name": "string",
        "cpu_cores": "number",
        "cpu_percent": "number",
        "memory_gb": "number",
        "memory_percent": "number",
        "pod_count": "number"
      }
    ]
  }
}
```

#### `validate_migration_feasibility`
Pre-flight check before any migration is executed.

```json
{
  "name": "validate_migration_feasibility",
  "description": "Check if a workload can safely be migrated to a destination node",
  "inputSchema": {
    "cluster_id": "string",
    "namespace": "string",
    "workload_name": "string",
    "workload_type": "Deployment | StatefulSet",
    "destination_node_name": "string"
  },
  "outputSchema": {
    "feasible": "boolean",
    "checks": {
      "destination_node_ready": "boolean",
      "destination_node_not_cordoned": "boolean",
      "destination_node_has_capacity": "boolean",
      "pdb_allows_disruption": "boolean",
      "statefulset_opt_in": "boolean (only for StatefulSets)"
    },
    "blocking_reason": "string | null"
  }
}
```

#### `execute_migration`
Patch the workload spec to move it to a target node.

```json
{
  "name": "execute_migration",
  "description": "Migrate a workload to a specific destination node using node affinity",
  "inputSchema": {
    "cluster_id": "string",
    "namespace": "string",
    "workload_name": "string",
    "workload_type": "Deployment | StatefulSet",
    "destination_node_name": "string",
    "migration_type": "node_selector | affinity | taint_toleration"
  },
  "outputSchema": {
    "migration_id": "string",
    "status": "in_progress",
    "patch_applied": "boolean",
    "rollout_started_at": "ISO8601"
  }
}
```

#### `check_migration_status`
Poll the status of an in-progress migration.

```json
{
  "name": "check_migration_status",
  "description": "Check the current rollout status of an ongoing migration",
  "inputSchema": {
    "cluster_id": "string",
    "namespace": "string",
    "workload_name": "string",
    "workload_type": "Deployment | StatefulSet"
  },
  "outputSchema": {
    "status": "in_progress | completed | failed",
    "replicas": "number",
    "ready_replicas": "number",
    "updated_replicas": "number",
    "conditions": ["string"],
    "duration_seconds": "number"
  }
}
```

#### `rollback_migration`
Remove migration constraints and let K8s re-schedule normally.

```json
{
  "name": "rollback_migration",
  "description": "Rollback a migration by removing the node affinity constraint",
  "inputSchema": {
    "cluster_id": "string",
    "namespace": "string",
    "workload_name": "string",
    "workload_type": "Deployment | StatefulSet"
  },
  "outputSchema": {
    "rollback_status": "success | failed",
    "patch_applied": "boolean"
  }
}
```

#### `get_pod_disruption_budgets`
Fetch PDBs for a namespace to inform safety checks.

```json
{
  "name": "get_pod_disruption_budgets",
  "description": "List PodDisruptionBudgets in a namespace and their current disruption allowance",
  "inputSchema": {
    "cluster_id": "string",
    "namespace": "string"
  },
  "outputSchema": {
    "pdbs": [
      {
        "name": "string",
        "selector": "object",
        "min_available": "string | null",
        "max_unavailable": "string | null",
        "current_healthy": "number",
        "desired_healthy": "number",
        "disruptions_allowed": "number"
      }
    ]
  }
}
```

---

## 3. Internal DB MCP (Custom Build)

Exposes the PostgreSQL database to the AI agent via structured tools.

### Tools

#### `get_cluster_topology`
Return full cluster/node/zone topology for agent context.

```json
{
  "name": "get_cluster_topology",
  "description": "Get cluster nodes with zone mapping and current energy/load status",
  "inputSchema": {
    "cluster_id": { "type": "string", "optional": true },
    "include_non_green_only": { "type": "boolean", "optional": true }
  },
  "outputSchema": {
    "clusters": [
      {
        "cluster_id": "string",
        "cluster_name": "string",
        "nodes": [
          {
            "node_id": "string",
            "node_name": "string",
            "zone_name": "string",
            "zone_is_green": "boolean",
            "carbon_intensity": "number",
            "cpu_usage_percent": "number",
            "memory_usage_percent": "number",
            "is_overloaded": "boolean",
            "is_migration_target": "boolean",
            "migration_opt_out": "boolean"
          }
        ]
      }
    ]
  }
}
```

#### `get_migratable_workloads`
Return workloads eligible for migration (on non-green nodes, migration allowed).

```json
{
  "name": "get_migratable_workloads",
  "description": "Return workloads currently on non-green nodes and eligible for migration",
  "inputSchema": {
    "cluster_id": { "type": "string", "optional": true }
  },
  "outputSchema": {
    "workloads": [
      {
        "workload_id": "string",
        "name": "string",
        "namespace": "string",
        "workload_type": "string",
        "priority": "string",
        "current_node_name": "string",
        "current_zone": "string",
        "current_zone_is_green": "boolean",
        "current_zone_carbon_intensity": "number",
        "resource_requests_cpu": "number",
        "resource_requests_memory_gb": "number"
      }
    ]
  }
}
```

#### `record_ai_decision`
Persist an AI decision to the audit log.

```json
{
  "name": "record_ai_decision",
  "description": "Record an AI agent decision to the audit log",
  "inputSchema": {
    "agent_run_id": "string",
    "model_name": "string",
    "reasoning": "string",
    "decision_type": "migrate | skip | wait | alert",
    "recommended_actions": "array",
    "safety_check_passed": "boolean",
    "safety_check_notes": "string | null"
  },
  "outputSchema": { "decision_id": "string" }
}
```

#### `record_migration_event`
Record a migration start/update/completion.

```json
{
  "name": "record_migration_event",
  "description": "Create or update a migration event record",
  "inputSchema": {
    "workload_id": "string",
    "ai_decision_id": "string",
    "source_node_id": "string",
    "destination_node_id": "string",
    "status": "pending | in_progress | completed | failed | rolled_back",
    "trigger_reason": "string",
    "carbon_savings_estimate": "number"
  },
  "outputSchema": { "migration_event_id": "string" }
}
```

#### `get_migration_history`
Used by AI agent to inform cool-down and failure avoidance.

```json
{
  "name": "get_migration_history",
  "description": "Get recent migration history for a workload or node",
  "inputSchema": {
    "workload_id": { "type": "string", "optional": true },
    "node_id": { "type": "string", "optional": true },
    "hours_back": { "type": "integer", "default": 24 }
  },
  "outputSchema": {
    "migrations": [
      {
        "migration_id": "string",
        "workload_name": "string",
        "status": "string",
        "completed_at": "ISO8601",
        "duration_seconds": "number"
      }
    ]
  }
}
```

---

## Summary Table

| Tool | MCP Server | Build/Reuse |
|------|-----------|-------------|
| `get_zone_energy_status` | Green Energy MCP | 🔨 Build |
| `get_all_zones_energy_status` | Green Energy MCP | 🔨 Build |
| `get_greenest_zones` | Green Energy MCP | 🔨 Build |
| `get_zone_energy_forecast` | Green Energy MCP | 🔨 Build |
| `backfill_energy_history` | Green Energy MCP | 🔨 Build |
| `list_nodes` | Kubernetes MCP | ♻️ Reuse |
| `list_deployments` | Kubernetes MCP | ♻️ Reuse |
| `list_statefulsets` | Kubernetes MCP | ♻️ Reuse |
| `apply_manifest` | Kubernetes MCP | ♻️ Reuse |
| `get_events` | Kubernetes MCP | ♻️ Reuse |
| `get_node_metrics` | Kubernetes MCP | 🔧 Extend |
| `validate_migration_feasibility` | Kubernetes MCP | 🔧 Extend |
| `execute_migration` | Kubernetes MCP | 🔧 Extend |
| `check_migration_status` | Kubernetes MCP | 🔧 Extend |
| `rollback_migration` | Kubernetes MCP | 🔧 Extend |
| `get_pod_disruption_budgets` | Kubernetes MCP | 🔧 Extend |
| `get_cluster_topology` | Internal DB MCP | 🔨 Build |
| `get_migratable_workloads` | Internal DB MCP | 🔨 Build |
| `record_ai_decision` | Internal DB MCP | 🔨 Build |
| `record_migration_event` | Internal DB MCP | 🔨 Build |
| `get_migration_history` | Internal DB MCP | 🔨 Build |

**Legend**: 🔨 Custom build &nbsp;|&nbsp; ♻️ Reuse as-is &nbsp;|&nbsp; 🔧 Extend existing
