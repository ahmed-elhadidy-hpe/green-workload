-- ============================================================
-- Green Workload AI — MySQL 8.0 Schema
-- ============================================================

-- ============================================================
-- REGIONS
-- ============================================================
CREATE TABLE IF NOT EXISTS regions (
    id           CHAR(36)      NOT NULL DEFAULT (UUID()),
    name         VARCHAR(100)  NOT NULL,
    display_name VARCHAR(200),
    country_code CHAR(2),
    latitude     DECIMAL(9,6),
    longitude    DECIMAL(9,6),
    created_at   DATETIME(6)   NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at   DATETIME(6)   NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
    PRIMARY KEY (id),
    UNIQUE KEY uq_regions_name (name)
);

-- ============================================================
-- ZONES
-- ============================================================
CREATE TABLE IF NOT EXISTS zones (
    id                  CHAR(36)     NOT NULL DEFAULT (UUID()),
    name                VARCHAR(100) NOT NULL,
    region_id           CHAR(36)     NOT NULL,
    display_name        VARCHAR(200),
    energy_provider     VARCHAR(200),
    electricitymap_zone VARCHAR(50),
    watttime_ba         VARCHAR(50),
    created_at          DATETIME(6)  NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at          DATETIME(6)  NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
    PRIMARY KEY (id),
    UNIQUE KEY uq_zones_name_region (name, region_id),
    CONSTRAINT fk_zones_region FOREIGN KEY (region_id) REFERENCES regions (id) ON DELETE RESTRICT
);

-- ============================================================
-- CLUSTERS
-- ============================================================
CREATE TABLE IF NOT EXISTS clusters (
    id                    CHAR(36)     NOT NULL DEFAULT (UUID()),
    name                  VARCHAR(200) NOT NULL,
    display_name          VARCHAR(200),
    kubeconfig_secret_ref VARCHAR(500),
    api_endpoint          VARCHAR(500),
    region_id             CHAR(36),
    status                VARCHAR(50)  NOT NULL DEFAULT 'active',
    kubernetes_version    VARCHAR(50),
    managed_by            VARCHAR(200),
    created_at            DATETIME(6)  NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at            DATETIME(6)  NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
    PRIMARY KEY (id),
    UNIQUE KEY uq_clusters_name (name),
    CONSTRAINT fk_clusters_region FOREIGN KEY (region_id) REFERENCES regions (id) ON DELETE SET NULL
);

-- ============================================================
-- NODES
-- ============================================================
CREATE TABLE IF NOT EXISTS nodes (
    id                  CHAR(36)      NOT NULL DEFAULT (UUID()),
    name                VARCHAR(200)  NOT NULL,
    cluster_id          CHAR(36)      NOT NULL,
    zone_id             CHAR(36),
    provider_id         VARCHAR(500),
    instance_type       VARCHAR(100),
    operating_system    VARCHAR(100),
    kernel_version      VARCHAR(100),
    container_runtime   VARCHAR(100),
    allocatable_cpu     DECIMAL(10,3),
    allocatable_memory_gb DECIMAL(10,3),
    allocatable_pods    INT,
    labels              JSON,
    taints              JSON,
    status              VARCHAR(50)   NOT NULL DEFAULT 'Ready',
    is_cordoned         TINYINT(1)    NOT NULL DEFAULT 0,
    is_migration_target TINYINT(1)    NOT NULL DEFAULT 1,
    migration_opt_out   TINYINT(1)    NOT NULL DEFAULT 0,
    created_at          DATETIME(6)   NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at          DATETIME(6)   NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
    PRIMARY KEY (id),
    UNIQUE KEY uq_nodes_name_cluster (name, cluster_id),
    KEY idx_nodes_cluster (cluster_id),
    KEY idx_nodes_zone (zone_id),
    CONSTRAINT fk_nodes_cluster FOREIGN KEY (cluster_id) REFERENCES clusters (id) ON DELETE CASCADE,
    CONSTRAINT fk_nodes_zone    FOREIGN KEY (zone_id)    REFERENCES zones    (id) ON DELETE SET NULL
);

