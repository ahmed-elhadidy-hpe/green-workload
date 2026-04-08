import structlog
from src.database.repository import GreenWorkloadRepository

log = structlog.get_logger()


class SafetyValidator:
    """Validates proposed migration actions against hard safety rules."""

    def __init__(self, repo: GreenWorkloadRepository, settings):
        self.repo = repo
        self.settings = settings

    def validate_action(self, action: dict, topology: dict) -> tuple[bool, str]:
        """
        Validate a proposed migration action against hard safety rules.
        Returns (True, "") if all pass, (False, reason) if any fail.
        """
        dest_node_name = action.get("destination_node_name", "")
        workload_type = action.get("workload_type", "Deployment")

        # Find destination node in topology by name
        dest_node = None
        clusters = topology.get("clusters", []) if isinstance(topology, dict) else []
        for cluster in clusters:
            for node in cluster.get("nodes", []):
                if (
                    node.get("node_name") == dest_node_name
                    or node.get("name") == dest_node_name
                ):
                    dest_node = node
                    break
            if dest_node:
                break

        if not dest_node and dest_node_name:
            # Try flat topology format
            nodes = topology.get("nodes", [])
            for node in nodes:
                if node.get("name") == dest_node_name or node.get("node_name") == dest_node_name:
                    dest_node = node
                    break

        if dest_node:
            # Rule 1: Destination node must be Ready and not cordoned
            node_status = dest_node.get("status", "Ready")
            if node_status != "Ready":
                return False, f"Destination node {dest_node_name} is not Ready (status: {node_status})"
            if dest_node.get("is_cordoned"):
                return False, f"Destination node {dest_node_name} is cordoned"

            # Rule 2: is_migration_target must be True and migration_opt_out must be False
            if not dest_node.get("is_migration_target", True):
                return False, f"Destination node {dest_node_name} is not a migration target"
            if dest_node.get("migration_opt_out", False):
                return False, f"Destination node {dest_node_name} has opted out of migrations"

            # Rule 3: CPU and memory below threshold
            cpu_pct = dest_node.get("cpu_usage_percent", 0) or 0
            mem_pct = dest_node.get("memory_usage_percent", 0) or 0
            if cpu_pct > self.settings.NODE_CPU_THRESHOLD:
                return (
                    False,
                    f"Destination node {dest_node_name} CPU at {cpu_pct}% "
                    f"(threshold: {self.settings.NODE_CPU_THRESHOLD}%)",
                )
            if mem_pct > self.settings.NODE_MEMORY_THRESHOLD:
                return (
                    False,
                    f"Destination node {dest_node_name} memory at {mem_pct}% "
                    f"(threshold: {self.settings.NODE_MEMORY_THRESHOLD}%)",
                )

        # Rule 4: Max concurrent migrations
        try:
            in_progress = self.repo.get_in_progress_migrations_count()
            if in_progress >= self.settings.MAX_CONCURRENT_MIGRATIONS:
                return (
                    False,
                    f"Max concurrent migrations reached ({in_progress}/{self.settings.MAX_CONCURRENT_MIGRATIONS})",
                )
        except Exception as e:
            log.warning("Could not check in-progress migrations", error=str(e))

        # Rule 5: StatefulSet must have opt-in annotation
        if workload_type.lower() == "statefulset":
            annotations = action.get("annotations", {})
            if annotations.get("green-workload/migration-allowed") != "true":
                return (
                    False,
                    "StatefulSet migration requires annotation 'green-workload/migration-allowed: true'",
                )

        return True, ""
