# Green Workload AI — Artifacts Index

Autonomous AI system for migrating Kubernetes workloads to green energy zones.

## Repository Structure

```
green-workload-ai/
├── main.py                             ← Entry point (scheduler loop, --once, --setup)
├── setup_db.py                         ← Creates MySQL DB, runs schema, inserts seed data
├── requirements.txt                    ← Python dependencies
├── .env.example                        ← Environment variable template
│
├── config/
│   └── settings.py                     ← Pydantic settings (DB, LLM, thresholds, MCP cmds)
│
├── src/
│   ├── agent/
│   │   ├── agent.py                    ← GreenWorkloadAgent — main evaluation cycle
│   │   ├── prompts.py                  ← System prompt + build_user_prompt()
│   │   └── safety.py                   ← SafetyValidator — hard rule enforcement
│   │
│   ├── scheduler/
│   │   └── scheduler.py                ← APScheduler interval runner (run_scheduler_forever)
│   │
│   ├── database/
│   │   ├── connection.py               ← SQLAlchemy engine / session factory
│   │   ├── models.py                   ← ORM models (Region, Zone, Cluster, Node, …)
│   │   └── repository.py               ← GreenWorkloadRepository — all DB queries
│   │
│   └── mcp_servers/
│       ├── green_energy/
│       │   ├── server.py               ← MCP tools: zone energy status, forecast, backfill
│       │   └── energy_client.py        ← Electricity Maps / WattTime / Mock client
│       ├── kubernetes_mcp/
│       │   └── server.py               ← MCP tools: list nodes, metrics, migrate, rollback
│       └── internal_db/
│           └── server.py               ← MCP tools: agent runs, decisions, migration events
│
└── planning/
    ├── PROJECT_PLAN.md                 ← Problem statement, approach, milestones
    ├── db/
    │   ├── DB_DESIGN.md                ← Database design, ERD, table descriptions
    │   ├── schema.sql                  ← PostgreSQL DDL schema
    │   └── mysql_schema.sql            ← MySQL DDL schema (used by setup_db.py)
    ├── diagrams/
    │   └── SEQUENCE_DIAGRAMS.md        ← 5 Mermaid sequence diagrams
    ├── mcp/
    │   └── MCP_APIS.md                 ← All MCP tools — input/output schemas
    └── ai/
        └── AI_MODEL.md                 ← Model selection, prompts, code skeleton
```

## Source Code Overview

### `main.py` — Entry Point
Parses CLI flags and starts the system:
- `python main.py` — starts the scheduled agent loop
- `python main.py --once` — runs a single evaluation cycle and exits
- `python main.py --setup` — creates/migrates the database schema

### `config/settings.py` — Configuration
Pydantic `BaseSettings` loaded from `.env`. Key settings:

| Setting | Default | Description |
|---------|---------|-------------|
| `OLLAMA_MODEL` | `llama3.1:8b` | LLM model served by Ollama |
| `OLLAMA_BASE_URL` | `http://localhost:11434/v1` | OpenAI-compatible API base |
| `SCHEDULE_INTERVAL_SECONDS` | `10` | How often the agent evaluates |
| `DRY_RUN` | `False` | Simulate migrations without applying |
| `MIN_RENEWABLE_PCT` | `50.0` | Minimum % renewables to consider a zone "green" |
| `MAX_CONCURRENT_MIGRATIONS` | `5` | Safety cap on in-flight migrations |
| `NODE_CPU_THRESHOLD` | `80.0` | Max CPU % allowed on destination node |
| `NODE_MEMORY_THRESHOLD` | `80.0` | Max memory % allowed on destination node |

### `src/agent/` — AI Agent Core

| File | Class / Key function | Purpose |
|------|----------------------|---------|
| `agent.py` | `GreenWorkloadAgent` | Orchestrates a full evaluation cycle: collect energy data → call LLM → validate safety → execute migrations |
| `prompts.py` | `SYSTEM_PROMPT`, `build_user_prompt()` | Defines the LLM persona, hard rules, JSON output schema, and dynamic user prompt |
| `safety.py` | `SafetyValidator` | Enforces hard safety rules (node readiness, capacity thresholds, concurrent migration cap, StatefulSet opt-in) |

**Agent cycle (`run_cycle`):**
1. Pull energy status and cluster topology from the DB
2. Query migratable workloads (on non-green zones)
3. Call Ollama LLM with structured context; falls back to rule-based logic on failure
4. Record AI decision to DB
5. Run each proposed action through `SafetyValidator`
6. Execute approved migrations (node affinity patch via K8s MCP)

### `src/scheduler/` — Scheduler
`scheduler.py` wraps APScheduler's `AsyncIOScheduler` to fire the agent at the configured interval, runs once immediately on startup, and shuts down cleanly on `KeyboardInterrupt`.