-- ============================================================
-- ENERGY READINGS
-- ============================================================
CREATE TABLE IF NOT EXISTS energy_readings (
    id                   CHAR(36)     NOT NULL DEFAULT (UUID()),
    zone_id              CHAR(36)     NOT NULL,
    timestamp            DATETIME(6)  NOT NULL,
    carbon_intensity     DECIMAL(8,3),
    renewable_percentage DECIMAL(5,2),
    energy_sources       JSON,
    is_green             TINYINT(1)   GENERATED ALWAYS AS (renewable_percentage >= 50) STORED,
    data_source          VARCHAR(100),
    data_quality         VARCHAR(50),
    created_at           DATETIME(6)  NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    PRIMARY KEY (id),
    KEY idx_energy_zone_time  (zone_id, timestamp DESC),
    KEY idx_energy_is_green   (is_green, timestamp DESC),
    CONSTRAINT fk_energy_zone FOREIGN KEY (zone_id) REFERENCES zones (id) ON DELETE CASCADE
);

-- ============================================================
-- NODE METRICS
-- ============================================================
CREATE TABLE IF NOT EXISTS node_metrics (
    id                   CHAR(36)     NOT NULL DEFAULT (UUID()),
    node_id              CHAR(36)     NOT NULL,
    timestamp            DATETIME(6)  NOT NULL,
    cpu_usage_cores      DECIMAL(10,3),
    cpu_usage_percent    DECIMAL(5,2),
    memory_usage_gb      DECIMAL(10,3),
    memory_usage_percent DECIMAL(5,2),
    pod_count            INT,
    network_in_mbps      DECIMAL(10,3),
    network_out_mbps     DECIMAL(10,3),
    is_overloaded        TINYINT(1)   GENERATED ALWAYS AS (cpu_usage_percent > 80 OR memory_usage_percent > 80) STORED,
    created_at           DATETIME(6)  NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    PRIMARY KEY (id),
    KEY idx_node_metrics_node_time (node_id, timestamp DESC),
    CONSTRAINT fk_metrics_node FOREIGN KEY (node_id) REFERENCES nodes (id) ON DELETE CASCADE
);

-- ============================================================
-- WORKLOADS
-- ============================================================
CREATE TABLE IF NOT EXISTS workloads (
    id                        CHAR(36)     NOT NULL DEFAULT (UUID()),
    name                      VARCHAR(200) NOT NULL,
    namespace                 VARCHAR(200) NOT NULL DEFAULT 'default',
    cluster_id                CHAR(36)     NOT NULL,
    workload_type             VARCHAR(50)  NOT NULL,
    current_node_id           CHAR(36),
    replica_count             INT          NOT NULL DEFAULT 1,
    priority                  VARCHAR(50)  NOT NULL DEFAULT 'normal',
    migration_allowed         TINYINT(1)   NOT NULL DEFAULT 1,
    stateful                  TINYINT(1)   NOT NULL DEFAULT 0,
    labels                    JSON,
    annotations               JSON,
    resource_requests_cpu     DECIMAL(10,3),
    resource_requests_memory_gb DECIMAL(10,3),
    last_seen_at              DATETIME(6),
    created_at                DATETIME(6)  NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at                DATETIME(6)  NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
    PRIMARY KEY (id),
    UNIQUE KEY uq_workloads_name_ns_cluster (name, namespace, cluster_id),
    KEY idx_workloads_cluster (cluster_id),
    CONSTRAINT fk_workloads_cluster FOREIGN KEY (cluster_id)       REFERENCES clusters (id) ON DELETE CASCADE,
    CONSTRAINT fk_workloads_node    FOREIGN KEY (current_node_id)  REFERENCES nodes    (id) ON DELETE SET NULL
);

-- ============================================================
-- AGENT RUNS
-- ============================================================
CREATE TABLE IF NOT EXISTS agent_runs (
    id                  CHAR(36)    NOT NULL DEFAULT (UUID()),
    started_at          DATETIME(6) NOT NULL,
    completed_at        DATETIME(6),
    status              VARCHAR(50) NOT NULL DEFAULT 'running',
    clusters_evaluated  INT         NOT NULL DEFAULT 0,
    workloads_evaluated INT         NOT NULL DEFAULT 0,
    migrations_initiated INT        NOT NULL DEFAULT 0,
    error_message       TEXT,
    created_at          DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    PRIMARY KEY (id)
);

