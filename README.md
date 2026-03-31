# Green Workload AI — Artifacts Index

Autonomous AI system for migrating Kubernetes workloads to green energy zones.

## Directory Structure

```
green-workload-ai/
├── planning
    ├── README.md                       ← This file
    ├── PROJECT_PLAN.md                 ← Problem statement, approach, milestones
    ├── db/
    │   ├── DB_DESIGN.md                ← Database design, ERD, table descriptions
    │   └── schema.sql                  ← Full PostgreSQL DDL schema
    ├── diagrams/
    │   └── SEQUENCE_DIAGRAMS.md        ← 5 Mermaid sequence diagrams
    ├── mcp/
    │   └── MCP_APIS.md                 ← All MCP tools (build vs reuse)
    └── ai/
        └── AI_MODEL.md                 ← Model selection, prompts, code skeleton
    ```

## Quick Navigation

| Document | What's Inside |
|----------|---------------|
| [PROJECT_PLAN.md](PROJECT_PLAN.md) | Problem statement, architecture overview, decision logic, risks, milestones |
| [db/DB_DESIGN.md](db/DB_DESIGN.md) | All tables with column-by-column descriptions, ERD, retention policy |
| [db/schema.sql](db/schema.sql) | Ready-to-run PostgreSQL DDL (tables, indexes, views, triggers) |
| [diagrams/SEQUENCE_DIAGRAMS.md](diagrams/SEQUENCE_DIAGRAMS.md) | Main agent loop, energy monitoring, migration execution, metrics collection, bootstrap |
| [mcp/MCP_APIS.md](mcp/MCP_APIS.md) | 21 MCP tools across 3 servers — input/output schemas, build vs reuse classification |
| [ai/AI_MODEL.md](ai/AI_MODEL.md) | LLaMA 3.3 70B recommendation, Ollama/Groq setup, system prompt, Python agent skeleton |

## Key Decisions at a Glance

| Decision | Choice | Rationale |
|----------|--------|-----------|
| AI Model | LLaMA 3.3 70B (Ollama/Groq) | Free, supports tool calling, 128K context, no fine-tuning needed |
| Energy API | Electricity Maps + WattTime | Both have free tiers; complementary data |
| K8s MCP | `mcp-k8s-go` + custom extensions | Most mature open-source K8s MCP |
| Database | PostgreSQL 16 | Time-series + relational; views simplify AI queries |
| Migration method | Node Affinity patching | Non-disruptive rolling update; reversible |
| Safety model | Hard rules in code, LLM ranks candidates | LLM cannot override safety constraints |
