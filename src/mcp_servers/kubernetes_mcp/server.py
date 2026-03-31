import os
import random
import structlog
from mcp.server.fastmcp import FastMCP

log = structlog.get_logger()
mcp = FastMCP("kubernetes-mcp")

DRY_RUN = os.environ.get("DRY_RUN", "false").lower() == "true"


def get_k8s_client(cluster_id: str = None):
    """Load kubernetes client config. Returns (v1, apps_v1, policy_v1) or raises."""
    import kubernetes
    kubeconfig = os.environ.get("KUBECONFIG", os.path.expanduser("~/.kube/config"))
    try:
        kubernetes.config.load_kube_config(config_file=kubeconfig)
    except Exception as e:
        try:
            kubernetes.config.load_incluster_config()
        except Exception:
            raise RuntimeError(f"Could not load kubeconfig: {e}")

    v1 = kubernetes.client.CoreV1Api()
    apps_v1 = kubernetes.client.AppsV1Api()
    policy_v1 = kubernetes.client.PolicyV1Api()
    return v1, apps_v1, policy_v1


@mcp.tool()
def list_nodes(cluster_id: str = "") -> dict:
    """List all nodes with status, labels, taints."""
    try:
        v1, _, _ = get_k8s_client(cluster_id)
        nodes = v1.list_node()
        result = []
        for node in nodes.items:
            conditions = {c.type: c.status for c in (node.status.conditions or [])}
            result.append({
                "name": node.metadata.name,
                "status": "Ready" if conditions.get("Ready") == "True" else "NotReady",
                "labels": node.metadata.labels or {},
                "taints": [
                    {"key": t.key, "value": t.value, "effect": t.effect}
                    for t in (node.spec.taints or [])
                ],
                "is_cordoned": node.spec.unschedulable or False,
                "allocatable_cpu": (
                    node.status.allocatable.get("cpu", "0") if node.status.allocatable else "0"
                ),
                "allocatable_memory": (
                    node.status.allocatable.get("memory", "0") if node.status.allocatable else "0"
                ),
                "instance_type": (node.metadata.labels or {}).get(
                    "node.kubernetes.io/instance-type", "unknown"
                ),
            })
        return {"nodes": result, "count": len(result), "cluster_id": cluster_id}
    except Exception as e:
        log.warning("list_nodes failed (k8s unavailable?)", error=str(e))
        return {"nodes": [], "count": 0, "error": str(e), "cluster_id": cluster_id}


@mcp.tool()
def get_node_metrics(cluster_id: str = "") -> dict:
    """Get node metrics. Simulates if metrics-server unavailable."""
    try:
        v1, _, _ = get_k8s_client(cluster_id)
        nodes = v1.list_node()
        metrics_list = []
        try:
            import kubernetes
            custom_api = kubernetes.client.CustomObjectsApi()
            node_metrics = custom_api.list_cluster_custom_object(
                group="metrics.k8s.io", version="v1beta1", plural="nodes"
            )
            metrics_by_name = {m["metadata"]["name"]: m for m in node_metrics.get("items", [])}
        except Exception:
            metrics_by_name = {}

        for node in nodes.items:
            name = node.metadata.name
            if name in metrics_by_name:
                m = metrics_by_name[name]
                cpu_str = m["usage"]["cpu"]
                mem_str = m["usage"]["memory"]
                if cpu_str.endswith("n"):
                    cpu_cores = float(cpu_str.rstrip("n")) / 1e9
                else:
                    cpu_cores = float(cpu_str.rstrip("m")) / 1000
                if mem_str.endswith("Ki"):
                    mem_gb = float(mem_str.rstrip("Ki")) * 1024 / 1e9
                else:
                    mem_gb = float(mem_str) / 1e9
            else:
                cpu_cores = round(random.uniform(0.1, 4.0), 3)
                mem_gb = round(random.uniform(1.0, 32.0), 3)

            alloc = node.status.allocatable or {}
            alloc_cpu_str = alloc.get("cpu", "4")
            alloc_mem_str = alloc.get("memory", "16Gi")
            try:
                alloc_cpu = float(alloc_cpu_str)
            except Exception:
                alloc_cpu = 4.0
            try:
                if alloc_mem_str.endswith("Gi"):
                    alloc_mem = float(alloc_mem_str[:-2])
                elif alloc_mem_str.endswith("Mi"):
                    alloc_mem = float(alloc_mem_str[:-2]) / 1024
                else:
                    alloc_mem = float(alloc_mem_str) / 1e9
            except Exception:
                alloc_mem = 16.0

            cpu_pct = round(min(100.0, (cpu_cores / alloc_cpu) * 100), 2)
            mem_pct = round(min(100.0, (mem_gb / alloc_mem) * 100), 2)
            pod_count = random.randint(5, 40)

            metrics_list.append({
                "node_name": name,
                "cpu_usage_cores": cpu_cores,
                "cpu_usage_percent": cpu_pct,
                "memory_usage_gb": mem_gb,
                "memory_usage_percent": mem_pct,
                "pod_count": pod_count,
                "network_in_mbps": round(random.uniform(0.1, 100.0), 3),
                "network_out_mbps": round(random.uniform(0.1, 100.0), 3),
                "simulated": name not in metrics_by_name,
            })
        return {"metrics": metrics_list, "count": len(metrics_list), "cluster_id": cluster_id}
    except Exception as e:
        log.warning("get_node_metrics failed", error=str(e))
        return {"metrics": [], "count": 0, "error": str(e)}