-- ============================================================
-- AI DECISIONS
-- ============================================================
CREATE TABLE IF NOT EXISTS ai_decisions (
    id                  CHAR(36)    NOT NULL DEFAULT (UUID()),
    agent_run_id        CHAR(36)    NOT NULL,
    timestamp           DATETIME(6) NOT NULL,
    model_name          VARCHAR(200),
    input_context       JSON,
    reasoning           TEXT,
    decision_type       VARCHAR(50),
    recommended_actions JSON,
    safety_check_passed TINYINT(1)  NOT NULL DEFAULT 1,
    safety_check_notes  TEXT,
    execution_started   TINYINT(1)  NOT NULL DEFAULT 0,
    created_at          DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    PRIMARY KEY (id),
    KEY idx_ai_decisions_run (agent_run_id),
    CONSTRAINT fk_decisions_run FOREIGN KEY (agent_run_id) REFERENCES agent_runs (id) ON DELETE CASCADE
);

-- ============================================================
-- MIGRATION EVENTS
-- ============================================================
CREATE TABLE IF NOT EXISTS migration_events (
    id                           CHAR(36)     NOT NULL DEFAULT (UUID()),
    workload_id                  CHAR(36),
    workload_name                VARCHAR(200) NOT NULL,
    namespace                    VARCHAR(200) NOT NULL,
    cluster_id                   CHAR(36),
    source_node_id               CHAR(36),
    destination_node_id          CHAR(36),
    source_zone_id               CHAR(36),
    destination_zone_id          CHAR(36),
    migration_type               VARCHAR(50)  NOT NULL DEFAULT 'affinity',
    status                       VARCHAR(50)  NOT NULL DEFAULT 'pending',
    ai_decision_id               CHAR(36),
    trigger_reason               TEXT,
    source_carbon_intensity      DECIMAL(8,3),
    destination_carbon_intensity DECIMAL(8,3),
    carbon_savings_estimate      DECIMAL(8,3),
    started_at                   DATETIME(6),
    completed_at                 DATETIME(6),
    duration_seconds             INT,
    error_message                TEXT,
    rollback_attempted           TINYINT(1)   NOT NULL DEFAULT 0,
    created_at                   DATETIME(6)  NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at                   DATETIME(6)  NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
    PRIMARY KEY (id),
    KEY idx_migration_status    (status),
    KEY idx_migration_workload  (workload_id),
    KEY idx_migration_decision  (ai_decision_id),
    KEY idx_migration_cluster   (cluster_id),
    CONSTRAINT fk_migration_workload  FOREIGN KEY (workload_id)          REFERENCES workloads   (id) ON DELETE SET NULL,
    CONSTRAINT fk_migration_cluster   FOREIGN KEY (cluster_id)           REFERENCES clusters    (id) ON DELETE SET NULL,
    CONSTRAINT fk_migration_src_node  FOREIGN KEY (source_node_id)       REFERENCES nodes       (id) ON DELETE SET NULL,
    CONSTRAINT fk_migration_dst_node  FOREIGN KEY (destination_node_id)  REFERENCES nodes       (id) ON DELETE SET NULL,
    CONSTRAINT fk_migration_src_zone  FOREIGN KEY (source_zone_id)       REFERENCES zones       (id) ON DELETE SET NULL,
    CONSTRAINT fk_migration_dst_zone  FOREIGN KEY (destination_zone_id)  REFERENCES zones       (id) ON DELETE SET NULL,
    CONSTRAINT fk_migration_decision  FOREIGN KEY (ai_decision_id)       REFERENCES ai_decisions(id) ON DELETE SET NULL
);

