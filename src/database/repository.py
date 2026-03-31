import uuid
import json
from datetime import datetime, timedelta
from typing import Optional

import structlog
from sqlalchemy import text

from src.database.connection import get_db
from src.database.models import (
    Region, Zone, Cluster, Node, EnergyReading, NodeMetric,
    Workload, AgentRun, AiDecision, MigrationEvent,
)

log = structlog.get_logger()


class GreenWorkloadRepository:
    """Repository for all database operations."""

    # ------------------------------------------------------------------
    # Agent runs
    # ------------------------------------------------------------------

    def create_agent_run(self) -> str:
        """Create a new agent run record and return the run_id."""
        with get_db() as db:
            run = AgentRun(
                id=str(uuid.uuid4()),
                started_at=datetime.utcnow(),
                status="running",
            )
            db.add(run)
            db.flush()
            return run.id

    def complete_agent_run(
        self, run_id: str, migrations_initiated: int, status: str = "completed"
    ) -> None:
        """Mark an agent run as complete."""
        try:
            with get_db() as db:
                db.execute(
                    text(
                        "UPDATE agent_runs SET status=:status, completed_at=:now, "
                        "migrations_initiated=:mi WHERE id=:id"
                    ),
                    {
                        "status": status,
                        "now": datetime.utcnow(),
                        "mi": migrations_initiated,
                        "id": run_id,
                    },
                )
        except Exception as e:
            log.error("complete_agent_run failed", run_id=run_id, error=str(e))
            raise

    # ------------------------------------------------------------------
    # Energy / zones
    # ------------------------------------------------------------------

    def get_all_zones_with_energy(self) -> list[dict]:
        """Get all zones with their latest energy reading."""
        try:
            with get_db() as db:
                rows = db.execute(
                    text("""
                        SELECT z.id as zone_id, z.name as zone_name,
                               z.electricitymap_zone, z.watttime_ba,
                               r.name as region_name,
                               e.carbon_intensity, e.renewable_percentage, e.is_green,
                               e.energy_sources, e.timestamp as energy_timestamp
                        FROM zones z
                        JOIN regions r ON z.region_id = r.id
                        LEFT JOIN energy_readings e ON e.id = (
                            SELECT id FROM energy_readings er2
                            WHERE er2.zone_id = z.id
                            ORDER BY er2.timestamp DESC LIMIT 1
                        )
                    """)
                ).fetchall()
                return [dict(row._mapping) for row in rows]
        except Exception as e:
            log.error("get_all_zones_with_energy failed", error=str(e))
            return []

    def upsert_energy_reading(self, zone_id: str, data: dict) -> None:
        """Insert a new energy reading for a zone."""
        try:
            with get_db() as db:
                ts_raw = data.get("timestamp")
                if isinstance(ts_raw, str):
                    try:
                        ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
                    except ValueError:
                        ts = datetime.utcnow()
                elif isinstance(ts_raw, datetime):
                    ts = ts_raw
                else:
                    ts = datetime.utcnow()

                # Remove timezone info for MySQL DATETIME
                if hasattr(ts, "tzinfo") and ts.tzinfo is not None:
                    ts = ts.replace(tzinfo=None)

                energy_sources = data.get("energy_sources")
                if isinstance(energy_sources, dict):
                    energy_sources = json.dumps(energy_sources)

                reading = EnergyReading(
                    id=str(uuid.uuid4()),
                    zone_id=zone_id,
                    timestamp=ts,
                    carbon_intensity=data.get("carbon_intensity"),
                    renewable_percentage=data.get("renewable_percentage"),
                    energy_sources=data.get("energy_sources"),
                    data_source=data.get("data_source", "api"),
                    data_quality=data.get("data_quality", "live"),
                )
                db.add(reading)
        except Exception as e:
            log.error("upsert_energy_reading failed", zone_id=zone_id, error=str(e))
            raise

    # ------------------------------------------------------------------
    # Cluster topology
    # ------------------------------------------------------------------

    def get_cluster_topology(self, cluster_id: Optional[str] = None) -> dict:
        """Get cluster topology including nodes, zones, and latest energy/metrics."""
        try:
            with get_db() as db:
                cluster_filter = "AND c.id = :cluster_id" if cluster_id else ""
                rows = db.execute(
                    text(f"""
                        SELECT
                            n.id as node_id, n.name as node_name,
                            n.status, n.is_cordoned, n.is_migration_target,
                            n.migration_opt_out, n.instance_type,
                            n.allocatable_cpu, n.allocatable_memory_gb,
                            c.id as cluster_id, c.name as cluster_name,
                            z.id as zone_id, z.name as zone_name,
                            z.electricitymap_zone,
                            nm.cpu_usage_percent, nm.memory_usage_percent,
                            nm.cpu_usage_cores, nm.memory_usage_gb, nm.pod_count,
                            nm.is_overloaded,
                            e.carbon_intensity, e.renewable_percentage, e.is_green,
                            e.timestamp as energy_timestamp
                        FROM nodes n
                        JOIN clusters c ON n.cluster_id = c.id
                        LEFT JOIN zones z ON n.zone_id = z.id
                        LEFT JOIN node_metrics nm ON nm.id = (
                            SELECT id FROM node_metrics nm2
                            WHERE nm2.node_id = n.id
                            ORDER BY nm2.timestamp DESC LIMIT 1
                        )
                        LEFT JOIN energy_readings e ON e.id = (
                            SELECT id FROM energy_readings er2
                            WHERE er2.zone_id = z.id
                            ORDER BY er2.timestamp DESC LIMIT 1
                        )
                        WHERE 1=1 {cluster_filter}
                        ORDER BY c.id, n.name
                    """),
                    {"cluster_id": cluster_id} if cluster_id else {},
                ).fetchall()

                clusters: dict[str, dict] = {}
                for row in rows:
                    r = dict(row._mapping)
                    cid = r["cluster_id"]
                    if cid not in clusters:
                        clusters[cid] = {
                            "cluster_id": cid,
                            "cluster_name": r["cluster_name"],
                            "nodes": [],
                        }
                    clusters[cid]["nodes"].append(r)

                return {"clusters": list(clusters.values()), "node_count": len(rows)}
        except Exception as e:
            log.error("get_cluster_topology failed", error=str(e))
            return {"clusters": [], "node_count": 0, "error": str(e)}

    # ------------------------------------------------------------------
    # Workloads
    # ------------------------------------------------------------------

    def get_migratable_workloads(self, cluster_id: Optional[str] = None) -> list[dict]:
        """Get workloads on non-green zones eligible for migration."""
        try:
            with get_db() as db:
                cluster_filter = "AND w.cluster_id = :cluster_id" if cluster_id else ""
                rows = db.execute(
                    text(f"""
                        SELECT
                            w.id as workload_id, w.name as workload_name,
                            w.namespace, w.workload_type, w.replica_count,
                            w.migration_allowed, w.stateful, w.labels, w.annotations,
                            w.cluster_id, c.name as cluster_name,
                            n.id as node_id, n.name as node_name,
                            z.id as zone_id, z.name as zone_name,
                            e.carbon_intensity, e.renewable_percentage, e.is_green
                        FROM workloads w
                        JOIN clusters c ON w.cluster_id = c.id
                        LEFT JOIN nodes n ON w.current_node_id = n.id
                        LEFT JOIN zones z ON n.zone_id = z.id
                        LEFT JOIN energy_readings e ON e.id = (
                            SELECT id FROM energy_readings er2
                            WHERE er2.zone_id = z.id
                            ORDER BY er2.timestamp DESC LIMIT 1
                        )
                        WHERE w.migration_allowed = 1
                          AND (e.is_green = 0 OR e.is_green IS NULL)
                          {cluster_filter}
                        ORDER BY e.carbon_intensity DESC
                    """),
                    {"cluster_id": cluster_id} if cluster_id else {},
                ).fetchall()
                return [dict(row._mapping) for row in rows]
        except Exception as e:
            log.error("get_migratable_workloads failed", error=str(e))
            return []

    def upsert_workload(
        self,
        name: str,
        namespace: str,
        cluster_id: str,
        workload_type: str,
        replica_count: int,
        labels: dict,
        annotations: dict,
    ) -> str:
        """Insert or update a workload record. Returns workload id."""
        try:
            with get_db() as db:
                row = db.execute(
                    text(
                        "SELECT id FROM workloads WHERE name=:n AND namespace=:ns AND cluster_id=:cid"
                    ),
                    {"n": name, "ns": namespace, "cid": cluster_id},
                ).fetchone()

                if row:
                    wid = row[0]
                    db.execute(
                        text(
                            "UPDATE workloads SET workload_type=:wt, replica_count=:rc, "
                            "labels=:lb, annotations=:an, last_seen_at=:now WHERE id=:id"
                        ),
                        {
                            "wt": workload_type,
                            "rc": replica_count,
                            "lb": json.dumps(labels),
                            "an": json.dumps(annotations),
                            "now": datetime.utcnow(),
                            "id": wid,
                        },
                    )
                    return wid
                else:
                    wid = str(uuid.uuid4())
                    wl = Workload(
                        id=wid,
                        name=name,
                        namespace=namespace,
                        cluster_id=cluster_id,
                        workload_type=workload_type,
                        replica_count=replica_count,
                        labels=labels,
                        annotations=annotations,
                        last_seen_at=datetime.utcnow(),
                    )
                    db.add(wl)
                    db.flush()
                    return wid
        except Exception as e:
            log.error("upsert_workload failed", name=name, error=str(e))
            raise

    # ------------------------------------------------------------------
    # Node operations
    # ------------------------------------------------------------------

    def register_cluster(
        self, name: str, api_endpoint: str, region_id: str, managed_by: str
    ) -> str:
        """Register or update a cluster. Returns cluster id."""
        try:
            with get_db() as db:
                row = db.execute(
                    text("SELECT id FROM clusters WHERE name=:n"), {"n": name}
                ).fetchone()
                if row:
                    return row[0]
                cid = str(uuid.uuid4())
                db.add(
                    Cluster(
                        id=cid,
                        name=name,
                        api_endpoint=api_endpoint,
                        region_id=region_id,
                        managed_by=managed_by,
                    )
                )
                db.flush()
                return cid
        except Exception as e:
            log.error("register_cluster failed", name=name, error=str(e))
            raise

    def register_node(
        self,
        name: str,
        cluster_id: str,
        zone_id: Optional[str],
        instance_type: str,
        labels: dict,
        taints: list,
    ) -> str:
        """Register or update a node. Returns node id."""
        try:
            with get_db() as db:
                row = db.execute(
                    text("SELECT id FROM nodes WHERE name=:n AND cluster_id=:cid"),
                    {"n": name, "cid": cluster_id},
                ).fetchone()
                if row:
                    return row[0]
                nid = str(uuid.uuid4())
                db.add(
                    Node(
                        id=nid,
                        name=name,
                        cluster_id=cluster_id,
                        zone_id=zone_id,
                        instance_type=instance_type,
                        labels=labels,
                        taints=taints,
                    )
                )
                db.flush()
                return nid
        except Exception as e:
            log.error("register_node failed", name=name, error=str(e))
            raise

    def update_node_status(
        self,
        node_name: str,
        cluster_id: str,
        status: str,
        is_cordoned: bool,
        cpu_pct: float,
        mem_pct: float,
        pod_count: int,
    ) -> None:
        """Update node status and insert a new metric row."""
        try:
            with get_db() as db:
                row = db.execute(
                    text("SELECT id FROM nodes WHERE name=:n AND cluster_id=:cid"),
                    {"n": node_name, "cid": cluster_id},
                ).fetchone()
                if not row:
                    log.warning("update_node_status: node not found", node=node_name)
                    return
                node_id = row[0]
                db.execute(
                    text(
                        "UPDATE nodes SET status=:s, is_cordoned=:ic WHERE id=:id"
                    ),
                    {"s": status, "ic": is_cordoned, "id": node_id},
                )
                metric = NodeMetric(
                    id=str(uuid.uuid4()),
                    node_id=node_id,
                    timestamp=datetime.utcnow(),
                    cpu_usage_percent=cpu_pct,
                    memory_usage_percent=mem_pct,
                    pod_count=pod_count,
                )
                db.add(metric)
        except Exception as e:
            log.error("update_node_status failed", node=node_name, error=str(e))
            raise

    def get_node_by_name(self, node_name: str, cluster_id: str) -> Optional[dict]:
        """Get a node record by name and cluster id."""
        try:
            with get_db() as db:
                row = db.execute(
                    text("SELECT * FROM nodes WHERE name=:n AND cluster_id=:cid"),
                    {"n": node_name, "cid": cluster_id},
                ).fetchone()
                if row:
                    return dict(row._mapping)
                return None
        except Exception as e:
            log.error("get_node_by_name failed", node=node_name, error=str(e))
            return None

    def bulk_insert_node_metrics(self, cluster_id: str, metrics: list[dict]) -> None:
        """Bulk insert node metrics for a cluster."""
        try:
            with get_db() as db:
                for m in metrics:
                    node_name = m.get("node_name", "")
                    row = db.execute(
                        text("SELECT id FROM nodes WHERE name=:n AND cluster_id=:cid"),
                        {"n": node_name, "cid": cluster_id},
                    ).fetchone()
                    if not row:
                        continue
                    node_id = row[0]
                    metric = NodeMetric(
                        id=str(uuid.uuid4()),
                        node_id=node_id,
                        timestamp=datetime.utcnow(),
                        cpu_usage_cores=m.get("cpu_usage_cores"),
                        cpu_usage_percent=m.get("cpu_usage_percent"),
                        memory_usage_gb=m.get("memory_usage_gb"),
                        memory_usage_percent=m.get("memory_usage_percent"),
                        pod_count=m.get("pod_count"),
                        network_in_mbps=m.get("network_in_mbps"),
                        network_out_mbps=m.get("network_out_mbps"),
                    )
                    db.add(metric)
        except Exception as e:
            log.error("bulk_insert_node_metrics failed", cluster_id=cluster_id, error=str(e))
            raise

    # ------------------------------------------------------------------
    # AI decisions & migration events
    # ------------------------------------------------------------------

    def record_ai_decision(
        self,
        agent_run_id: str,
        decision_type: str,
        reasoning: str,
        recommended_actions: list,
        safety_check_passed: bool,
        model_name: str,
    ) -> str:
        """Record an AI decision and return its id."""
        try:
            with get_db() as db:
                did = str(uuid.uuid4())
                decision = AiDecision(
                    id=did,
                    agent_run_id=agent_run_id,
                    timestamp=datetime.utcnow(),
                    model_name=model_name,
                    reasoning=reasoning,
                    decision_type=decision_type,
                    recommended_actions=recommended_actions,
                    safety_check_passed=safety_check_passed,
                )
                db.add(decision)
                db.flush()
                return did
        except Exception as e:
            log.error("record_ai_decision failed", error=str(e))
            raise

    def record_migration_event(
        self,
        workload_id: str,
        ai_decision_id: str,
        source_node_id: Optional[str],
        destination_node_id: Optional[str],
        status: str,
        trigger_reason: str,
    ) -> str:
        """Record a migration event and return its id."""
        try:
            with get_db() as db:
                # Look up workload details
                wl_row = db.execute(
                    text("SELECT name, namespace, cluster_id FROM workloads WHERE id=:id"),
                    {"id": workload_id},
                ).fetchone()
                wl_name = wl_row[0] if wl_row else "unknown"
                wl_ns = wl_row[1] if wl_row else "default"
                wl_cluster = wl_row[2] if wl_row else None

                mid = str(uuid.uuid4())
                event = MigrationEvent(
                    id=mid,
                    workload_id=workload_id,
                    workload_name=wl_name,
                    namespace=wl_ns,
                    cluster_id=wl_cluster,
                    source_node_id=source_node_id or None,
                    destination_node_id=destination_node_id or None,
                    ai_decision_id=ai_decision_id or None,
                    status=status,
                    trigger_reason=trigger_reason,
                    started_at=datetime.utcnow(),
                )
                db.add(event)
                db.flush()
                return mid
        except Exception as e:
            log.error("record_migration_event failed", error=str(e))
            raise

    def update_migration_status(
        self,
        migration_event_id: str,
        status: str,
        error_message: Optional[str] = None,
    ) -> None:
        """Update the status of a migration event."""
        try:
            with get_db() as db:
                params: dict = {"status": status, "id": migration_event_id}
                extra = ""
                if status in ("completed", "failed", "rolled_back"):
                    extra = ", completed_at=:now"
                    params["now"] = datetime.utcnow()
                if error_message:
                    extra += ", error_message=:err"
                    params["err"] = error_message
                db.execute(
                    text(f"UPDATE migration_events SET status=:status{extra} WHERE id=:id"),
                    params,
                )
        except Exception as e:
            log.error("update_migration_status failed", id=migration_event_id, error=str(e))
            raise

    def get_migration_history(
        self,
        workload_id: Optional[str] = None,
        node_id: Optional[str] = None,
        hours_back: int = 24,
    ) -> list[dict]:
        """Get migration history filtered by workload, node, and time window."""
        try:
            with get_db() as db:
                since = datetime.utcnow() - timedelta(hours=hours_back)
                conditions = ["me.created_at >= :since"]
                params: dict = {"since": since}
                if workload_id:
                    conditions.append("me.workload_id = :wid")
                    params["wid"] = workload_id
                if node_id:
                    conditions.append(
                        "(me.source_node_id = :nid OR me.destination_node_id = :nid)"
                    )
                    params["nid"] = node_id
                where = " AND ".join(conditions)
                rows = db.execute(
                    text(f"""
                        SELECT me.*, sn.name as source_node_name, dn.name as dest_node_name
                        FROM migration_events me
                        LEFT JOIN nodes sn ON me.source_node_id = sn.id
                        LEFT JOIN nodes dn ON me.destination_node_id = dn.id
                        WHERE {where}
                        ORDER BY me.created_at DESC
                    """),
                    params,
                ).fetchall()
                return [dict(row._mapping) for row in rows]
        except Exception as e:
            log.error("get_migration_history failed", error=str(e))
            return []

    def get_in_progress_migrations_count(self) -> int:
        """Return the count of currently in-progress migrations."""
        try:
            with get_db() as db:
                row = db.execute(
                    text("SELECT COUNT(*) FROM migration_events WHERE status='in_progress'")
                ).fetchone()
                return row[0] if row else 0
        except Exception as e:
            log.error("get_in_progress_migrations_count failed", error=str(e))
            return 0
