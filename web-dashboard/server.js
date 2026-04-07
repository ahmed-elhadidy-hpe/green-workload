const express = require("express");
const mysql = require("mysql2/promise");
const path = require("path");

const app = express();
const PORT = process.env.DASHBOARD_PORT || 3099;

// ── MySQL pool ────────────────────────────────────────────────────────
const pool = mysql.createPool({
  host: process.env.DB_HOST || "127.0.0.1",
  port: parseInt(process.env.DB_PORT || "3306"),
  user: process.env.DB_USER || "root",
  password: process.env.DB_PASSWORD || "",
  database: process.env.DB_NAME || "GREEN_WORKLOAD_DB",
  waitForConnections: true,
  connectionLimit: 5,
  charset: "utf8mb4",
});

app.use(express.static(path.join(__dirname, "public")));

// ── Query helpers ─────────────────────────────────────────────────────

async function getMigrations() {
  const [rows] = await pool.query(`
    SELECT
      me.id, me.workload_name, me.namespace, me.status,
      me.trigger_reason, me.migration_type,
      me.source_carbon_intensity, me.destination_carbon_intensity,
      me.carbon_savings_estimate, me.duration_seconds,
      me.started_at, me.completed_at, me.error_message,
      sn.name  AS source_node_name,
      dn.name  AS destination_node_name,
      sz.name  AS source_zone_name,
      dz.name  AS destination_zone_name,
      c.name   AS cluster_name
    FROM migration_events me
    LEFT JOIN nodes sn ON me.source_node_id      = sn.id
    LEFT JOIN nodes dn ON me.destination_node_id  = dn.id
    LEFT JOIN zones sz ON me.source_zone_id       = sz.id
    LEFT JOIN zones dz ON me.destination_zone_id  = dz.id
    LEFT JOIN clusters c ON me.cluster_id         = c.id
    ORDER BY me.created_at DESC
    LIMIT 50
  `);
  return rows;
}

async function getNodes() {
  const [rows] = await pool.query(`
    SELECT
      n.id, n.name, n.instance_type, n.status,
      n.is_cordoned, n.is_migration_target, n.migration_opt_out,
      n.allocatable_cpu, n.allocatable_memory_gb, n.allocatable_pods,
      c.name   AS cluster_name,
      z.name   AS zone_name,
      z.display_name AS zone_display_name,
      nm.cpu_usage_cores, nm.cpu_usage_percent,
      nm.memory_usage_gb, nm.memory_usage_percent,
      nm.pod_count, nm.is_overloaded,
      nm.network_in_mbps, nm.network_out_mbps,
      nm.timestamp AS metrics_timestamp,
      e.carbon_intensity, e.renewable_percentage, e.is_green
    FROM nodes n
    JOIN clusters c ON n.cluster_id = c.id
    LEFT JOIN zones z ON n.zone_id = z.id
    LEFT JOIN node_metrics nm ON nm.id = (
      SELECT id FROM node_metrics nm2
      WHERE nm2.node_id = n.id ORDER BY nm2.timestamp DESC LIMIT 1
    )
    LEFT JOIN energy_readings e ON e.id = (
      SELECT id FROM energy_readings er
      WHERE er.zone_id = n.zone_id ORDER BY er.timestamp DESC LIMIT 1
    )
    ORDER BY c.name, z.name, n.name
  `);
  return rows;
}

async function getZones() {
  const [rows] = await pool.query(`
    SELECT
      z.id, z.name, z.display_name, z.energy_provider,
      r.name AS region_name, r.display_name AS region_display_name,
      e.carbon_intensity, e.renewable_percentage, e.is_green,
      e.energy_sources, e.timestamp AS energy_timestamp,
      e.data_quality,
      (SELECT COUNT(*) FROM nodes n WHERE n.zone_id = z.id) AS node_count,
      (SELECT COUNT(*) FROM workloads w
       JOIN nodes n2 ON w.current_node_id = n2.id
       WHERE n2.zone_id = z.id) AS workload_count
    FROM zones z
    JOIN regions r ON z.region_id = r.id
    LEFT JOIN energy_readings e ON e.id = (
      SELECT id FROM energy_readings er
      WHERE er.zone_id = z.id ORDER BY er.timestamp DESC LIMIT 1
    )
    ORDER BY e.is_green DESC, e.carbon_intensity ASC
  `);
  return rows;
}

async function getWorkloads() {
  const [rows] = await pool.query(`
    SELECT
      w.id, w.name, w.namespace, w.workload_type,
      w.replica_count, w.priority, w.migration_allowed, w.stateful,
      w.resource_requests_cpu, w.resource_requests_memory_gb,
      n.name AS node_name,
      z.name AS zone_name,
      c.name AS cluster_name,
      e.is_green AS on_green_zone,
      e.carbon_intensity
    FROM workloads w
    LEFT JOIN nodes n ON w.current_node_id = n.id
    LEFT JOIN zones z ON n.zone_id = z.id
    LEFT JOIN clusters c ON w.cluster_id = c.id
    LEFT JOIN energy_readings e ON e.id = (
      SELECT id FROM energy_readings er
      WHERE er.zone_id = n.zone_id ORDER BY er.timestamp DESC LIMIT 1
    )
    ORDER BY e.is_green ASC, e.carbon_intensity DESC
  `);
  return rows;
}