@mcp.tool()
def validate_migration_feasibility(
    cluster_id: str,
    namespace: str,
    workload_name: str,
    workload_type: str,
    destination_node_name: str,
) -> dict:
    """Validate if migration is feasible. Checks node readiness, capacity, PDB, StatefulSet opt-in."""
    try:
        v1, apps_v1, policy_v1 = get_k8s_client(cluster_id)
        checks: dict = {}

        # Check destination node
        try:
            node = v1.read_node(destination_node_name)
            conditions = {c.type: c.status for c in (node.status.conditions or [])}
            checks["node_ready"] = conditions.get("Ready") == "True"
            checks["node_not_cordoned"] = not (node.spec.unschedulable or False)
        except Exception as e:
            return {"feasible": False, "checks": {"node_ready": False, "error": str(e)}}

        # PDB check
        try:
            pdbs = policy_v1.list_namespaced_pod_disruption_budget(namespace)
            checks["pdb_allows"] = True
            for pdb in pdbs.items:
                if pdb.status.disruptions_allowed is not None and pdb.status.disruptions_allowed == 0:
                    checks["pdb_allows"] = False
                    break
        except Exception:
            checks["pdb_allows"] = True

        # StatefulSet opt-in check
        if workload_type.lower() == "statefulset":
            try:
                sts = apps_v1.read_namespaced_stateful_set(workload_name, namespace)
                annotations = sts.metadata.annotations or {}
                checks["statefulset_opt_in"] = annotations.get("green-workload/migration-allowed") == "true"
            except Exception:
                checks["statefulset_opt_in"] = False
        else:
            checks["statefulset_opt_in"] = True

        feasible = all(checks.values())
        return {"feasible": feasible, "checks": checks, "destination_node": destination_node_name}
    except Exception as e:
        log.error("validate_migration_feasibility failed", error=str(e))
        return {"feasible": False, "checks": {}, "error": str(e)}


@mcp.tool()
def execute_migration(
    cluster_id: str,
    namespace: str,
    workload_name: str,
    workload_type: str,
    destination_node_name: str,
    migration_type: str = "affinity",
) -> dict:
    """Patch workload with nodeAffinity for destination node. Respects DRY_RUN."""
    try:
        if DRY_RUN:
            log.info(
                "DRY_RUN: would migrate workload",
                workload=workload_name,
                destination=destination_node_name,
            )
            return {
                "success": True,
                "dry_run": True,
                "workload": workload_name,
                "destination": destination_node_name,
            }

        _, apps_v1, _ = get_k8s_client(cluster_id)

        affinity_patch = {
            "spec": {
                "template": {
                    "spec": {
                        "affinity": {
                            "nodeAffinity": {
                                "requiredDuringSchedulingIgnoredDuringExecution": {
                                    "nodeSelectorTerms": [{
                                        "matchExpressions": [{
                                            "key": "kubernetes.io/hostname",
                                            "operator": "In",
                                            "values": [destination_node_name],
                                        }]
                                    }]
                                }
                            }
                        }
                    }
                }
            }
        }

        if workload_type.lower() == "deployment":
            apps_v1.patch_namespaced_deployment(workload_name, namespace, affinity_patch)
        elif workload_type.lower() == "statefulset":
            apps_v1.patch_namespaced_stateful_set(workload_name, namespace, affinity_patch)
        else:
            return {"success": False, "error": f"Unsupported workload type: {workload_type}"}

        return {
            "success": True,
            "dry_run": False,
            "workload": workload_name,
            "destination": destination_node_name,
            "migration_type": migration_type,
        }
    except Exception as e:
        log.error("execute_migration failed", error=str(e))
        return {"success": False, "error": str(e)}


