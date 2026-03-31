# Database Design — Green Workload AI

## Overview

The database is **PostgreSQL 16** and stores:
1. Cluster and node topology (static metadata)
2. Zone-to-energy-source mapping
3. Time-series energy readings per zone
4. Time-series node load metrics
5. Workload registry
6. Migration event audit log
7. AI decision audit log

---

## Entity Relationship Diagram

```
regions
  │
  ├──< zones >──< energy_readings (time-series)
  │
  └──< clusters
         │
         └──< nodes >──< node_metrics (time-series)
               │    │
               │    └── zone_id ──> zones
               │
               └──< workloads >──< migration_events
                                        │
                                        └── ai_decision_id ──> ai_decisions

agent_runs ──< ai_decisions
```

---

## Table Definitions

### `regions`
Geographic regions (e.g., US-WEST, EU-CENTRAL).

| Column | Type | Description |
|--------|------|-------------|
| `id` | UUID PK | |
| `name` | VARCHAR(100) UNIQUE | Machine name, e.g., `us-west-2` |
| `display_name` | VARCHAR(200) | Human-readable name |
| `country_code` | CHAR(2) | ISO 3166-1 alpha-2 |
| `latitude` | DECIMAL(9,6) | Geographic center lat |
| `longitude` | DECIMAL(9,6) | Geographic center lon |
| `created_at` | TIMESTAMPTZ | |
| `updated_at` | TIMESTAMPTZ | |

---

### `zones`
Availability zones within a region. Each zone maps to a specific energy grid area.

| Column | Type | Description |
|--------|------|-------------|
| `id` | UUID PK | |
| `name` | VARCHAR(100) | Zone name, e.g., `us-west-2a` |
| `region_id` | UUID FK → regions | |
| `display_name` | VARCHAR(200) | |
| `energy_provider` | VARCHAR(200) | e.g., `Pacific Gas & Electric` |
| `electricitymap_zone` | VARCHAR(50) | Electricity Maps zone code, e.g., `US-CAL-CISO` |
| `watttime_ba` | VARCHAR(50) | WattTime balancing authority |
| `created_at` | TIMESTAMPTZ | |
| `updated_at` | TIMESTAMPTZ | |

*Unique constraint*: `(name, region_id)`

---

### `clusters`
Managed Kubernetes clusters.

| Column | Type | Description |
|--------|------|-------------|
| `id` | UUID PK | |
| `name` | VARCHAR(200) UNIQUE | Cluster name |
| `display_name` | VARCHAR(200) | |
| `kubeconfig_secret_ref` | VARCHAR(500) | Reference to kubeconfig secret in secret store |
| `api_endpoint` | VARCHAR(500) | K8s API server URL |
| `region_id` | UUID FK → regions | Primary region of cluster |
| `status` | VARCHAR(50) | `active`, `inactive`, `maintenance` |
| `kubernetes_version` | VARCHAR(50) | |
| `managed_by` | VARCHAR(200) | `EKS`, `GKE`, `AKS`, `on-prem`, etc. |
| `created_at` | TIMESTAMPTZ | |
| `updated_at` | TIMESTAMPTZ | |

---

### `nodes`
Individual Kubernetes nodes within a cluster.

| Column | Type | Description |
|--------|------|-------------|
| `id` | UUID PK | |
| `name` | VARCHAR(200) | Node name (matches K8s node name) |
| `cluster_id` | UUID FK → clusters | |
| `zone_id` | UUID FK → zones | Which availability zone the node sits in |
| `provider_id` | VARCHAR(500) | Cloud provider instance ID (e.g., AWS `i-0abc123`) |
| `instance_type` | VARCHAR(100) | e.g., `m5.xlarge` |
| `operating_system` | VARCHAR(100) | |
| `kernel_version` | VARCHAR(100) | |
| `container_runtime` | VARCHAR(100) | e.g., `containerd://1.7.0` |
| `allocatable_cpu` | DECIMAL(10,3) | Allocatable CPU in cores |
| `allocatable_memory_gb` | DECIMAL(10,3) | Allocatable memory in GB |
| `allocatable_pods` | INTEGER | Max pods |
| `labels` | JSONB | K8s node labels |
| `taints` | JSONB | K8s node taints |
| `status` | VARCHAR(50) | `Ready`, `NotReady`, `Unknown` |
| `is_cordoned` | BOOLEAN | Whether node is cordoned |
| `is_migration_target` | BOOLEAN | AI may schedule workloads here |
| `migration_opt_out` | BOOLEAN | Admin-flagged: exclude from AI migrations |
| `created_at` | TIMESTAMPTZ | |
| `updated_at` | TIMESTAMPTZ | |