async function getSummary() {
  const [[migrationStats]] = await pool.query(`
    SELECT
      COUNT(*)                                          AS total_migrations,
      SUM(status = 'completed')                         AS completed,
      SUM(status = 'in_progress')                       AS in_progress,
      SUM(status = 'pending')                           AS pending,
      SUM(status = 'failed')                            AS failed,
      ROUND(COALESCE(SUM(carbon_savings_estimate), 0), 1) AS total_carbon_saved
    FROM migration_events
  `);
  const [[zoneStats]] = await pool.query(`
    SELECT
      COUNT(*)                       AS total_zones,
      SUM(COALESCE(e.is_green, 0))   AS green_zones,
      SUM(1 - COALESCE(e.is_green, 0)) AS dirty_zones
    FROM zones z
    LEFT JOIN energy_readings e ON e.id = (
      SELECT id FROM energy_readings er
      WHERE er.zone_id = z.id ORDER BY er.timestamp DESC LIMIT 1
    )
  `);
  const [[nodeStats]] = await pool.query(`
    SELECT
      COUNT(*)             AS total_nodes,
      SUM(CASE WHEN nm.is_overloaded = 1 THEN 1 ELSE 0 END) AS overloaded_nodes
    FROM nodes n
    LEFT JOIN node_metrics nm ON nm.id = (
      SELECT id FROM node_metrics nm2
      WHERE nm2.node_id = n.id ORDER BY nm2.timestamp DESC LIMIT 1
    )
  `);
  const [[workloadStats]] = await pool.query(`
    SELECT COUNT(*) AS total_workloads FROM workloads
  `);
  return { ...migrationStats, ...zoneStats, ...nodeStats, ...workloadStats };
}

async function getMovementLog() {
  const [rows] = await pool.query(`
    SELECT
      wml.workload_name, wml.namespace,
      wml.source_node_name, wml.destination_node_name,
      wml.source_zone_name, wml.destination_zone_name,
      wml.moved_at, wml.migration_event_id,
      c.name AS cluster_name
    FROM workload_movement_log wml
    LEFT JOIN clusters c ON wml.cluster_id = c.id
    ORDER BY wml.moved_at DESC
    LIMIT 100
  `);
  return rows;
}

async function getAiDecisions() {
  const [rows] = await pool.query(`
    SELECT
      d.id, d.timestamp, d.model_name, d.decision_type,
      d.reasoning, d.recommended_actions,
      d.safety_check_passed, d.safety_check_notes,
      d.execution_started,
      d.created_at,
      ar.id AS agent_run_id,
      ar.status AS run_status,
      (SELECT COUNT(*) FROM migration_events me WHERE me.ai_decision_id = d.id) AS migration_count,
      (SELECT SUM(me.status = 'completed') FROM migration_events me WHERE me.ai_decision_id = d.id) AS migrations_completed,
      (SELECT SUM(me.status = 'failed') FROM migration_events me WHERE me.ai_decision_id = d.id) AS migrations_failed
    FROM ai_decisions d
    LEFT JOIN agent_runs ar ON d.agent_run_id = ar.id
    ORDER BY d.timestamp DESC
    LIMIT 50
  `);
  return rows;
}

// ── REST endpoints ────────────────────────────────────────────────────

app.get("/api/dashboard", async (_req, res) => {
  try {
    const [summary, migrations, nodes, zones, workloads, movementLog, aiDecisions] = await Promise.all([
      getSummary(), getMigrations(), getNodes(), getZones(), getWorkloads(), getMovementLog(), getAiDecisions(),
    ]);
    res.json({ summary, migrations, nodes, zones, workloads, movementLog, aiDecisions, timestamp: new Date().toISOString() });
  } catch (err) {
    console.error("Dashboard query error:", err.message);
    res.status(500).json({ error: err.message });
  }
});

// ── SSE live stream ───────────────────────────────────────────────────

app.get("/api/stream", (req, res) => {
  res.writeHead(200, {
    "Content-Type": "text/event-stream",
    "Cache-Control": "no-cache",
    Connection: "keep-alive",
    "X-Accel-Buffering": "no",
  });
  res.write(":\n\n"); // keep-alive comment

  const send = async () => {
    try {
      const [summary, migrations, nodes, zones, workloads, movementLog, aiDecisions] = await Promise.all([
        getSummary(), getMigrations(), getNodes(), getZones(), getWorkloads(), getMovementLog(), getAiDecisions(),
      ]);
      const payload = JSON.stringify({ summary, migrations, nodes, zones, workloads, movementLog, aiDecisions, timestamp: new Date().toISOString() });
      res.write(`data: ${payload}\n\n`);
    } catch (err) {
      console.error("SSE error:", err.message);
    }
  };

  send();
  const interval = setInterval(send, 3000);

  req.on("close", () => {
    clearInterval(interval);
  });
});

// ── Start ─────────────────────────────────────────────────────────────

app.listen(PORT, () => {
  console.log(`\n🌿 Green Workload Dashboard running at http://localhost:${PORT}\n`);
});
