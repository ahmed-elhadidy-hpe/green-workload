# AI Model Selection & Integration Guide

## Requirements Recap

| Requirement | Constraint |
|-------------|-----------|
| Cost | Free (or near-free) |
| ML training | Not required — no fine-tuning, prompt engineering only |
| Tool/function calling | Required (must invoke MCP tools) |
| Reasoning quality | High — must weigh multiple factors and apply logic |
| Deployment | Local or cloud API |

---

## Recommended Model: LLaMA 3.3 70B via Ollama (Local) or Groq (Cloud)

### Why LLaMA 3.3 70B?

| Criteria | Assessment |
|----------|-----------|
| **Free** | ✅ Ollama is fully free and local; Groq has a generous free tier |
| **No ML training** | ✅ Used purely via prompting + function calling |
| **Tool/function calling** | ✅ Native support in LLaMA 3.1+ (tool_use format) |
| **Reasoning quality** | ✅ Competitive with GPT-4o on reasoning benchmarks |
| **Context window** | ✅ 128K tokens (can fit full cluster topology + instructions) |
| **JSON output** | ✅ Reliable structured output with system prompt guidance |
| **Privacy** | ✅ Ollama runs fully local — no data leaves your infrastructure |

### Alternative Free Models

| Model | Provider | Notes |
|-------|----------|-------|
| **LLaMA 3.3 70B** | Ollama (local) / Groq | **Recommended** — best balance of reasoning + tool use |
| **LLaMA 3.1 8B** | Ollama (local) | Lighter, faster; lower reasoning quality |
| **Qwen2.5 72B** | Ollama (local) | Strong reasoning; good tool calling |
| **Mistral 7B** | Ollama (local) | Lightweight; weaker multi-step reasoning |
| **Gemini 1.5 Flash** | Google AI Studio | Free tier; cloud-only |
| **Gemini 2.0 Flash** | Google AI Studio | Free tier; very capable; cloud-only |

---

## Deployment Options

### Option A — Ollama (Recommended for On-Prem / Air-Gapped)

**Setup**:
```bash
# Install Ollama
curl -fsSL https://ollama.com/install.sh | sh

# Pull the model (once, ~40GB for 70B)
ollama pull llama3.3:70b

# Or use the quantized version (lighter, ~20GB)
ollama pull llama3.3:70b-instruct-q4_K_M

# Run Ollama as a service
ollama serve
```

**API endpoint**: `http://localhost:11434` (OpenAI-compatible)

**Kubernetes deployment**:
```yaml
# Deploy Ollama as a K8s Deployment with GPU node selector
apiVersion: apps/v1
kind: Deployment
metadata:
  name: ollama-server
spec:
  replicas: 1
  selector:
    matchLabels:
      app: ollama
  template:
    spec:
      nodeSelector:
        accelerator: gpu  # Schedule on GPU node
      containers:
        - name: ollama
          image: ollama/ollama:latest
          resources:
            limits:
              nvidia.com/gpu: 1
          env:
            - name: OLLAMA_HOST
              value: "0.0.0.0"
          volumeMounts:
            - name: ollama-models
              mountPath: /root/.ollama
      volumes:
        - name: ollama-models
          persistentVolumeClaim:
            claimName: ollama-models-pvc
```

### Option B — Groq API (Recommended for Cloud / Quick Start)

Groq offers a **free tier** with generous rate limits (14,400 requests/day for LLaMA 3.3 70B as of 2025).

```python
from groq import Groq

client = Groq(api_key="gsk_your_key_here")  # Free at console.groq.com
```

---

## Integration Architecture

The AI agent uses the **MCP client SDK** in Python to connect to all three MCP servers and the LLM.