*Unique constraint*: `(name, cluster_id)`

---

### `energy_readings` *(time-series)*
Point-in-time energy source readings per zone. Populated by the Green Energy MCP.

| Column | Type | Description |
|--------|------|-------------|
| `id` | UUID PK | |
| `zone_id` | UUID FK → zones | |
| `timestamp` | TIMESTAMPTZ | When reading was taken |
| `carbon_intensity` | DECIMAL(8,3) | gCO₂eq/kWh |
| `renewable_percentage` | DECIMAL(5,2) | 0–100 |
| `energy_sources` | JSONB | `{"solar": 20.5, "wind": 35.2, "hydro": 10.0, "coal": 34.3}` |
| `is_green` | BOOLEAN | **Generated**: `renewable_percentage >= 50` |
| `data_source` | VARCHAR(100) | `electricity_maps`, `watttime`, `manual` |
| `data_quality` | VARCHAR(50) | `live`, `estimated`, `historical` |
| `created_at` | TIMESTAMPTZ | |

*Indexes*: `(zone_id, timestamp DESC)`, `(is_green, timestamp DESC)`

> **Retention**: Keep full resolution for 7 days, hourly aggregates for 90 days, daily aggregates for 3 years.

---

### `node_metrics` *(time-series)*
Periodic snapshots of Kubernetes node resource utilization. Populated by the K8s MCP.

| Column | Type | Description |
|--------|------|-------------|
| `id` | UUID PK | |
| `node_id` | UUID FK → nodes | |
| `timestamp` | TIMESTAMPTZ | |
| `cpu_usage_cores` | DECIMAL(10,3) | |
| `cpu_usage_percent` | DECIMAL(5,2) | |
| `memory_usage_gb` | DECIMAL(10,3) | |
| `memory_usage_percent` | DECIMAL(5,2) | |
| `pod_count` | INTEGER | Running pods on node |
| `network_in_mbps` | DECIMAL(10,3) | |
| `network_out_mbps` | DECIMAL(10,3) | |
| `is_overloaded` | BOOLEAN | **Generated**: CPU>80% OR memory>80% |
| `created_at` | TIMESTAMPTZ | |

*Index*: `(node_id, timestamp DESC)`

---

### `workloads`
Registry of tracked Kubernetes workloads managed by the system.

| Column | Type | Description |
|--------|------|-------------|
| `id` | UUID PK | |
| `name` | VARCHAR(200) | K8s resource name |
| `namespace` | VARCHAR(200) | K8s namespace |
| `cluster_id` | UUID FK → clusters | |
| `workload_type` | VARCHAR(50) | `Deployment`, `StatefulSet`, `DaemonSet`, `ReplicaSet` |
| `current_node_id` | UUID FK → nodes | Primary node (NULL for multi-replica) |
| `replica_count` | INTEGER | Number of replicas |
| `priority` | VARCHAR(50) | `critical`, `high`, `normal`, `low` |
| `migration_allowed` | BOOLEAN | AI can migrate this workload |
| `stateful` | BOOLEAN | Has persistent storage (extra caution) |
| `labels` | JSONB | K8s labels |
| `annotations` | JSONB | K8s annotations (includes opt-in flags) |
| `resource_requests_cpu` | DECIMAL(10,3) | Requested CPU in cores |
| `resource_requests_memory_gb` | DECIMAL(10,3) | Requested memory in GB |
| `last_seen_at` | TIMESTAMPTZ | Last time agent verified workload exists |
| `created_at` | TIMESTAMPTZ | |
| `updated_at` | TIMESTAMPTZ | |

*Unique constraint*: `(name, namespace, cluster_id)`

---

### `migration_events`
Audit log of every migration attempted by the system.

| Column | Type | Description |
|--------|------|-------------|
| `id` | UUID PK | |
| `workload_id` | UUID FK → workloads | |
| `workload_name` | VARCHAR(200) | Denormalized for history |
| `namespace` | VARCHAR(200) | |
| `cluster_id` | UUID FK → clusters | |
| `source_node_id` | UUID FK → nodes | |
| `destination_node_id` | UUID FK → nodes | |
| `source_zone_id` | UUID FK → zones | |
| `destination_zone_id` | UUID FK → zones | |
| `migration_type` | VARCHAR(50) | `node_selector`, `taint_toleration`, `affinity` |
| `status` | VARCHAR(50) | `pending`, `in_progress`, `completed`, `failed`, `rolled_back` |
| `ai_decision_id` | UUID FK → ai_decisions | Decision that triggered this migration |
| `trigger_reason` | TEXT | AI's stated reason |
| `source_carbon_intensity` | DECIMAL(8,3) | gCO₂eq/kWh at source |
| `destination_carbon_intensity` | DECIMAL(8,3) | gCO₂eq/kWh at destination |
| `carbon_savings_estimate` | DECIMAL(8,3) | Estimated carbon saved |
| `started_at` | TIMESTAMPTZ | |
| `completed_at` | TIMESTAMPTZ | |
| `duration_seconds` | INTEGER | |
| `error_message` | TEXT | |
| `rollback_attempted` | BOOLEAN | |
| `created_at` | TIMESTAMPTZ | |
| `updated_at` | TIMESTAMPTZ | |