-- ============================================================
-- WORKLOAD MOVEMENT LOG
-- ============================================================
CREATE TABLE IF NOT EXISTS workload_movement_log (
    id                    CHAR(36)     NOT NULL DEFAULT (UUID()),
    workload_id           CHAR(36),
    workload_name         VARCHAR(200) NOT NULL,
    namespace             VARCHAR(200) NOT NULL DEFAULT 'default',
    cluster_id            CHAR(36),
    source_node_id        CHAR(36),
    source_node_name      VARCHAR(200),
    destination_node_id   CHAR(36),
    destination_node_name VARCHAR(200),
    source_zone_name      VARCHAR(100),
    destination_zone_name VARCHAR(100),
    migration_event_id    CHAR(36),
    moved_at              DATETIME(6)  NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    created_at            DATETIME(6)  NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    PRIMARY KEY (id),
    KEY idx_movement_workload (workload_id),
    KEY idx_movement_time (moved_at DESC),
    KEY idx_movement_migration (migration_event_id),
    CONSTRAINT fk_movement_workload  FOREIGN KEY (workload_id)        REFERENCES workloads (id) ON DELETE SET NULL,
    CONSTRAINT fk_movement_cluster   FOREIGN KEY (cluster_id)         REFERENCES clusters  (id) ON DELETE SET NULL,
    CONSTRAINT fk_movement_src_node  FOREIGN KEY (source_node_id)     REFERENCES nodes     (id) ON DELETE SET NULL,
    CONSTRAINT fk_movement_dst_node  FOREIGN KEY (destination_node_id) REFERENCES nodes    (id) ON DELETE SET NULL,
    CONSTRAINT fk_movement_migration FOREIGN KEY (migration_event_id) REFERENCES migration_events (id) ON DELETE SET NULL
);

-- ============================================================
-- VIEWS
-- ============================================================

-- Latest energy reading per zone
CREATE OR REPLACE VIEW zone_current_energy AS
SELECT
    z.id                 AS zone_id,
    z.name               AS zone_name,
    r.name               AS region_name,
    e.carbon_intensity,
    e.renewable_percentage,
    e.is_green,
    e.energy_sources,
    e.timestamp          AS last_updated,
    e.data_quality
FROM zones z
JOIN regions r ON z.region_id = r.id
LEFT JOIN energy_readings e ON e.id = (
    SELECT id FROM energy_readings er2
    WHERE er2.zone_id = z.id
    ORDER BY er2.timestamp DESC
    LIMIT 1
);

-- Node health, load, and zone greenness
CREATE OR REPLACE VIEW node_current_status AS
SELECT
    n.id                    AS node_id,
    n.name                  AS node_name,
    c.name                  AS cluster_name,
    c.id                    AS cluster_id,
    z.name                  AS zone_name,
    n.status,
    n.is_cordoned,
    n.migration_opt_out,
    n.is_migration_target,
    n.allocatable_cpu,
    n.allocatable_memory_gb,
    nm.cpu_usage_percent,
    nm.memory_usage_percent,
    nm.pod_count,
    nm.is_overloaded,
    nm.timestamp            AS metrics_last_updated,
    zce.is_green            AS zone_is_green,
    zce.carbon_intensity,
    zce.renewable_percentage,
    zce.last_updated        AS energy_last_updated
FROM nodes n
JOIN clusters c ON n.cluster_id = c.id
LEFT JOIN zones z ON n.zone_id = z.id
LEFT JOIN node_metrics nm ON nm.id = (
    SELECT id FROM node_metrics nm2
    WHERE nm2.node_id = n.id
    ORDER BY nm2.timestamp DESC
    LIMIT 1
)
LEFT JOIN zone_current_energy zce ON zce.zone_id = n.zone_id;

-- Carbon savings summary per cluster per day
CREATE OR REPLACE VIEW migration_carbon_summary AS
SELECT
    c.name                                   AS cluster,
    DATE(me.completed_at)                    AS day,
    COUNT(*)                                 AS migrations_completed,
    SUM(me.carbon_savings_estimate)          AS total_carbon_saved_gco2,
    AVG(me.duration_seconds)                 AS avg_migration_duration_s
FROM migration_events me
JOIN clusters c ON me.cluster_id = c.id
WHERE me.status = 'completed'
GROUP BY c.name, DATE(me.completed_at)
ORDER BY day DESC