```
┌─────────────────────────────────────────────────────────┐
│                   AI Agent (Python)                      │
│                                                           │
│  ┌────────────────────────────────────────────────────┐  │
│  │              Agent Reasoning Loop                   │  │
│  │                                                     │  │
│  │  1. Collect context via MCP tool calls              │  │
│  │  2. Build LLM prompt with context + instructions    │  │
│  │  3. Call LLM → get structured decision + actions    │  │
│  │  4. Validate actions against safety rules           │  │
│  │  5. Execute approved actions via MCP tool calls     │  │
│  │  6. Record decisions to DB MCP                      │  │
│  └────────────────────────────────────────────────────┘  │
│                                                           │
│  MCP Clients:                                             │
│  ┌──────────────┐ ┌─────────────────┐ ┌───────────────┐ │
│  │ Green Energy │ │   Kubernetes    │ │  Internal DB  │ │
│  │ MCP Client   │ │   MCP Client    │ │  MCP Client   │ │
│  └──────────────┘ └─────────────────┘ └───────────────┘ │
│                                                           │
│  LLM Client:                                              │
│  ┌────────────────────────────────────┐                  │
│  │ Ollama client  OR  Groq client     │                  │
│  │ (OpenAI-compatible API)            │                  │
│  └────────────────────────────────────┘                  │
└─────────────────────────────────────────────────────────┘
```

---

## Agent Prompt Design

### System Prompt

```
You are an autonomous green workload migration agent. Your job is to migrate 
Kubernetes workloads from nodes on non-green energy zones to nodes on green 
energy zones, while ensuring workload availability and cluster stability.

You have access to the following tools:
- Green Energy MCP: check zone carbon intensity and renewable energy percentages
- Kubernetes MCP: check node health, metrics, and execute workload migrations
- Internal DB MCP: query cluster topology and record your decisions

Decision rules you MUST follow (these are hard constraints, not suggestions):
1. NEVER migrate a workload if it would violate a PodDisruptionBudget
2. NEVER migrate a StatefulSet unless it has annotation "green-workload/migration-allowed: true"
3. NEVER select a destination node with CPU > 80% or memory > 80% utilization
4. NEVER select a destination node that is not in "Ready" status
5. NEVER initiate more than 3 concurrent migrations across all clusters
6. ALWAYS prefer nodes with the highest renewable_percentage among candidates
7. If in doubt about safety, choose "skip" over "migrate"

Output format: After reasoning, output a JSON block:
{
  "decision_type": "migrate | skip | wait",
  "reasoning": "brief explanation",
  "actions": [
    {
      "workload_name": "string",
      "namespace": "string",
      "cluster_id": "string",
      "destination_node": "string",
      "reason": "string"
    }
  ]
}
```

### User Prompt (generated per cycle)

```
Current date/time: {timestamp}

== ZONE ENERGY STATUS ==
{json: zone energy readings from get_all_zones_energy_status}

== NODE STATUS ==
{json: node_current_status view from get_cluster_topology}

== MIGRATABLE WORKLOADS (currently on non-green nodes) ==
{json: workloads from get_migratable_workloads}

== RECENT MIGRATION HISTORY (last 2 hours) ==
{json: from get_migration_history}

Please analyze the current state and decide which workloads (if any) should be 
migrated to greener nodes. Run validation checks for each candidate before 
recommending a migration.
```

---

## Code Skeleton