### `src/database/` — Data Layer

| File | Purpose |
|------|---------|
| `connection.py` | SQLAlchemy engine and `SessionLocal` factory |
| `models.py` | ORM models: `Region`, `Zone`, `Cluster`, `Node`, `EnergyReading`, `NodeMetric`, `Workload`, `AgentRun`, `AiDecision`, `MigrationEvent` |
| `repository.py` | `GreenWorkloadRepository` — typed query methods used by both the agent and MCP servers |

### `src/mcp_servers/` — MCP Tool Servers
Three FastMCP servers, each runnable as a subprocess over stdio:

**`green_energy/server.py`** — Energy data tools

| Tool | Description |
|------|-------------|
| `get_zone_energy_status` | Fetch & cache energy data for a single zone |
| `get_all_zones_energy_status` | Fetch & cache energy data for all zones |
| `get_greenest_zones` | Return zones ranked by renewable % above a threshold |
| `get_zone_energy_forecast` | Short-term forecast (mock variation around current reading) |
| `backfill_energy_history` | Populate historical energy readings at hourly intervals |

**`kubernetes_mcp/server.py`** — Kubernetes management tools

| Tool | Description |
|------|-------------|
| `list_nodes` | List nodes with status, labels, taints |
| `get_node_metrics` | CPU/memory usage (metrics-server or simulated fallback) |
| `validate_migration_feasibility` | Check node readiness, PDB, StatefulSet opt-in |
| `execute_migration` | Patch workload with `nodeAffinity` for destination node |
| `check_migration_status` | Verify pod placement after migration |
| `rollback_migration` | Remove `nodeAffinity` patch to revert migration |
| `get_pod_disruption_budgets` | List PDBs for a namespace |
| `discover_nodes` | Full node inventory with zone hints from labels |

**`internal_db/server.py`** — Database access tools

| Tool | Description |
|------|-------------|
| `create_agent_run` / `complete_agent_run` | Lifecycle management for agent run records |
| `get_cluster_topology` | Nodes, zones, and energy data for a cluster |
| `get_migratable_workloads` | Workloads on non-green zones eligible for migration |
| `get_migration_history` | Recent migration events by workload or node |
| `record_ai_decision` | Persist LLM decision and reasoning |
| `record_migration_event` / `update_migration_status` | Track each migration lifecycle |
| `get_all_zones_with_energy` | Zones with latest energy readings |

### `setup_db.py` — Database Bootstrap
Connects to MySQL, creates the `GREEN_WORKLOAD_DB` database if absent, executes `planning/db/mysql_schema.sql`, and inserts deterministic seed data (1 region, 2 zones, 1 cluster, 2 nodes).

---

## Quick Navigation — Planning Docs

| Document | What's Inside |
|----------|---------------|
| [planning/PROJECT_PLAN.md](planning/PROJECT_PLAN.md) | Problem statement, architecture overview, decision logic, risks, milestones |
| [planning/db/DB_DESIGN.md](planning/db/DB_DESIGN.md) | All tables with column-by-column descriptions, ERD, retention policy |
| [planning/db/schema.sql](planning/db/schema.sql) | PostgreSQL DDL (tables, indexes, views, triggers) |
| [planning/db/mysql_schema.sql](planning/db/mysql_schema.sql) | MySQL DDL used by `setup_db.py` |
| [planning/diagrams/SEQUENCE_DIAGRAMS.md](planning/diagrams/SEQUENCE_DIAGRAMS.md) | Main agent loop, energy monitoring, migration execution, metrics collection, bootstrap |
| [planning/mcp/MCP_APIS.md](planning/mcp/MCP_APIS.md) | 21 MCP tools across 3 servers — input/output schemas, build vs reuse classification |
| [planning/ai/AI_MODEL.md](planning/ai/AI_MODEL.md) | LLaMA 3.3 70B recommendation, Ollama/Groq setup, system prompt, Python agent skeleton |

## Key Decisions at a Glance

| Decision | Choice | Rationale |
|----------|--------|-----------|
| AI Model | LLaMA 3.3 70B (Ollama/Groq) | Free, supports tool calling, 128K context, no fine-tuning needed |
| Energy API | Electricity Maps + WattTime | Both have free tiers; complementary data |
| K8s MCP | Custom FastMCP server | Direct `kubernetes` Python client; dry-run aware |
| Database | MySQL (via SQLAlchemy) | Time-series + relational; ORM + raw queries via repository pattern |
| Migration method | Node Affinity patching | Non-disruptive rolling update; reversible |
| Safety model | Hard rules in code, LLM ranks candidates | LLM cannot override safety constraints |