*Indexes*: `(status)`, `(workload_id)`, `(ai_decision_id)`

---

### `ai_decisions`
Audit log of every AI reasoning step and decision.

| Column | Type | Description |
|--------|------|-------------|
| `id` | UUID PK | |
| `agent_run_id` | UUID FK → agent_runs | Groups decisions from one run |
| `timestamp` | TIMESTAMPTZ | |
| `model_name` | VARCHAR(200) | e.g., `llama3.3:70b` |
| `input_context` | JSONB | Sanitized snapshot of data fed to LLM |
| `reasoning` | TEXT | LLM chain-of-thought explanation |
| `decision_type` | VARCHAR(50) | `migrate`, `skip`, `wait`, `alert` |
| `recommended_actions` | JSONB | List of recommended migration actions |
| `safety_check_passed` | BOOLEAN | Did the actions pass safety validation? |
| `safety_check_notes` | TEXT | Details of any safety failures |
| `execution_started` | BOOLEAN | Were the actions executed? |
| `created_at` | TIMESTAMPTZ | |

---

### `agent_runs`
Tracks each autonomous evaluation cycle.

| Column | Type | Description |
|--------|------|-------------|
| `id` | UUID PK | |
| `started_at` | TIMESTAMPTZ | |
| `completed_at` | TIMESTAMPTZ | |
| `status` | VARCHAR(50) | `running`, `completed`, `failed` |
| `clusters_evaluated` | INTEGER | |
| `workloads_evaluated` | INTEGER | |
| `migrations_initiated` | INTEGER | |
| `error_message` | TEXT | |
| `created_at` | TIMESTAMPTZ | |

---

## Useful Views

### `zone_current_energy`
Latest energy reading per zone (used by AI agent at query time).

```sql
SELECT DISTINCT ON (z.id)
    z.id AS zone_id, z.name AS zone_name, r.name AS region_name,
    er.carbon_intensity, er.renewable_percentage, er.is_green,
    er.energy_sources, er.timestamp AS last_updated, er.data_quality
FROM zones z
JOIN regions r ON z.region_id = r.id
LEFT JOIN energy_readings er ON er.zone_id = z.id
ORDER BY z.id, er.timestamp DESC;
```

### `node_current_status`
Joined view of node health, zone greenness, and latest metrics — the primary input to the AI agent.

```sql
SELECT DISTINCT ON (n.id)
    n.id, n.name AS node_name, c.name AS cluster_name,
    z.name AS zone_name, n.status, n.is_cordoned, n.migration_opt_out,
    n.allocatable_cpu, n.allocatable_memory_gb,
    nm.cpu_usage_percent, nm.memory_usage_percent, nm.pod_count, nm.is_overloaded,
    nm.timestamp AS metrics_last_updated,
    zce.is_green, zce.carbon_intensity, zce.renewable_percentage
FROM nodes n
JOIN clusters c ON n.cluster_id = c.id
LEFT JOIN zones z ON n.zone_id = z.id
LEFT JOIN node_metrics nm ON nm.node_id = n.id
LEFT JOIN zone_current_energy zce ON zce.zone_id = n.zone_id
ORDER BY n.id, nm.timestamp DESC;
```

### `migration_summary`
Carbon savings aggregated by cluster and time period.

```sql
SELECT
    c.name AS cluster,
    DATE_TRUNC('day', me.completed_at) AS day,
    COUNT(*) AS migrations_completed,
    SUM(me.carbon_savings_estimate) AS total_carbon_saved_gco2
FROM migration_events me
JOIN clusters c ON me.cluster_id = c.id
WHERE me.status = 'completed'
GROUP BY c.name, DATE_TRUNC('day', me.completed_at)
ORDER BY day DESC;
```

---

## Data Retention Policy

| Table | Full Resolution | Aggregated | Archive |
|-------|----------------|------------|---------|
| `energy_readings` | 7 days | 90 days (hourly) | 3 years (daily) |
| `node_metrics` | 7 days | 30 days (hourly) | 1 year (daily) |
| `migration_events` | Forever | — | — |
| `ai_decisions` | 90 days | — | Archived to cold storage |
| `agent_runs` | 90 days | — | — |
