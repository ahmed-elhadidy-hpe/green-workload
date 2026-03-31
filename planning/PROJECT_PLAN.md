# Green Workload Migration AI — Project Plan

## 1. Problem Statement

Data centers and cloud providers have varying levels of renewable energy availability across geographic zones. Energy greenness depends on location, time of day, and regional grid conditions. Today, Kubernetes workloads are scheduled purely on capacity and affinity rules — with zero awareness of the carbon intensity of the underlying energy source. This causes workloads to run on fossil-fuel-powered nodes even when greener alternatives exist within the same managed cluster portfolio.

**Goal**: Build an autonomous AI-driven system that continuously monitors the energy source of each Kubernetes zone and intelligently migrates workloads to nodes running on green energy — while respecting node capacity limits, SLA constraints, and workload migration safety rules.

---

## 2. Approach

The system adopts an **agentic AI architecture** where a Large Language Model (LLM) acts as the reasoning engine, orchestrating a set of MCP (Model Context Protocol) tools.

```
┌─────────────────────────────────────────────────────────────────────┐
│                      Green Workload AI Agent                         │
│                                                                       │
│   ┌─────────────┐    ┌──────────────────────┐    ┌───────────────┐  │
│   │  Scheduler  │───▶│   LLM Reasoning      │───▶│ Decision Log  │  │
│   │ (cron/k8s)  │    │  (Ollama / Groq)     │    │  (Postgres)   │  │
│   └─────────────┘    └──────────┬───────────┘    └───────────────┘  │
│                                 │ MCP Tool Calls                     │
└─────────────────────────────────┼─────────────────────────────────── ┘
                                  │
              ┌───────────────────┼──────────────────┐
              ▼                   ▼                   ▼
    ┌─────────────────┐  ┌──────────────────┐  ┌────────────────┐
    │  Green Energy   │  │   Kubernetes     │  │  Internal DB   │
    │     MCP         │  │     MCP          │  │     MCP        │
    │  (custom-built) │  │ (reuse existing) │  │ (custom-built) │
    └────────┬────────┘  └───────┬──────────┘  └───────┬────────┘
             │                   │                      │
             ▼                   ▼                      ▼
    ┌─────────────────┐  ┌──────────────────┐  ┌────────────────┐
    │ Electricity Maps│  │  K8s Clusters    │  │  PostgreSQL DB │
    │  / WattTime API │  │  (multi-cluster) │  │  (metadata +   │
    └─────────────────┘  └──────────────────┘  │   audit log)   │
                                               └────────────────┘
```

### Core Principles

| Principle | Description |
|-----------|-------------|
| **Non-disruptive** | Rolling updates, PodDisruptionBudget (PDB) compliance |
| **Observable** | Full audit trail of every AI decision and action |
| **Conservative** | When uncertain → do not migrate. Safety overrides LLM output |
| **Extensible** | Phase 1: Kubernetes; future phases: VMs, bare-metal, edge |
| **No human in the loop** | Fully autonomous once configured |

---

## 3. Implementation Details

### Phase 1 — Foundation

#### 3.1 Internal Database (PostgreSQL)
- Stores cluster, node, zone, and energy metadata
- Time-series energy readings and node load metrics
- Audit log of AI decisions and migration events
- See `db/DB_DESIGN.md` for full schema and ERD

#### 3.2 Green Energy MCP (Custom Build)
- Wraps **Electricity Maps** and/or **WattTime** carbon intensity APIs
- Provides real-time and forecasted carbon intensity per zone
- Tools: `get_zone_energy_status`, `get_greenest_zones`, `list_green_zones`
- See `mcp/MCP_APIS.md` for full specification

#### 3.3 Kubernetes MCP (Reuse + Extend)
- Reuses `kubernetes-mcp-server` for base K8s operations
- Custom extensions: `migrate_workload`, `check_migration_status`, `get_node_metrics`
- Handles: cordon, drain, node affinity patching, rollout status monitoring

#### 3.4 Internal DB MCP (Custom Build)
- Thin MCP wrapper over PostgreSQL DB
- Allows the LLM agent to query cluster/node/zone metadata and write decisions
- Tools: `get_cluster_topology`, `get_node_zone_mapping`, `record_decision`, `get_migration_history`

#### 3.5 AI Agent
- LLM-based reasoning loop using **Ollama + LLaMA 3.3 70B** (local/free)
  or **Groq free tier** (cloud)
