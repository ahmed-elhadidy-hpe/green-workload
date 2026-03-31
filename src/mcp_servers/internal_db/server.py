import json
import structlog
from mcp.server.fastmcp import FastMCP
from src.database.repository import GreenWorkloadRepository

log = structlog.get_logger()
mcp = FastMCP("internal-db-mcp")
repo = GreenWorkloadRepository()


@mcp.tool()
def create_agent_run() -> dict:
    """Create a new agent run record. Returns run_id."""
    try:
        run_id = repo.create_agent_run()
        return {"agent_run_id": run_id, "status": "created"}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def complete_agent_run(
    agent_run_id: str, migrations_initiated: int = 0, status: str = "completed"
) -> dict:
    """Mark an agent run as complete."""
    try:
        repo.complete_agent_run(agent_run_id, migrations_initiated, status)
        return {"agent_run_id": agent_run_id, "status": status}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def get_cluster_topology(cluster_id: str = "") -> dict:
    """Get cluster topology including nodes, zones, and energy data."""
    try:
        topology = repo.get_cluster_topology(cluster_id if cluster_id else None)
        return topology
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def get_migratable_workloads(cluster_id: str = "") -> dict:
    """Get workloads on non-green zones that are eligible for migration."""
    try:
        workloads = repo.get_migratable_workloads(cluster_id if cluster_id else None)
        return {"workloads": workloads, "count": len(workloads)}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def get_migration_history(
    workload_id: str = "", node_id: str = "", hours_back: int = 24
) -> dict:
    """Get migration history."""
    try:
        history = repo.get_migration_history(
            workload_id if workload_id else None,
            node_id if node_id else None,
            hours_back,
        )
        return {"history": history, "count": len(history)}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def record_ai_decision(
    agent_run_id: str,
    decision_type: str,
    reasoning: str,
    recommended_actions: str,
    safety_check_passed: bool,
    model_name: str,
) -> dict:
    """Record an AI decision. recommended_actions should be JSON string."""
    try:
        actions = (
            json.loads(recommended_actions)
            if isinstance(recommended_actions, str)
            else recommended_actions
        )
        decision_id = repo.record_ai_decision(
            agent_run_id, decision_type, reasoning, actions, safety_check_passed, model_name
        )
        return {"decision_id": decision_id, "status": "recorded"}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def record_migration_event(
    workload_id: str,
    ai_decision_id: str,
    source_node_id: str,
    destination_node_id: str,
    status: str,
    trigger_reason: str,
) -> dict:
    """Record a migration event."""
    try:
        event_id = repo.record_migration_event(
            workload_id, ai_decision_id, source_node_id, destination_node_id, status, trigger_reason
        )
        return {"migration_event_id": event_id, "status": "recorded"}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def update_migration_status(
    migration_event_id: str, status: str, error_message: str = ""
) -> dict:
    """Update the status of a migration event."""
    try:
        repo.update_migration_status(
            migration_event_id, status, error_message if error_message else None
        )
        return {"migration_event_id": migration_event_id, "status": status}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def get_all_zones_with_energy() -> dict:
    """Get all zones with their latest energy readings."""
    try:
        zones = repo.get_all_zones_with_energy()
        return {"zones": zones, "count": len(zones)}
    except Exception as e:
        return {"error": str(e)}


if __name__ == "__main__":
    mcp.run()