```python
# agent/green_workload_agent.py

import json
import asyncio
from datetime import datetime
from mcp import Client, StdioServerParameters

# Use OpenAI-compatible client for both Ollama and Groq
from openai import OpenAI

# ── LLM Client ────────────────────────────────────────────────────────────────

def get_llm_client(provider: str = "ollama"):
    if provider == "ollama":
        return OpenAI(base_url="http://localhost:11434/v1", api_key="ollama")
    elif provider == "groq":
        return OpenAI(
            base_url="https://api.groq.com/openai/v1",
            api_key="gsk_your_groq_key"
        )

MODEL = "llama3.3:70b"  # or "llama-3.3-70b-versatile" for Groq

# ── Agent Loop ─────────────────────────────────────────────────────────────────

async def run_evaluation_cycle(
    db_client: Client,
    energy_client: Client,
    k8s_client: Client,
    llm: OpenAI
):
    # 1. Create agent run
    run = await db_client.call_tool("create_agent_run", {})
    run_id = run["agent_run_id"]

    # 2. Collect context in parallel
    energy_status, topology, history = await asyncio.gather(
        energy_client.call_tool("get_all_zones_energy_status", {}),
        db_client.call_tool("get_cluster_topology", {}),
        db_client.call_tool("get_migration_history", {"hours_back": 2})
    )
    workloads = await db_client.call_tool("get_migratable_workloads", {})

    # 3. Build prompt
    user_prompt = f"""
Current date/time: {datetime.utcnow().isoformat()}

== ZONE ENERGY STATUS ==
{json.dumps(energy_status, indent=2)}

== NODE STATUS ==
{json.dumps(topology, indent=2)}

== MIGRATABLE WORKLOADS ==
{json.dumps(workloads, indent=2)}

== RECENT MIGRATION HISTORY ==
{json.dumps(history, indent=2)}

Analyze and decide which workloads to migrate.
"""

    # 4. Call LLM
    response = llm.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt}
        ],
        temperature=0.1,  # low temp for deterministic decisions
        response_format={"type": "json_object"}
    )

    decision_raw = response.choices[0].message.content
    decision = json.loads(decision_raw)

    # 5. Safety validation (hard rules enforced in code)
    validated_actions = []
    for action in decision.get("actions", []):
        feasibility = await k8s_client.call_tool("validate_migration_feasibility", {
            "cluster_id": action["cluster_id"],
            "namespace": action["namespace"],
            "workload_name": action["workload_name"],
            "workload_type": "Deployment",
            "destination_node_name": action["destination_node"]
        })
        if feasibility["feasible"]:
            validated_actions.append(action)

    decision["actions"] = validated_actions
    safety_passed = len(validated_actions) == len(decision.get("actions", []))

    # 6. Record decision
    decision_record = await db_client.call_tool("record_ai_decision", {
        "agent_run_id": run_id,
        "model_name": MODEL,
        "reasoning": decision.get("reasoning", ""),
        "decision_type": decision["decision_type"],
        "recommended_actions": validated_actions,
        "safety_check_passed": safety_passed
    })
    decision_id = decision_record["decision_id"]

    # 7. Execute validated migrations
    for action in validated_actions:
        migration = await k8s_client.call_tool("execute_migration", {
            "cluster_id": action["cluster_id"],
            "namespace": action["namespace"],
            "workload_name": action["workload_name"],
            "workload_type": "Deployment",
            "destination_node_name": action["destination_node"],
            "migration_type": "affinity"
        })
        await db_client.call_tool("record_migration_event", {
            "workload_id": action.get("workload_id"),
            "ai_decision_id": decision_id,
            "source_node_id": action.get("source_node_id"),
            "destination_node_id": action.get("destination_node_id"),
            "status": "in_progress",
            "trigger_reason": action["reason"]
        })

    await db_client.call_tool("complete_agent_run", {
        "agent_run_id": run_id,
        "migrations_initiated": len(validated_actions)
    })

    print(f"Cycle complete. Migrations initiated: {len(validated_actions)}")
```

---

## Why No Fine-Tuning Is Needed

This system uses the LLM purely as a **reasoning engine** with structured prompts:

1. All domain knowledge (rules, thresholds, zone data) is **injected at runtime** via the prompt
2. The model is instructed to output **structured JSON** — no free-form interpretation needed
3. Safety rules are **enforced in code** — the LLM's role is to rank and select, not to enforce
4. The prompt is designed to work with any capable instruction-tuned model

This means:
- ✅ No training data collection required
- ✅ No GPU cluster for training required
- ✅ Model can be swapped by changing one config value
- ✅ Behavior is adjusted by editing the system prompt, not retraining

---

## Model Upgrade Path

| Phase | Model | Rationale |
|-------|-------|-----------|
| Development | LLaMA 3.1 8B (Ollama local) | Fast iteration, low resource cost |
| Staging / Testing | LLaMA 3.3 70B (Ollama or Groq) | Higher reasoning quality |
| Production | LLaMA 3.3 70B (Ollama on GPU node) | Private, free, reliable |
| Optional upgrade | Gemini 2.0 Flash (Google AI Studio free tier) | Larger context, fast responses |
