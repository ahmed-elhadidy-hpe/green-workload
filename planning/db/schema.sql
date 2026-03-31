-- ============================================================
-- Green Workload AI — PostgreSQL Schema
-- ============================================================

CREATE EXTENSION IF NOT EXISTS "pgcrypto";  -- for gen_random_uuid()

-- ============================================================
-- REGIONS
-- ============================================================
CREATE TABLE regions (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name            VARCHAR(100) NOT NULL UNIQUE,
    display_name    VARCHAR(200),
    country_code    CHAR(2),
    latitude        DECIMAL(9,6),
    longitude       DECIMAL(9,6),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- ============================================================
-- ZONES
-- ============================================================
CREATE TABLE zones (
    id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name                  VARCHAR(100) NOT NULL,
    region_id             UUID NOT NULL REFERENCES regions(id) ON DELETE RESTRICT,
    display_name          VARCHAR(200),
    energy_provider       VARCHAR(200),
    electricitymap_zone   VARCHAR(50),  -- Electricity Maps zone code (e.g. US-CAL-CISO)
    watttime_ba           VARCHAR(50),  -- WattTime balancing authority
    created_at            TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(name, region_id)
);

-- ============================================================
-- CLUSTERS
-- ============================================================
CREATE TABLE clusters (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name                    VARCHAR(200) NOT NULL UNIQUE,
    display_name            VARCHAR(200),
    kubeconfig_secret_ref   VARCHAR(500),  -- path/reference in secret store (e.g. Vault)
    api_endpoint            VARCHAR(500),
    region_id               UUID REFERENCES regions(id) ON DELETE SET NULL,
    status                  VARCHAR(50) NOT NULL DEFAULT 'active'
                            CHECK (status IN ('active','inactive','maintenance')),
    kubernetes_version      VARCHAR(50),
    managed_by              VARCHAR(200),  -- EKS, GKE, AKS, on-prem, etc.
    created_at              TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- ============================================================
-- NODES
-- ============================================================
CREATE TABLE nodes (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name                    VARCHAR(200) NOT NULL,
    cluster_id              UUID NOT NULL REFERENCES clusters(id) ON DELETE CASCADE,
    zone_id                 UUID REFERENCES zones(id) ON DELETE SET NULL,
    provider_id             VARCHAR(500),           -- cloud provider instance ID
    instance_type           VARCHAR(100),
    operating_system        VARCHAR(100),
    kernel_version          VARCHAR(100),
    container_runtime       VARCHAR(100),
    allocatable_cpu         DECIMAL(10,3),           -- cores
    allocatable_memory_gb   DECIMAL(10,3),
    allocatable_pods        INTEGER,
    labels                  JSONB NOT NULL DEFAULT '{}',
    taints                  JSONB NOT NULL DEFAULT '[]',
    status                  VARCHAR(50) NOT NULL DEFAULT 'Ready'
                            CHECK (status IN ('Ready','NotReady','Unknown')),
    is_cordoned             BOOLEAN NOT NULL DEFAULT FALSE,
    is_migration_target     BOOLEAN NOT NULL DEFAULT TRUE,
    migration_opt_out       BOOLEAN NOT NULL DEFAULT FALSE,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(name, cluster_id)
);

CREATE INDEX idx_nodes_cluster ON nodes(cluster_id);
CREATE INDEX idx_nodes_zone    ON nodes(zone_id);

-- ============================================================
-- ENERGY READINGS  (time-series)
-- ============================================================
CREATE TABLE energy_readings (
    id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    zone_id               UUID NOT NULL REFERENCES zones(id) ON DELETE CASCADE,
    timestamp             TIMESTAMPTZ NOT NULL,
    carbon_intensity      DECIMAL(8,3),          -- gCO2eq/kWh
    renewable_percentage  DECIMAL(5,2),           -- 0-100
    energy_sources        JSONB,                  -- {"solar":20.5,"wind":35.2,"hydro":10.0,"coal":34.3}
    -- Generated: TRUE when renewable_percentage >= 50
    is_green              BOOLEAN GENERATED ALWAYS AS (renewable_percentage >= 50) STORED,
    data_source           VARCHAR(100),           -- electricity_maps | watttime | manual
    data_quality          VARCHAR(50) NOT NULL DEFAULT 'live'
                          CHECK (data_quality IN ('live','estimated','historical')),
    created_at            TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_energy_zone_time   ON energy_readings(zone_id, timestamp DESC);
CREATE INDEX idx_energy_is_green    ON energy_readings(is_green, timestamp DESC);
CREATE INDEX idx_energy_timestamp   ON energy_readings(timestamp DESC);

-- ============================================================
-- NODE METRICS  (time-series)
-- ============================================================
CREATE TABLE node_metrics (
    id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    node_id               UUID NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
    timestamp             TIMESTAMPTZ NOT NULL,
    cpu_usage_cores       DECIMAL(10,3),
    cpu_usage_percent     DECIMAL(5,2),
    memory_usage_gb       DECIMAL(10,3),
    memory_usage_percent  DECIMAL(5,2),
    pod_count             INTEGER,
    network_in_mbps       DECIMAL(10,3),
    network_out_mbps      DECIMAL(10,3),
    -- Generated: TRUE when CPU > 80% OR memory > 80%
    is_overloaded         BOOLEAN GENERATED ALWAYS AS (
                              cpu_usage_percent > 80 OR memory_usage_percent > 80
                          ) STORED,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_node_metrics_node_time ON node_metrics(node_id, timestamp DESC);

-- ============================================================
-- WORKLOADS
-- ============================================================
CREATE TABLE workloads (
    id                        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name                      VARCHAR(200) NOT NULL,
    namespace                 VARCHAR(200) NOT NULL DEFAULT 'default',
    cluster_id                UUID NOT NULL REFERENCES clusters(id) ON DELETE CASCADE,
    workload_type             VARCHAR(50) NOT NULL
                              CHECK (workload_type IN ('Deployment','StatefulSet','DaemonSet','ReplicaSet')),
    current_node_id           UUID REFERENCES nodes(id) ON DELETE SET NULL,
    replica_count             INTEGER NOT NULL DEFAULT 1,
    priority                  VARCHAR(50) NOT NULL DEFAULT 'normal'
                              CHECK (priority IN ('critical','high','normal','low')),
    migration_allowed         BOOLEAN NOT NULL DEFAULT TRUE,
    stateful                  BOOLEAN NOT NULL DEFAULT FALSE,
    labels                    JSONB NOT NULL DEFAULT '{}',
    annotations               JSONB NOT NULL DEFAULT '{}',
    resource_requests_cpu     DECIMAL(10,3),   -- cores
    resource_requests_memory_gb DECIMAL(10,3), -- GB
    last_seen_at              TIMESTAMPTZ,
    created_at                TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at                TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(name, namespace, cluster_id)
);

CREATE INDEX idx_workloads_cluster ON workloads(cluster_id);

-- ============================================================
-- AGENT RUNS
-- ============================================================
CREATE TABLE agent_runs (
    id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    started_at            TIMESTAMPTZ NOT NULL,
    completed_at          TIMESTAMPTZ,
    status                VARCHAR(50) NOT NULL DEFAULT 'running'
                          CHECK (status IN ('running','completed','failed')),
    clusters_evaluated    INTEGER NOT NULL DEFAULT 0,
    workloads_evaluated   INTEGER NOT NULL DEFAULT 0,
    migrations_initiated  INTEGER NOT NULL DEFAULT 0,
    error_message         TEXT,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- ============================================================
-- AI DECISIONS
-- ============================================================
CREATE TABLE ai_decisions (
    id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_run_id          UUID NOT NULL REFERENCES agent_runs(id) ON DELETE CASCADE,
    timestamp             TIMESTAMPTZ NOT NULL,
    model_name            VARCHAR(200),
    input_context         JSONB,        -- sanitized context snapshot sent to LLM
    reasoning             TEXT,         -- LLM chain-of-thought
    decision_type         VARCHAR(50)
                          CHECK (decision_type IN ('migrate','skip','wait','alert')),
    recommended_actions   JSONB,        -- list of planned migration actions
    safety_check_passed   BOOLEAN NOT NULL DEFAULT TRUE,
    safety_check_notes    TEXT,
    execution_started     BOOLEAN NOT NULL DEFAULT FALSE,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_ai_decisions_run ON ai_decisions(agent_run_id);

-- ============================================================
-- MIGRATION EVENTS
-- ============================================================
CREATE TABLE migration_events (
    id                          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workload_id                 UUID REFERENCES workloads(id) ON DELETE SET NULL,
    workload_name               VARCHAR(200) NOT NULL,  -- denormalized for history
    namespace                   VARCHAR(200) NOT NULL,
    cluster_id                  UUID REFERENCES clusters(id) ON DELETE SET NULL,
    source_node_id              UUID REFERENCES nodes(id) ON DELETE SET NULL,
    destination_node_id         UUID REFERENCES nodes(id) ON DELETE SET NULL,
    source_zone_id              UUID REFERENCES zones(id) ON DELETE SET NULL,
    destination_zone_id         UUID REFERENCES zones(id) ON DELETE SET NULL,
    migration_type              VARCHAR(50) NOT NULL DEFAULT 'node_selector'
                                CHECK (migration_type IN ('node_selector','taint_toleration','affinity')),
    status                      VARCHAR(50) NOT NULL DEFAULT 'pending'
                                CHECK (status IN ('pending','in_progress','completed','failed','rolled_back')),
    ai_decision_id              UUID REFERENCES ai_decisions(id) ON DELETE SET NULL,
    trigger_reason              TEXT,
    source_carbon_intensity     DECIMAL(8,3),   -- gCO2eq/kWh at time of migration
    destination_carbon_intensity DECIMAL(8,3),
    carbon_savings_estimate     DECIMAL(8,3),   -- estimated gCO2eq/kWh saved
    started_at                  TIMESTAMPTZ,
    completed_at                TIMESTAMPTZ,
    duration_seconds            INTEGER,
    error_message               TEXT,
    rollback_attempted          BOOLEAN NOT NULL DEFAULT FALSE,
    created_at                  TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at                  TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_migration_events_status     ON migration_events(status);
CREATE INDEX idx_migration_events_workload   ON migration_events(workload_id);
CREATE INDEX idx_migration_events_decision   ON migration_events(ai_decision_id);
CREATE INDEX idx_migration_events_cluster    ON migration_events(cluster_id);

-- ============================================================
-- VIEWS
-- ============================================================

-- Latest energy reading per zone
CREATE OR REPLACE VIEW zone_current_energy AS
SELECT DISTINCT ON (z.id)
    z.id                    AS zone_id,
    z.name                  AS zone_name,
    r.name                  AS region_name,
    er.carbon_intensity,
    er.renewable_percentage,
    er.is_green,
    er.energy_sources,
    er.timestamp            AS last_updated,
    er.data_quality
FROM zones z
JOIN regions r ON z.region_id = r.id
LEFT JOIN energy_readings er ON er.zone_id = z.id
ORDER BY z.id, er.timestamp DESC;

-- Node health, load, and zone greenness — primary AI agent query
CREATE OR REPLACE VIEW node_current_status AS
SELECT DISTINCT ON (n.id)
    n.id                        AS node_id,
    n.name                      AS node_name,
    c.name                      AS cluster_name,
    c.id                        AS cluster_id,
    z.name                      AS zone_name,
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
    nm.timestamp                AS metrics_last_updated,
    zce.is_green                AS zone_is_green,
    zce.carbon_intensity,
    zce.renewable_percentage,
    zce.last_updated            AS energy_last_updated
FROM nodes n
JOIN clusters c                 ON n.cluster_id = c.id
LEFT JOIN zones z               ON n.zone_id = z.id
LEFT JOIN node_metrics nm       ON nm.node_id = n.id
LEFT JOIN zone_current_energy zce ON zce.zone_id = n.zone_id
ORDER BY n.id, nm.timestamp DESC;

-- Carbon savings summary per cluster per day
CREATE OR REPLACE VIEW migration_carbon_summary AS
SELECT
    c.name                              AS cluster,
    DATE_TRUNC('day', me.completed_at)  AS day,
    COUNT(*)                            AS migrations_completed,
    SUM(me.carbon_savings_estimate)     AS total_carbon_saved_gco2,
    AVG(me.duration_seconds)            AS avg_migration_duration_s
FROM migration_events me
JOIN clusters c ON me.cluster_id = c.id
WHERE me.status = 'completed'
GROUP BY c.name, DATE_TRUNC('day', me.completed_at)
ORDER BY day DESC;

-- ============================================================
-- UPDATED_AT TRIGGER
-- ============================================================
CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = CURRENT_TIMESTAMP;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_regions_updated_at    BEFORE UPDATE ON regions    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER trg_zones_updated_at      BEFORE UPDATE ON zones      FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER trg_clusters_updated_at   BEFORE UPDATE ON clusters   FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER trg_nodes_updated_at      BEFORE UPDATE ON nodes      FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER trg_workloads_updated_at  BEFORE UPDATE ON workloads  FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER trg_migration_updated_at  BEFORE UPDATE ON migration_events FOR EACH ROW EXECUTE FUNCTION set_updated_at();