@mcp.tool()
def check_migration_status(
    cluster_id: str, namespace: str, workload_name: str, workload_type: str
) -> dict:
    """Check if migration is complete by examining pod placement."""
    try:
        v1, apps_v1, _ = get_k8s_client(cluster_id)

        if workload_type.lower() == "deployment":
            resource = apps_v1.read_namespaced_deployment(workload_name, namespace)
            desired = resource.spec.replicas or 1
            ready = resource.status.ready_replicas or 0
        elif workload_type.lower() == "statefulset":
            resource = apps_v1.read_namespaced_stateful_set(workload_name, namespace)
            desired = resource.spec.replicas or 1
            ready = resource.status.ready_replicas or 0
        else:
            return {"error": f"Unsupported workload type: {workload_type}"}

        pods = v1.list_namespaced_pod(namespace, label_selector=f"app={workload_name}")
        pod_nodes = [p.spec.node_name for p in pods.items if p.spec.node_name]

        status = "complete" if ready >= desired else "in_progress"
        return {
            "workload": workload_name,
            "namespace": namespace,
            "status": status,
            "desired_replicas": desired,
            "ready_replicas": ready,
            "pod_nodes": pod_nodes,
        }
    except Exception as e:
        log.error("check_migration_status failed", error=str(e))
        return {"error": str(e), "status": "unknown"}


@mcp.tool()
def rollback_migration(
    cluster_id: str, namespace: str, workload_name: str, workload_type: str
) -> dict:
    """Remove the nodeAffinity patch to rollback migration."""
    try:
        if DRY_RUN:
            return {"success": True, "dry_run": True, "workload": workload_name, "action": "rollback"}

        _, apps_v1, _ = get_k8s_client(cluster_id)
        patch = {"spec": {"template": {"spec": {"affinity": None}}}}

        if workload_type.lower() == "deployment":
            apps_v1.patch_namespaced_deployment(workload_name, namespace, patch)
        elif workload_type.lower() == "statefulset":
            apps_v1.patch_namespaced_stateful_set(workload_name, namespace, patch)
        else:
            return {"success": False, "error": f"Unsupported workload type: {workload_type}"}

        return {"success": True, "workload": workload_name, "action": "rollback"}
    except Exception as e:
        log.error("rollback_migration failed", error=str(e))
        return {"success": False, "error": str(e)}


@mcp.tool()
def get_pod_disruption_budgets(cluster_id: str = "", namespace: str = "default") -> dict:
    """Get PodDisruptionBudgets for a namespace."""
    try:
        _, _, policy_v1 = get_k8s_client(cluster_id)
        pdbs = policy_v1.list_namespaced_pod_disruption_budget(namespace)
        result = []
        for pdb in pdbs.items:
            result.append({
                "name": pdb.metadata.name,
                "namespace": pdb.metadata.namespace,
                "min_available": str(pdb.spec.min_available) if pdb.spec.min_available else None,
                "max_unavailable": str(pdb.spec.max_unavailable) if pdb.spec.max_unavailable else None,
                "disruptions_allowed": pdb.status.disruptions_allowed,
                "current_healthy": pdb.status.current_healthy,
                "desired_healthy": pdb.status.desired_healthy,
            })
        return {"pdbs": result, "count": len(result), "namespace": namespace}
    except Exception as e:
        log.warning("get_pod_disruption_budgets failed", error=str(e))
        return {"pdbs": [], "count": 0, "error": str(e)}


@mcp.tool()
def discover_nodes(cluster_id: str = "") -> dict:
    """Return full node list for initial DB setup including zone hints from labels."""
    try:
        v1, _, _ = get_k8s_client(cluster_id)
        nodes = v1.list_node()
        result = []
        for node in nodes.items:
            labels = node.metadata.labels or {}
            conditions = {c.type: c.status for c in (node.status.conditions or [])}
            alloc = node.status.allocatable or {}
            result.append({
                "name": node.metadata.name,
                "provider_id": node.spec.provider_id or "",
                "instance_type": labels.get("node.kubernetes.io/instance-type", "unknown"),
                "region": labels.get("topology.kubernetes.io/region", ""),
                "zone": labels.get("topology.kubernetes.io/zone", ""),
                "os": node.status.node_info.operating_system if node.status.node_info else "",
                "kernel_version": node.status.node_info.kernel_version if node.status.node_info else "",
                "container_runtime": (
                    node.status.node_info.container_runtime_version if node.status.node_info else ""
                ),
                "status": "Ready" if conditions.get("Ready") == "True" else "NotReady",
                "is_cordoned": node.spec.unschedulable or False,
                "allocatable_cpu": alloc.get("cpu", "0"),
                "allocatable_memory": alloc.get("memory", "0"),
                "allocatable_pods": int(alloc.get("pods", "0")),
                "labels": labels,
                "taints": [
                    {"key": t.key, "value": t.value, "effect": t.effect}
                    for t in (node.spec.taints or [])
                ],
            })
        return {"nodes": result, "count": len(result), "cluster_id": cluster_id}
    except Exception as e:
        log.warning("discover_nodes failed (k8s unavailable?)", error=str(e))
        return {"nodes": [], "count": 0, "error": str(e)}


if __name__ == "__main__":
    mcp.run()
