# 🌿 Green Workload AI — Demo Guide

This guide walks you through running a full end-to-end demo of the Green Workload AI system: starting the services, simulating realistic energy and metric changes across multiple waves, and observing the AI agent's autonomous migration decisions in the live dashboard.

---

## Prerequisites

| Requirement | Notes |
|---|---|
| Python 3.11+ | With dependencies installed (`pip install -r requirements.txt`) |
| Node.js 18+ | For the web dashboard |
| MySQL 8.x | Running locally (default: `127.0.0.1:3306`) |
| Ollama or Copilot proxy | LLM endpoint configured in `.env` |
| `.env` file | Copy `.env.example` and fill in your values |

**First-time setup — initialise the database:**
```bash
python main.py --setup
```

---

## Step 1 — Start All Services

Run the start script from the project root. It launches both the AI agent scheduler and the web dashboard as background processes, writing logs under `.logs/`.

```bash
./start.sh
```

Expected output:
```
🌿 Green Workload AI — Starting Services
=========================================
[✔] AI Agent started (PID 12345) — logs: .logs/agent.log
[✔] Dashboard started (PID 12346) — http://localhost:3099

   Services:
   ─────────────────────────────────────────
   AI Agent     PID 12345    python main.py
   Dashboard    PID 12346    http://localhost:3099
   ─────────────────────────────────────────

   Stop all:  ./stop.sh
   View logs: tail -f .logs/agent.log .logs/dashboard.log
```

**Tail the agent logs in a separate terminal** to watch decisions in real time:
```bash
tail -f .logs/agent.log
```

**To stop everything:**
```bash
./stop.sh
```

---

## Step 2 — Run the Simulation Waves

`simulate_migration_triggers.py` injects realistic energy readings and node metrics into the database to drive different agent behaviours. It does **not** restart the agent — the running agent picks up the new data automatically on its next evaluation cycle.

### Reset to a clean baseline first

Always start a demo from a clean slate to avoid leftover data affecting decisions:

```bash
python simulate_migration_triggers.py --reset
```

This clears all simulation-generated rows (migrations, decisions, energy readings, node metrics) and re-seeds the database with the original topology.

---

### Wave overview

| Wave | Command | Conditions Simulated | Expected Agent Decision |
|------|---------|----------------------|------------------------|
| **0** | `--wave 0` | All zones moderate carbon (150–220 gCO₂/kWh), gap < 20% | `skip` — no migration needed |
| **1** | `--wave 1` | Dirty zones spike to 550–700 gCO₂/kWh, green zones drop to 60–90, nodes have capacity | `migrate` — carbon gap > 80% |
| **2** | `--wave 2` | Dirty zones still high, green nodes nearly full (72–85% utilisation) | partial `migrate` / `wait` |
| **3** | `--wave 3` | All green nodes overloaded (83–92%), dirty zones moderate | `skip` / `wait` — no safe destination |
| **4** | `--wave 4` | Green capacity recovered (30–45%), dirty zones extreme (800–980 gCO₂/kWh) | `migrate` — aggressive |

---

### Run individual waves

```bash
# Wave 0 — baseline: all zones similar carbon intensity
python simulate_migration_triggers.py --wave 0

# Wave 1 — big carbon gap: should trigger migrations
python simulate_migration_triggers.py --wave 1

# Wave 2 — green nodes near capacity: limited migrations
python simulate_migration_triggers.py --wave 2

# Wave 3 — all green nodes full: agent should wait/skip
python simulate_migration_triggers.py --wave 3

# Wave 4 — recovery: aggressive migration opportunity
python simulate_migration_triggers.py --wave 4
```

### Run all waves in sequence (full demo storyline)

```bash
python simulate_migration_triggers.py
```

Add `--pause N` to insert a delay (in seconds) between waves so the agent has time to process each one:

```bash
python simulate_migration_triggers.py --pause 30
```

### Run a specific subset of waves

```bash
python simulate_migration_triggers.py --wave 1,2,4
```

### Verify current state without inserting anything

```bash
python simulate_migration_triggers.py --verify
```

Prints a table of current zone energy readings and node utilisation metrics.

---

## Step 3 — Open the Dashboard

Open your browser to:

```
http://localhost:3099
```

The dashboard auto-refreshes every 3 seconds via Server-Sent Events (SSE). You will see:

### Summary cards
- Total migrations completed / in-progress / failed
- Total carbon saved (estimated gCO₂)
- Zone green/dirty split
- Node overload count

### Zones panel
Live energy status for all zones — carbon intensity (gCO₂/kWh), renewable percentage, and green/dirty classification. Watch these change as you inject waves.

### Nodes panel
Per-node CPU %, memory %, pod count, and energy zone. Destination nodes fill up as the agent migrates workloads onto them.

### Migrations panel
Every migration event with source node → destination node, carbon savings estimate, duration, and status. New rows appear within seconds of the agent executing a migration.

### AI Decisions panel
Full record of each LLM decision: decision type (`migrate` / `skip` / `wait`), model name, reasoning text, and the number of migrations that resulted from it. This is where you can see the agent's chain-of-thought for each wave.

### Workload Movement Log
Historical ledger of every workload relocation — which node and zone it moved from and to.

---

## Recommended Demo Script

For a live audience, run the waves in sequence with pauses:

```bash
# 1. Clean slate
python simulate_migration_triggers.py --reset

# 2. Show a stable baseline — agent does nothing
python simulate_migration_triggers.py --wave 0
#    → Watch dashboard: all zones moderate, AI Decisions shows "skip"

# 3. Inject carbon crisis — agent migrates workloads
python simulate_migration_triggers.py --wave 1
#    → Watch Migrations panel fill up, carbon savings increase

# 4. Congest green nodes — agent backs off
python simulate_migration_triggers.py --wave 2
#    → Partial migration or "wait" decision visible in AI Decisions

# 5. Fully saturate green nodes — agent waits
python simulate_migration_triggers.py --wave 3
#    → Node metrics show 83-92% utilisation, agent skips/waits

# 6. Recovery — agent resumes aggressive migration
python simulate_migration_triggers.py --wave 4
#    → New migrations kick off, total carbon saved jumps
```

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| Agent not starting | Missing `.env` or DB not running | Check `.logs/agent.log`, verify MySQL is up |
| Dashboard blank | Node.js not installed or port taken | Run `node web-dashboard/server.js` manually |
| No migrations after wave 1 | LLM unreachable | Check `OLLAMA_BASE_URL` in `.env`; agent falls back to rule-based engine |
| `--reset` errors | FK constraint violations | Ensure no other process holds DB transactions open |
| Port 3099 in use | Another process on that port | Set `DASHBOARD_PORT=3100` in `.env` and restart |

---

## Useful Commands

```bash
# Run a single agent cycle manually and exit
python main.py --once

# View agent logs live
tail -f .logs/agent.log

# View dashboard logs
tail -f .logs/dashboard.log

# Restart just the agent (after a code change)
./stop.sh && ./start.sh
```
