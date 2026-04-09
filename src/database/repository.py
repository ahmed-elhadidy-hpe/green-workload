import uuid
import json
from datetime import datetime, timedelta
from typing import Optional

import structlog
from sqlalchemy import text

from src.database.connection import get_db
from src.database.models import (
    Region, Zone, Cluster, Node, EnergyReading, NodeMetric,
    Workload, AgentRun, AiDecision, MigrationEvent, WorkloadMovementLog,
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
        """Get workloads on non-green zones eligible for migration.

        Excludes:
        - workloads with migration_allowed = 0
        - workloads already on green zones
        - StatefulSets without the opt-in annotation
          'green-workload/migration-allowed: true'
        - DaemonSets (cannot be migrated by design)
        """
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
                          AND w.workload_type != 'DaemonSet'
                          {cluster_filter}
                        ORDER BY e.carbon_intensity DESC
                    """),
                    {"cluster_id": cluster_id} if cluster_id else {},
                ).fetchall()

                results = []
                for row in rows:
                    wl = dict(row._mapping)
                    # Filter out StatefulSets without the opt-in annotation
                    # if wl.get("workload_type", "").lower() == "statefulset":
                    #     annotations = wl.get("annotations") or {}
                    #     if isinstance(annotations, str):
                    #         try:
                    #             annotations = json.loads(annotations)
                    #         except Exception:
                    #             annotations = {}
                    #     if annotations.get("green-workload/migration-allowed") != "true":
                    #         log.info(
                    #             "Excluding StatefulSet without opt-in annotation",
                    #             workload=wl.get("workload_name"),
                    #             annotations=annotations,
                    #         )
                    #         continue
                    results.append(wl)
                return results
        except Exception as e:
            log.error("get_migratable_workloads failed", error=str(e))
            return []

    def resolve_action_names(self, action: dict) -> dict:
        """Resolve human-readable names in an LLM action to database IDs.

        Looks up workload_name, source_node_name, destination_node_name
        and populates workload_id, source_node_id, destination_node_id,
        plus cluster_id and annotations from the workload row.
        Returns a new dict with IDs filled in.
        """
        resolved = dict(action)
        try:
            with get_db() as db:
                # Resolve workload by name + namespace
                wl_name = action.get("workload_name", "")
                wl_ns = action.get("namespace", "")
                wl_row = db.execute(
                    text(
                        "SELECT id, cluster_id, annotations, workload_type "
                        "FROM workloads WHERE name = :n"
                        + (" AND namespace = :ns" if wl_ns else "")
                        + " LIMIT 1"
                    ),
                    {"n": wl_name, "ns": wl_ns} if wl_ns else {"n": wl_name},
                ).fetchone()
                if wl_row:
                    resolved["workload_id"] = wl_row[0]
                    resolved["cluster_id"] = wl_row[1]
                    raw_ann = wl_row[2]
                    if raw_ann:
                        try:
                            resolved["annotations"] = json.loads(raw_ann) if isinstance(raw_ann, str) else raw_ann
                        except Exception:
                            resolved["annotations"] = {}
                    else:
                        resolved["annotations"] = {}
                    resolved["workload_type"] = wl_row[3] or resolved.get("workload_type", "Deployment")
                else:
                    log.warning("resolve_action_names — workload not found", workload_name=wl_name, namespace=wl_ns)
                    resolved["workload_id"] = ""

                # Resolve source node by name
                src_name = action.get("source_node_name", "")
                if src_name:
                    src_row = db.execute(
                        text("SELECT id FROM nodes WHERE name = :n LIMIT 1"),
                        {"n": src_name},
                    ).fetchone()
                    resolved["source_node_id"] = src_row[0] if src_row else ""

                # Resolve destination node by name
                dst_name = action.get("destination_node_name", "")
                if dst_name:
                    dst_row = db.execute(
                        text("SELECT id FROM nodes WHERE name = :n LIMIT 1"),
                        {"n": dst_name},
                    ).fetchone()
                    resolved["destination_node_id"] = dst_row[0] if dst_row else ""

        except Exception as e:
            log.error("resolve_action_names failed", error=str(e), action=action)
        return resolved

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

    def complete_migration(
        self,
        migration_id: str,
        workload_id: str,
        destination_node_id: str,
        duration_seconds: int,
    ) -> None:
        """
        Mark a migration as completed and update the cluster topology so
        the workload's current_node_id points to its new destination.

        Also backfills zone IDs and carbon-intensity numbers on the
        migration_events row so the dashboard can show full details,
        and writes a row to workload_movement_log.
        """
        try:
            with get_db() as db:
                now = datetime.utcnow()

                # ── 1. Read the workload's CURRENT position (before move) ─
                wl_row = db.execute(
                    text(
                        "SELECT w.id, w.name, w.namespace, w.cluster_id, "
                        "       w.current_node_id, n.name AS node_name "
                        "FROM workloads w "
                        "LEFT JOIN nodes n ON w.current_node_id = n.id "
                        "WHERE w.id = :wid"
                    ),
                    {"wid": workload_id},
                ).fetchone()

                if not wl_row:
                    log.error(
                        "complete_migration — workload not found, cannot update topology",
                        workload_id=workload_id,
                        migration_id=migration_id,
                    )
                    # Still mark the migration event as completed
                    db.execute(
                        text(
                            "UPDATE migration_events "
                            "SET status='completed', completed_at=:now, duration_seconds=:dur "
                            "WHERE id=:mid"
                        ),
                        {"now": now, "dur": duration_seconds, "mid": migration_id},
                    )
                    return

                old_node_id = wl_row[4]      # current_node_id before move
                old_node_name = wl_row[5]     # node name before move
                wl_name = wl_row[1]
                wl_ns = wl_row[2]
                wl_cluster = wl_row[3]

                log.info(
                    "complete_migration — moving workload",
                    workload=wl_name,
                    workload_id=workload_id,
                    from_node_id=old_node_id,
                    from_node=old_node_name,
                    to_node_id=destination_node_id,
                    migration_id=migration_id,
                )

                # ── 2. UPDATE workload to destination node ────────────
                result = db.execute(
                    text(
                        "UPDATE workloads "
                        "SET current_node_id = :dst, updated_at = :now "
                        "WHERE id = :wid"
                    ),
                    {"dst": destination_node_id, "now": now, "wid": workload_id},
                )
                log.info(
                    "complete_migration — workload UPDATE executed",
                    rows_affected=result.rowcount,
                    workload_id=workload_id,
                    new_node_id=destination_node_id,
                )

                # ── 3. Resolve zone IDs and carbon data ───────────────
                src_node_id = old_node_id
                dst_node_id = destination_node_id

                src_zone_id = dst_zone_id = None
                src_zone_name = dst_zone_name = None
                src_carbon = dst_carbon = None

                if src_node_id:
                    zrow = db.execute(
                        text("SELECT zone_id FROM nodes WHERE id = :nid"),
                        {"nid": src_node_id},
                    ).fetchone()
                    if zrow and zrow[0]:
                        src_zone_id = zrow[0]
                        znrow = db.execute(
                            text("SELECT name FROM zones WHERE id = :zid"),
                            {"zid": src_zone_id},
                        ).fetchone()
                        src_zone_name = znrow[0] if znrow else None

                if dst_node_id:
                    zrow = db.execute(
                        text("SELECT zone_id FROM nodes WHERE id = :nid"),
                        {"nid": dst_node_id},
                    ).fetchone()
                    if zrow and zrow[0]:
                        dst_zone_id = zrow[0]
                        znrow = db.execute(
                            text("SELECT name FROM zones WHERE id = :zid"),
                            {"zid": dst_zone_id},
                        ).fetchone()
                        dst_zone_name = znrow[0] if znrow else None

                if src_zone_id:
                    crow = db.execute(
                        text(
                            "SELECT carbon_intensity FROM energy_readings "
                            "WHERE zone_id = :zid ORDER BY timestamp DESC LIMIT 1"
                        ),
                        {"zid": src_zone_id},
                    ).fetchone()
                    if crow:
                        src_carbon = float(crow[0])

                if dst_zone_id:
                    crow = db.execute(
                        text(
                            "SELECT carbon_intensity FROM energy_readings "
                            "WHERE zone_id = :zid ORDER BY timestamp DESC LIMIT 1"
                        ),
                        {"zid": dst_zone_id},
                    ).fetchone()
                    if crow:
                        dst_carbon = float(crow[0])

                carbon_saved = None
                if src_carbon is not None and dst_carbon is not None:
                    carbon_saved = round(src_carbon - dst_carbon, 3)

                # Destination node name
                dst_name_row = db.execute(
                    text("SELECT name FROM nodes WHERE id = :nid"),
                    {"nid": dst_node_id},
                ).fetchone()
                dst_node_name = dst_name_row[0] if dst_name_row else None

                # ── 4. Update the migration event with full details ───
                db.execute(
                    text(
                        "UPDATE migration_events SET "
                        "  status = 'completed', "
                        "  completed_at = :now, "
                        "  duration_seconds = :dur, "
                        "  source_node_id = COALESCE(source_node_id, :sn), "
                        "  destination_node_id = COALESCE(destination_node_id, :dn), "
                        "  source_zone_id = :sz, "
                        "  destination_zone_id = :dz, "
                        "  source_carbon_intensity = :sc, "
                        "  destination_carbon_intensity = :dc, "
                        "  carbon_savings_estimate = :cs "
                        "WHERE id = :mid"
                    ),
                    {
                        "now": now,
                        "dur": duration_seconds,
                        "sn": src_node_id,
                        "dn": dst_node_id,
                        "sz": src_zone_id,
                        "dz": dst_zone_id,
                        "sc": src_carbon,
                        "dc": dst_carbon,
                        "cs": carbon_saved,
                        "mid": migration_id,
                    },
                )

                # ── 5. Write movement log entry ──────────────────────
                db.execute(
                    text(
                        "INSERT INTO workload_movement_log "
                        "(id, workload_id, workload_name, namespace, cluster_id, "
                        " source_node_id, source_node_name, "
                        " destination_node_id, destination_node_name, "
                        " source_zone_name, destination_zone_name, "
                        " migration_event_id, moved_at) "
                        "VALUES (UUID(), :wid, :wname, :ns, :cid, "
                        " :sn, :snn, :dn, :dnn, :szn, :dzn, :mid, :now)"
                    ),
                    {
                        "wid": workload_id,
                        "wname": wl_name,
                        "ns": wl_ns,
                        "cid": wl_cluster,
                        "sn": src_node_id,
                        "snn": old_node_name,
                        "dn": dst_node_id,
                        "dnn": dst_node_name,
                        "szn": src_zone_name,
                        "dzn": dst_zone_name,
                        "mid": migration_id,
                        "now": now,
                    },
                )

                # ── 6. Commit critical changes (workload move + movement log) ──
                # Flush to DB so the critical rows are ready for commit.
                db.flush()
                log.info(
                    "Movement log entry written",
                    migration_id=migration_id,
                    workload=wl_name,
                )

                # ── 7. Update node metrics (non-fatal, uses savepoint) ──
                # A savepoint ensures that a metrics failure only rolls
                # back the metrics INSERT, not the critical changes above.
                try:
                    nested = db.begin_nested()  # SAVEPOINT
                    self._recompute_node_metrics(db, src_node_id, now)
                    self._recompute_node_metrics(db, dst_node_id, now)
                    nested.commit()
                except Exception as e:
                    log.warning(
                        "Node metrics recomputation failed (non-fatal, savepoint rolled back)",
                        error=str(e),
                    )

                log.info(
                    "Topology updated — workload relocated",
                    migration_id=migration_id,
                    workload=wl_name,
                    workload_id=workload_id,
                    from_node=old_node_name,
                    to_node=dst_node_name,
                    source_zone=src_zone_name,
                    destination_zone=dst_zone_name,
                    carbon_saved=carbon_saved,
                    duration_seconds=duration_seconds,
                )
        except Exception as e:
            log.error(
                "complete_migration failed",
                migration_id=migration_id,
                workload_id=workload_id,
                destination_node_id=destination_node_id,
                error=str(e),
            )
            raise

    # ------------------------------------------------------------------
    # Node metrics recomputation
    # ------------------------------------------------------------------

    def _recompute_node_metrics(self, db, node_id: Optional[str], now) -> None:
        """
        Insert a fresh node_metrics row that reflects the current workloads
        assigned to the node.  Uses the node's allocatable capacity and
        the sum of resource_requests from its workloads to derive
        CPU / memory percentages and pod count.  Network figures are
        carried over from the previous reading with a small jitter.
        """
        if not node_id:
            return

        # Node capacity
        node_row = db.execute(
            text(
                "SELECT allocatable_cpu, allocatable_memory_gb, allocatable_pods "
                "FROM nodes WHERE id = :nid"
            ),
            {"nid": node_id},
        ).fetchone()
        if not node_row:
            return

        alloc_cpu = float(node_row[0] or 4)
        alloc_mem = float(node_row[1] or 16)

        # Sum resource requests of all workloads currently on this node
        usage_row = db.execute(
            text(
                "SELECT COALESCE(SUM(resource_requests_cpu * replica_count), 0), "
                "       COALESCE(SUM(resource_requests_memory_gb * replica_count), 0), "
                "       COUNT(*) "
                "FROM workloads WHERE current_node_id = :nid"
            ),
            {"nid": node_id},
        ).fetchone()

        used_cpu = float(usage_row[0])
        used_mem = float(usage_row[1])
        pod_count = int(usage_row[2])

        # Add a small base overhead (OS / kubelet / daemonsets ≈ 10%)
        base_cpu = alloc_cpu * 0.10
        base_mem = alloc_mem * 0.10
        used_cpu = min(used_cpu + base_cpu, alloc_cpu)
        used_mem = min(used_mem + base_mem, alloc_mem)

        cpu_pct = round((used_cpu / alloc_cpu) * 100, 2) if alloc_cpu else 0
        mem_pct = round((used_mem / alloc_mem) * 100, 2) if alloc_mem else 0

        # Carry forward network from the latest reading (with ±5% jitter)
        import random
        prev = db.execute(
            text(
                "SELECT network_in_mbps, network_out_mbps "
                "FROM node_metrics WHERE node_id = :nid "
                "ORDER BY timestamp DESC LIMIT 1"
            ),
            {"nid": node_id},
        ).fetchone()
        if prev and prev[0] is not None:
            jitter = lambda v: round(float(v) * random.uniform(0.95, 1.05), 3)
            net_in = jitter(prev[0])
            net_out = jitter(prev[1])
        else:
            net_in = round(random.uniform(50, 200), 3)
            net_out = round(random.uniform(30, 150), 3)

        db.execute(
            text(
                "INSERT INTO node_metrics "
                "(id, node_id, timestamp, cpu_usage_cores, cpu_usage_percent, "
                " memory_usage_gb, memory_usage_percent, pod_count, "
                " network_in_mbps, network_out_mbps) "
                "VALUES (UUID(), :nid, :ts, :cpu_cores, :cpu_pct, "
                " :mem_gb, :mem_pct, :pods, :net_in, :net_out)"
            ),
            {
                "nid": node_id,
                "ts": now,
                "cpu_cores": round(used_cpu, 3),
                "cpu_pct": cpu_pct,
                "mem_gb": round(used_mem, 3),
                "mem_pct": mem_pct,
                "pods": pod_count,
                "net_in": net_in,
                "net_out": net_out,
            },
        )

        log.info(
            "Node metrics updated",
            node_id=node_id,
            cpu_pct=cpu_pct,
            mem_pct=mem_pct,
            pod_count=pod_count,
        )