- Supports MCP tool calling (function calling)
- Decision output: structured JSON migration plan
- Hard safety rules enforced in code — LLM output is validated before execution
- See `ai/AI_MODEL.md` for full integration guide

#### 3.6 Scheduler
- Kubernetes CronJob or standalone cron (configurable interval, default: 10 minutes)
- Triggers the agent evaluation cycle
- Configurable "migration window" — e.g., restrict migrations to off-peak hours

---

### Phase 2 — Intelligence Enhancement
- Historical pattern learning for **predictive pre-migration**
  (e.g., renewable energy tends to peak at midday in solar-heavy zones)
- Carbon savings scoring per migration candidate
- Multi-cluster cross-zone workload rebalancing
- Slack/PagerDuty alerting for significant green/non-green transitions

### Phase 3 — Expansion
- VM workload support
- Bare-metal workload support
- Cloud-native carbon APIs (AWS Sustainability, Azure Carbon Optimization, GCP Carbon Footprint)

---

## 4. Key Decision Logic

The AI agent evaluates each candidate workload-to-node migration using these weighted factors:

| Factor | Priority | Rule |
|--------|----------|------|
| Energy greenness of destination zone | **High** | Renewable % ≥ 50% |
| Node CPU utilization | **High** | Destination node CPU < 80% |
| Node memory utilization | **High** | Destination node memory < 80% |
| Workload priority | **High** | Critical workloads → last to migrate, safest path |
| Workload type | **High** | StatefulSets require explicit opt-in annotation |
| PodDisruptionBudget | **Hard** | Never violate PDB — migration blocked if would breach |
| Node health | **Hard** | Destination node must be in `Ready` state |
| Migration cool-down | **Medium** | Same workload not migrated more than once per hour |
| Source zone energy trend | **Medium** | Is the source zone improving? May defer migration |
| Migration history success rate | **Medium** | Avoid nodes/workloads with recent failures |

**Hard safety rules** (enforced in application code, not left to LLM):
- Never migrate if it would violate a PodDisruptionBudget
- Never migrate a StatefulSet unless it has annotation `green-workload/migration-allowed: "true"`
- Never cordon a node if it would make the cluster under-capacity for current workloads
- Always verify the destination node is `Ready` and not already cordoned
- Maximum 5 concurrent migrations across all clusters at one time

---

## 5. Technology Stack

| Component | Technology |
|-----------|------------|
| AI Reasoning Engine | Ollama (local) or Groq API (cloud) with LLaMA 3.3 70B |
| MCP Framework | Model Context Protocol SDK (Python or TypeScript) |
| Internal DB | PostgreSQL 16 |
| Scheduler | Kubernetes CronJob |
| Agent Runtime | Python (langchain or direct MCP client) |
| Carbon Intensity Data | Electricity Maps API, WattTime API |
| Kubernetes Client | kubernetes Python client (in K8s MCP) |
| Observability | Prometheus metrics + structured JSON logs |

---

## 6. Expected Results

| Metric | Target |
|--------|--------|
| Carbon-attributed compute reduction | 30–60% (zone-dependent) |
| Workload availability during migration | >99.9% |
| False migration rate (migrated to non-green) | <1% |
| Mean time to migrate a Deployment | <5 minutes |
| System autonomous operation | 100% |
| Migration rollback success rate | >95% |

---

## 7. Risk Mitigation

| Risk | Mitigation |
|------|------------|
| Migration causes workload downtime | Rolling updates, PDB checks, pre-flight validation |
| Energy data is stale or incorrect | Staleness threshold (reject data >15 min old) |
| Node overload after migration | Pre-check capacity + post-migration node metric monitoring |
| LLM makes bad decision | Hard-coded safety rules override LLM; all actions validated |
| Cascading migrations destabilize cluster | Rate limiting + per-cluster migration cool-down periods |
| Carbon API rate limits | Local caching with TTL + fallback to DB historical data |

---

## 8. Project Milestones

| Milestone | Deliverable |
|-----------|-------------|
| M1: Infrastructure | DB schema deployed, Green Energy MCP live, K8s MCP configured |
| M2: Core Agent | LLM agent reasoning loop working end-to-end in dry-run mode |
| M3: First Migration | First live autonomous migration executed and validated |
| M4: Hardening | Safety rules, rollback, rate limiting, observability |
| M5: Phase 2 | Predictive migration, carbon scoring, multi-cluster balancing |
