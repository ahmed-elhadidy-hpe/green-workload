# Green Workload AI — Setup Guide

## Prerequisites

| Requirement | Version |
|-------------|---------|
| Python | 3.10+ |
| MySQL | 8.0+ |
| Ollama | latest |
| kubectl (optional) | any |

---

## 1. Clone & Install Dependencies

```bash
cd /path/to/green-workload-ai
pip install -r requirements.txt
```

---

## 2. Ollama Setup (Local LLM)

```bash
# macOS
brew install ollama

# Linux
curl -fsSL https://ollama.com/install.sh | sh

# Pull the model (default: llama3.1:8b — ~4.7 GB)
ollama pull llama3.1:8b

# Start the Ollama server (keep this running in a separate terminal)
ollama serve
```

Ollama exposes an OpenAI-compatible API at `http://localhost:11434/v1`.

---

## 3. MySQL Setup

MySQL 8.0 must be running on `127.0.0.1:3306`.

```bash
# macOS (Homebrew)
brew install mysql@8.0
brew services start mysql@8.0

# Or use Docker
docker run -d --name mysql8 -p 3306:3306 -e MYSQL_ALLOW_EMPTY_PASSWORD=yes mysql:8.0
```

---

## 4. Configuration

Copy the example env file and edit as needed:

```bash
cp .env.example .env
```

Key settings in `.env`:

| Variable | Default | Description |
|----------|---------|-------------|
| `DB_HOST` | `127.0.0.1` | MySQL host |
| `DB_PORT` | `3306` | MySQL port |
| `DB_USER` | `root` | MySQL user |
| `DB_PASSWORD` | _(empty)_ | MySQL password |
| `DB_NAME` | `GREEN_WORKLOAD_DB` | Database name |
| `LLM_PROVIDER`|`ollama`| OPENAI compatible model interfacing provider currently supported values are `ollama` `copilot`  |
| `OLLAMA_BASE_URL` | `http://localhost:11434/v1` | Ollama API base URL |
| `OLLAMA_MODEL` | `llama3.1:8b` | Model name |
|`COPILOT_BASE_URL`|_(empty)_| the OPENAI compatible server created by `copilot-api`|
|`COPILOT_MODEL`|`claude-sonnet-4.6`| any model supported by your copilot subscription |
| `ELECTRICITY_MAPS_API_KEY` | _(empty)_ | Optional — live energy data |
| `WATTTIME_USERNAME` | _(empty)_ | Optional — live energy data |
| `WATTTIME_PASSWORD` | _(empty)_ | Optional — live energy data |
| `SCHEDULE_INTERVAL_SECONDS` | `10` | How often the agent runs |
| `DRY_RUN` | `false` | Set `true` to preview without executing |
| `MAX_CONCURRENT_MIGRATIONS` | `5` | Safety limit |
| `MIN_RENEWABLE_PCT` | `50.0` | Minimum % renewable for a zone to be "green" |

---

## 5. Database Setup

Run the schema creation and seed data script:

```bash
python setup_db.py
```

This will:
1. Create the `GREEN_WORKLOAD_DB` database if it doesn't exist
2. Apply `db/mysql_schema.sql` (all tables and views)
3. Insert seed data (1 region, 2 zones, 1 cluster, 2 nodes)

---

## 6. Running the Application

### Start the scheduler (continuous mode)

```bash
python main.py
```

The agent will run immediately and then every `SCHEDULE_INTERVAL_SECONDS` minutes.

### Run a single evaluation cycle

```bash
python main.py --once
```

### Dry-run mode (no actual k8s changes)

```bash
DRY_RUN=true python main.py --once
```

---

## 7. Running Individual MCP Servers (for testing/debugging)

Each MCP server can be run standalone in stdio mode:

```bash
# Green Energy MCP
python -m src.mcp_servers.green_energy.server

# Kubernetes MCP
python -m src.mcp_servers.kubernetes_mcp.server

# Internal DB MCP
python -m src.mcp_servers.internal_db.server
```

You can test them with the MCP CLI:

```bash
pip install mcp[cli]
mcp dev src/mcp_servers/green_energy/server.py
```

---

## 8. Verifying the Setup

```bash
# Check settings load correctly
python -c "from config.settings import settings; print('DB:', settings.DB_NAME, '| Model:', settings.OLLAMA_MODEL)"

# Check DB connectivity
python -c "
from src.database.repository import GreenWorkloadRepository
repo = GreenWorkloadRepository()
zones = repo.get_all_zones_with_energy()
print(f'Zones found: {len(zones)}')
for z in zones:
    print(f'  {z[\"zone_name\"]} ({z[\"zone_id\"]})')
"
```

---

## 9. Troubleshooting

### `Can't connect to MySQL server`
- Confirm MySQL is running: `mysql -u root -e "SELECT 1;"`
- Check `DB_HOST`, `DB_PORT`, `DB_USER`, `DB_PASSWORD` in `.env`

### `Connection refused` on Ollama
- Start Ollama: `ollama serve`
- Verify: `curl http://localhost:11434/v1/models`

### `ModuleNotFoundError`
- Ensure you run Python commands from the project root
- Reinstall: `pip install -r requirements.txt`

### `ERROR 1292` (Incorrect datetime value)
- Ensure MySQL `sql_mode` does not include `NO_ZERO_DATE` / `STRICT_TRANS_TABLES`
  or use the `.env` setting `DRY_RUN=true` for testing without DB writes

### Kubernetes client errors
- The K8s MCP server gracefully handles a missing kubeconfig — it returns `{"nodes": [], "error": "..."}`
- Set `KUBECONFIG` in `.env` to point to your cluster config

### LLM returns non-JSON output
- The agent has a `_rule_based_fallback` that activates automatically when LLM output cannot be parsed
- Try a larger model: `OLLAMA_MODEL=llama3.3:70b`

---

## Project Structure

```
green-workload-ai/
├── main.py                        # Entry point
├── setup_db.py                    # DB setup & seed data
├── requirements.txt
├── .env.example
├── config/
│   └── settings.py                # Pydantic settings
├── db/
│   ├── mysql_schema.sql           # MySQL 8.0 DDL
│   └── schema.sql                 # PostgreSQL reference schema
└── src/
    ├── database/
    │   ├── connection.py          # SQLAlchemy engine
    │   ├── models.py              # ORM models
    │   └── repository.py         # Data access layer
    ├── mcp_servers/
    │   ├── green_energy/          # Carbon intensity MCP
    │   ├── kubernetes_mcp/        # K8s operations MCP
    │   └── internal_db/          # DB query MCP
    ├── agent/
    │   ├── agent.py               # Main evaluation loop
    │   ├── prompts.py             # LLM prompt templates
    │   └── safety.py             # Hard safety rules
    └── scheduler/
        └── scheduler.py          # APScheduler wrapper
```
