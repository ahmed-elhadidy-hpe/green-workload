-- mysql -h 127.0.0.1 -D GREEN_WORKLOAD_DB  -u root -p < seed_data.sql
-- ============================================================
-- Green Workload AI — Comprehensive Seed Data
-- ============================================================
-- This script populates the database with realistic test data:
--   • 3 Regions, 6 Zones (3 green, 3 dirty)
--   • 3 Clusters, 8 Nodes
--   • 19 Workloads (10 migratable on dirty, 3 non-migratable on dirty, 1 opt-in StatefulSet on dirty, 5 on green)
--   • Energy readings (historical baseline)
--   • Node metrics (current state)
-- ============================================================

-- Deterministic UUIDs for reproducibility
-- Pattern: a1b2c3d4-TTTT-NNNN-0001-000000000001
--   TTTT = entity type (0001=region, 0002=zone, 0003=cluster, 0004=node, 0005=workload)
--   NNNN = sequential number

-- ============================================================
-- REGIONS
-- ============================================================
INSERT IGNORE INTO regions (id, name, display_name, country_code, latitude, longitude) VALUES
  ('a1b2c3d4-0001-0001-0001-000000000001', 'us-west-2',       'US West (Oregon)',     'US', 45.5231, -122.6765),
  ('a1b2c3d4-0001-0002-0001-000000000001', 'eu-west-1',       'EU West (Ireland)',    'IE', 53.3498,  -6.2603),
  ('a1b2c3d4-0001-0003-0001-000000000001', 'ap-southeast-1',  'Asia Pacific (Singapore)', 'SG', 1.3521, 103.8198);

-- ============================================================
-- ZONES  (3 green-energy, 3 dirty-energy)
-- ============================================================
INSERT IGNORE INTO zones (id, name, region_id, display_name, energy_provider, electricitymap_zone, watttime_ba) VALUES
  -- GREEN zones
  ('a1b2c3d4-0002-0001-0001-000000000001', 'us-west-2a',       'a1b2c3d4-0001-0001-0001-000000000001',
   'US West 2a (Solar/Wind)', 'Pacific Gas & Electric', 'US-CAL-CISO', 'CAISO_NP26'),
  ('a1b2c3d4-0002-0003-0001-000000000001', 'eu-west-1a',       'a1b2c3d4-0001-0002-0001-000000000001',
   'EU West 1a (Wind/Hydro)', 'ESB Networks', 'IE', 'IE'),
  ('a1b2c3d4-0002-0005-0001-000000000001', 'ap-southeast-1b',  'a1b2c3d4-0001-0003-0001-000000000001',
   'AP Southeast 1b (Solar)', 'SP Group', 'SG', 'SG'),
  -- DIRTY zones
  ('a1b2c3d4-0002-0002-0001-000000000001', 'us-west-2b',       'a1b2c3d4-0001-0001-0001-000000000001',
   'US West 2b (Coal Heavy)', 'AEP', 'US-MIDA-PJM', 'PJM_DOM'),
  ('a1b2c3d4-0002-0004-0001-000000000001', 'eu-west-1b',       'a1b2c3d4-0001-0002-0001-000000000001',
   'EU West 1b (Coal/Gas)', 'Drax Power', 'GB', 'GB'),
  ('a1b2c3d4-0002-0006-0001-000000000001', 'ap-southeast-1a',  'a1b2c3d4-0001-0003-0001-000000000001',
   'AP Southeast 1a (Natural Gas)', 'Senoko Energy', 'SG', 'SG');

-- ============================================================
-- CLUSTERS
-- ============================================================
INSERT IGNORE INTO clusters (id, name, display_name, api_endpoint, region_id, status, managed_by) VALUES
  ('a1b2c3d4-0003-0001-0001-000000000001', 'test-cluster',  'US West Cluster',       'https://k8s-us.example.com',  'a1b2c3d4-0001-0001-0001-000000000001', 'active', 'EKS'),
  ('a1b2c3d4-0003-0002-0001-000000000001', 'eu-cluster',    'EU West Cluster',       'https://k8s-eu.example.com',  'a1b2c3d4-0001-0002-0001-000000000001', 'active', 'EKS'),
  ('a1b2c3d4-0003-0003-0001-000000000001', 'ap-cluster',    'AP Southeast Cluster',  'https://k8s-ap.example.com',  'a1b2c3d4-0001-0003-0001-000000000001', 'active', 'EKS');

-- ============================================================
-- NODES  (4 on green zones, 4 on dirty zones)
-- ============================================================
INSERT IGNORE INTO nodes (id, name, cluster_id, zone_id, instance_type, allocatable_cpu, allocatable_memory_gb, allocatable_pods, status, is_migration_target) VALUES
  -- US WEST cluster
  ('a1b2c3d4-0004-0001-0001-000000000001', 'node-green-1',     'a1b2c3d4-0003-0001-0001-000000000001', 'a1b2c3d4-0002-0001-0001-000000000001',
   'm5.xlarge', 4.000, 16.000, 110, 'Ready', 1),
  ('a1b2c3d4-0004-0002-0001-000000000001', 'node-coal-1',      'a1b2c3d4-0003-0001-0001-000000000001', 'a1b2c3d4-0002-0002-0001-000000000001',
   'm5.xlarge', 4.000, 16.000, 110, 'Ready', 1),
  ('a1b2c3d4-0004-0003-0001-000000000001', 'node-green-2',     'a1b2c3d4-0003-0001-0001-000000000001', 'a1b2c3d4-0002-0001-0001-000000000001',
   'm5.2xlarge', 8.000, 32.000, 110, 'Ready', 1),
  -- EU WEST cluster
  ('a1b2c3d4-0004-0004-0001-000000000001', 'node-eu-green-1',  'a1b2c3d4-0003-0002-0001-000000000001', 'a1b2c3d4-0002-0003-0001-000000000001',
   'm5.xlarge', 4.000, 16.000, 110, 'Ready', 1),
  ('a1b2c3d4-0004-0005-0001-000000000001', 'node-eu-dirty-1',  'a1b2c3d4-0003-0002-0001-000000000001', 'a1b2c3d4-0002-0004-0001-000000000001',
   'm5.xlarge', 4.000, 16.000, 110, 'Ready', 1),
  ('a1b2c3d4-0004-0006-0001-000000000001', 'node-eu-dirty-2',  'a1b2c3d4-0003-0002-0001-000000000001', 'a1b2c3d4-0002-0004-0001-000000000001',
   'm5.2xlarge', 8.000, 32.000, 110, 'Ready', 1),
  -- AP SOUTHEAST cluster
  ('a1b2c3d4-0004-0007-0001-000000000001', 'node-ap-dirty-1',  'a1b2c3d4-0003-0003-0001-000000000001', 'a1b2c3d4-0002-0006-0001-000000000001',
   'm5.xlarge', 4.000, 16.000, 110, 'Ready', 1),
  ('a1b2c3d4-0004-0008-0001-000000000001', 'node-ap-green-1',  'a1b2c3d4-0003-0003-0001-000000000001', 'a1b2c3d4-0002-0005-0001-000000000001',
   'm5.2xlarge', 8.000, 32.000, 110, 'Ready', 1);

-- ============================================================
-- WORKLOADS  (18 total — most on dirty nodes, some on green, some non-migratable)
-- ============================================================
-- US WEST: 7 workloads (4 migratable on dirty, 1 non-migratable on dirty, 2 on green)
INSERT IGNORE INTO workloads (id, name, namespace, cluster_id, workload_type, current_node_id, replica_count, priority, migration_allowed, stateful, resource_requests_cpu, resource_requests_memory_gb, last_seen_at) VALUES
  ('a1b2c3d4-0005-0001-0001-000000000001', 'api-gateway',       'production',  'a1b2c3d4-0003-0001-0001-000000000001', 'Deployment',  'a1b2c3d4-0004-0002-0001-000000000001', 3, 'high',     1, 0, 0.500, 1.000, NOW()),
  ('a1b2c3d4-0005-0002-0001-000000000001', 'order-service',     'production',  'a1b2c3d4-0003-0001-0001-000000000001', 'Deployment',  'a1b2c3d4-0004-0002-0001-000000000001', 2, 'normal',   1, 0, 0.250, 0.512, NOW()),
  ('a1b2c3d4-0005-0003-0001-000000000001', 'payment-processor', 'production',  'a1b2c3d4-0003-0001-0001-000000000001', 'Deployment',  'a1b2c3d4-0004-0002-0001-000000000001', 2, 'critical', 1, 0, 1.000, 2.000, NOW()),
  ('a1b2c3d4-0005-0004-0001-000000000001', 'batch-jobs',        'batch',       'a1b2c3d4-0003-0001-0001-000000000001', 'Deployment',  'a1b2c3d4-0004-0002-0001-000000000001', 1, 'normal',   1, 0, 0.200, 0.256, NOW()),
  ('a1b2c3d4-0005-0005-0001-000000000001', 'monitoring-agent',  'monitoring',  'a1b2c3d4-0003-0001-0001-000000000001', 'DaemonSet',   'a1b2c3d4-0004-0001-0001-000000000001', 1, 'normal',   0, 0, 0.100, 0.128, NOW()),
  ('a1b2c3d4-0005-0006-0001-000000000001', 'cache-redis',       'production',  'a1b2c3d4-0003-0001-0001-000000000001', 'StatefulSet', 'a1b2c3d4-0004-0001-0001-000000000001', 1, 'high',     1, 1, 0.500, 4.000, NOW()),
  ('a1b2c3d4-0005-0016-0001-000000000001', 'database-primary',  'production',  'a1b2c3d4-0003-0001-0001-000000000001', 'StatefulSet', 'a1b2c3d4-0004-0002-0001-000000000001', 1, 'critical', 1, 1, 2.000, 8.000, NOW()),

-- EU WEST: 7 workloads (3 migratable on dirty, 1 non-migratable on dirty, 1 opt-in StatefulSet on dirty, 2 on green)
  ('a1b2c3d4-0005-0007-0001-000000000001', 'web-frontend',      'production',  'a1b2c3d4-0003-0002-0001-000000000001', 'Deployment',  'a1b2c3d4-0004-0005-0001-000000000001', 4, 'high',     1, 0, 0.500, 1.000, NOW()),
  ('a1b2c3d4-0005-0008-0001-000000000001', 'user-service',      'production',  'a1b2c3d4-0003-0002-0001-000000000001', 'Deployment',  'a1b2c3d4-0004-0005-0001-000000000001', 2, 'normal',   1, 0, 0.250, 0.512, NOW()),
  ('a1b2c3d4-0005-0009-0001-000000000001', 'analytics-worker',  'analytics',   'a1b2c3d4-0003-0002-0001-000000000001', 'Deployment',  'a1b2c3d4-0004-0006-0001-000000000001', 3, 'normal',   1, 0, 1.500, 4.000, NOW()),
  ('a1b2c3d4-0005-0010-0001-000000000001', 'logging-pipeline',  'monitoring',  'a1b2c3d4-0003-0002-0001-000000000001', 'DaemonSet',   'a1b2c3d4-0004-0004-0001-000000000001', 1, 'normal',   0, 0, 0.100, 0.256, NOW()),
  ('a1b2c3d4-0005-0011-0001-000000000001', 'search-engine',     'production',  'a1b2c3d4-0003-0002-0001-000000000001', 'Deployment',  'a1b2c3d4-0004-0004-0001-000000000001', 2, 'high',     1, 0, 0.750, 2.000, NOW()),
  ('a1b2c3d4-0005-0017-0001-000000000001', 'dns-resolver',      'infra',       'a1b2c3d4-0003-0002-0001-000000000001', 'DaemonSet',   'a1b2c3d4-0004-0006-0001-000000000001', 1, 'high',     0, 0, 0.050, 0.064, NOW()),

-- AP SOUTHEAST: 5 workloads (3 migratable on dirty, 1 non-migratable on dirty, 1 on green)
  ('a1b2c3d4-0005-0012-0001-000000000001', 'image-processor',   'media',       'a1b2c3d4-0003-0003-0001-000000000001', 'Deployment',  'a1b2c3d4-0004-0007-0001-000000000001', 2, 'normal',   1, 0, 2.000, 4.000, NOW()),
  ('a1b2c3d4-0005-0013-0001-000000000001', 'notification-svc',  'production',  'a1b2c3d4-0003-0003-0001-000000000001', 'Deployment',  'a1b2c3d4-0004-0007-0001-000000000001', 2, 'normal',   1, 0, 0.250, 0.512, NOW()),
  ('a1b2c3d4-0005-0014-0001-000000000001', 'ml-training',       'ml',          'a1b2c3d4-0003-0003-0001-000000000001', 'Deployment',  'a1b2c3d4-0004-0007-0001-000000000001', 1, 'normal',   1, 0, 3.000, 8.000, NOW()),
  ('a1b2c3d4-0005-0015-0001-000000000001', 'cdn-origin',        'production',  'a1b2c3d4-0003-0003-0001-000000000001', 'Deployment',  'a1b2c3d4-0004-0008-0001-000000000001', 2, 'high',     1, 0, 0.500, 1.000, NOW()),
  ('a1b2c3d4-0005-0018-0001-000000000001', 'security-scanner',  'security',    'a1b2c3d4-0003-0003-0001-000000000001', 'Deployment',  'a1b2c3d4-0004-0007-0001-000000000001', 1, 'critical', 0, 0, 0.100, 0.256, NOW());

-- Opt-in StatefulSet on a dirty EU node (has the required migration annotation)
INSERT IGNORE INTO workloads (id, name, namespace, cluster_id, workload_type, current_node_id, replica_count, priority, migration_allowed, stateful, resource_requests_cpu, resource_requests_memory_gb, annotations, last_seen_at) VALUES
  ('a1b2c3d4-0005-0019-0001-000000000001', 'session-store-redis', 'production', 'a1b2c3d4-0003-0002-0001-000000000001', 'StatefulSet', 'a1b2c3d4-0004-0005-0001-000000000001', 1, 'high', 1, 1, 0.500, 2.000, '{"green-workload/migration-allowed": "true"}', NOW());

-- ============================================================
-- ENERGY READINGS — Baseline (last 6 hours, one per hour)
-- Green zones:  renewable 65-85%, carbon intensity 45-120 gCO2/kWh
-- Dirty zones:  renewable 8-25%,  carbon intensity 420-780 gCO2/kWh
-- ============================================================

-- Helper: insert 6 hours of readings per zone
-- US-WEST-2A  (GREEN — Solar/Wind)
INSERT INTO energy_readings (id, zone_id, timestamp, carbon_intensity, renewable_percentage, energy_sources, data_source, data_quality) VALUES
  (UUID(), 'a1b2c3d4-0002-0001-0001-000000000001', DATE_SUB(NOW(), INTERVAL 6 HOUR), 110.500, 68.00, '{"solar": 40, "wind": 20, "hydro": 8, "natural_gas": 22, "coal": 10}',              'simulation', 'live'),
  (UUID(), 'a1b2c3d4-0002-0001-0001-000000000001', DATE_SUB(NOW(), INTERVAL 5 HOUR), 95.200,  72.50, '{"solar": 45, "wind": 18, "hydro": 9.5, "natural_gas": 20, "coal": 7.5}',            'simulation', 'live'),
  (UUID(), 'a1b2c3d4-0002-0001-0001-000000000001', DATE_SUB(NOW(), INTERVAL 4 HOUR), 78.100,  78.00, '{"solar": 50, "wind": 18, "hydro": 10, "natural_gas": 15, "coal": 7}',               'simulation', 'live'),
  (UUID(), 'a1b2c3d4-0002-0001-0001-000000000001', DATE_SUB(NOW(), INTERVAL 3 HOUR), 65.800,  82.00, '{"solar": 55, "wind": 17, "hydro": 10, "natural_gas": 12, "coal": 6}',               'simulation', 'live'),
  (UUID(), 'a1b2c3d4-0002-0001-0001-000000000001', DATE_SUB(NOW(), INTERVAL 2 HOUR), 72.400,  80.00, '{"solar": 52, "wind": 18, "hydro": 10, "natural_gas": 14, "coal": 6}',               'simulation', 'live'),
  (UUID(), 'a1b2c3d4-0002-0001-0001-000000000001', DATE_SUB(NOW(), INTERVAL 1 HOUR), 85.300,  75.00, '{"solar": 48, "wind": 17, "hydro": 10, "natural_gas": 18, "coal": 7}',               'simulation', 'live');

-- US-WEST-2B  (DIRTY — Coal Heavy)
INSERT INTO energy_readings (id, zone_id, timestamp, carbon_intensity, renewable_percentage, energy_sources, data_source, data_quality) VALUES
  (UUID(), 'a1b2c3d4-0002-0002-0001-000000000001', DATE_SUB(NOW(), INTERVAL 6 HOUR), 685.200, 12.00, '{"coal": 55, "natural_gas": 30, "nuclear": 3, "wind": 7, "solar": 5}',               'simulation', 'live'),
  (UUID(), 'a1b2c3d4-0002-0002-0001-000000000001', DATE_SUB(NOW(), INTERVAL 5 HOUR), 710.400, 10.50, '{"coal": 58, "natural_gas": 28, "nuclear": 3.5, "wind": 6, "solar": 4.5}',           'simulation', 'live'),
  (UUID(), 'a1b2c3d4-0002-0002-0001-000000000001', DATE_SUB(NOW(), INTERVAL 4 HOUR), 740.100, 9.00,  '{"coal": 60, "natural_gas": 28, "nuclear": 3, "wind": 5, "solar": 4}',               'simulation', 'live'),
  (UUID(), 'a1b2c3d4-0002-0002-0001-000000000001', DATE_SUB(NOW(), INTERVAL 3 HOUR), 720.800, 11.00, '{"coal": 57, "natural_gas": 29, "nuclear": 3, "wind": 6, "solar": 5}',               'simulation', 'live'),
  (UUID(), 'a1b2c3d4-0002-0002-0001-000000000001', DATE_SUB(NOW(), INTERVAL 2 HOUR), 698.500, 13.50, '{"coal": 54, "natural_gas": 29, "nuclear": 3.5, "wind": 7, "solar": 6.5}',           'simulation', 'live'),
  (UUID(), 'a1b2c3d4-0002-0002-0001-000000000001', DATE_SUB(NOW(), INTERVAL 1 HOUR), 725.000, 10.00, '{"coal": 58, "natural_gas": 29, "nuclear": 3, "wind": 5, "solar": 5}',               'simulation', 'live');

-- EU-WEST-1A  (GREEN — Wind/Hydro)
INSERT INTO energy_readings (id, zone_id, timestamp, carbon_intensity, renewable_percentage, energy_sources, data_source, data_quality) VALUES
  (UUID(), 'a1b2c3d4-0002-0003-0001-000000000001', DATE_SUB(NOW(), INTERVAL 6 HOUR), 95.400,  70.00, '{"wind": 45, "hydro": 15, "solar": 10, "natural_gas": 20, "coal": 10}',              'simulation', 'live'),
  (UUID(), 'a1b2c3d4-0002-0003-0001-000000000001', DATE_SUB(NOW(), INTERVAL 5 HOUR), 82.100,  76.00, '{"wind": 50, "hydro": 16, "solar": 10, "natural_gas": 16, "coal": 8}',               'simulation', 'live'),
  (UUID(), 'a1b2c3d4-0002-0003-0001-000000000001', DATE_SUB(NOW(), INTERVAL 4 HOUR), 68.300,  83.00, '{"wind": 55, "hydro": 18, "solar": 10, "natural_gas": 12, "coal": 5}',               'simulation', 'live'),
  (UUID(), 'a1b2c3d4-0002-0003-0001-000000000001', DATE_SUB(NOW(), INTERVAL 3 HOUR), 55.700,  88.00, '{"wind": 60, "hydro": 18, "solar": 10, "natural_gas": 8, "coal": 4}',                'simulation', 'live'),
  (UUID(), 'a1b2c3d4-0002-0003-0001-000000000001', DATE_SUB(NOW(), INTERVAL 2 HOUR), 62.900,  85.00, '{"wind": 57, "hydro": 18, "solar": 10, "natural_gas": 10, "coal": 5}',               'simulation', 'live'),
  (UUID(), 'a1b2c3d4-0002-0003-0001-000000000001', DATE_SUB(NOW(), INTERVAL 1 HOUR), 58.200,  86.50, '{"wind": 58, "hydro": 18.5, "solar": 10, "natural_gas": 9, "coal": 4.5}',            'simulation', 'live');

-- EU-WEST-1B  (DIRTY — Coal/Gas)
INSERT INTO energy_readings (id, zone_id, timestamp, carbon_intensity, renewable_percentage, energy_sources, data_source, data_quality) VALUES
  (UUID(), 'a1b2c3d4-0002-0004-0001-000000000001', DATE_SUB(NOW(), INTERVAL 6 HOUR), 520.300, 18.00, '{"coal": 42, "natural_gas": 35, "nuclear": 5, "wind": 10, "solar": 8}',              'simulation', 'live'),
  (UUID(), 'a1b2c3d4-0002-0004-0001-000000000001', DATE_SUB(NOW(), INTERVAL 5 HOUR), 545.100, 15.50, '{"coal": 44, "natural_gas": 36, "nuclear": 4.5, "wind": 9, "solar": 6.5}',           'simulation', 'live'),
  (UUID(), 'a1b2c3d4-0002-0004-0001-000000000001', DATE_SUB(NOW(), INTERVAL 4 HOUR), 580.700, 13.00, '{"coal": 48, "natural_gas": 35, "nuclear": 4, "wind": 8, "solar": 5}',               'simulation', 'live'),
  (UUID(), 'a1b2c3d4-0002-0004-0001-000000000001', DATE_SUB(NOW(), INTERVAL 3 HOUR), 560.200, 16.00, '{"coal": 45, "natural_gas": 35, "nuclear": 4, "wind": 9, "solar": 7}',               'simulation', 'live'),
  (UUID(), 'a1b2c3d4-0002-0004-0001-000000000001', DATE_SUB(NOW(), INTERVAL 2 HOUR), 535.800, 19.00, '{"coal": 42, "natural_gas": 34, "nuclear": 5, "wind": 11, "solar": 8}',              'simulation', 'live'),
  (UUID(), 'a1b2c3d4-0002-0004-0001-000000000001', DATE_SUB(NOW(), INTERVAL 1 HOUR), 555.000, 17.00, '{"coal": 44, "natural_gas": 35, "nuclear": 4, "wind": 10, "solar": 7}',              'simulation', 'live');

-- AP-SOUTHEAST-1B  (GREEN — Solar)
INSERT INTO energy_readings (id, zone_id, timestamp, carbon_intensity, renewable_percentage, energy_sources, data_source, data_quality) VALUES
  (UUID(), 'a1b2c3d4-0002-0005-0001-000000000001', DATE_SUB(NOW(), INTERVAL 6 HOUR), 105.200, 66.00, '{"solar": 50, "wind": 6, "hydro": 10, "natural_gas": 28, "coal": 6}',               'simulation', 'live'),
  (UUID(), 'a1b2c3d4-0002-0005-0001-000000000001', DATE_SUB(NOW(), INTERVAL 5 HOUR), 88.700,  73.00, '{"solar": 58, "wind": 5, "hydro": 10, "natural_gas": 22, "coal": 5}',               'simulation', 'live'),
  (UUID(), 'a1b2c3d4-0002-0005-0001-000000000001', DATE_SUB(NOW(), INTERVAL 4 HOUR), 72.500,  80.00, '{"solar": 65, "wind": 5, "hydro": 10, "natural_gas": 16, "coal": 4}',               'simulation', 'live'),
  (UUID(), 'a1b2c3d4-0002-0005-0001-000000000001', DATE_SUB(NOW(), INTERVAL 3 HOUR), 60.100,  85.00, '{"solar": 70, "wind": 5, "hydro": 10, "natural_gas": 12, "coal": 3}',               'simulation', 'live'),
  (UUID(), 'a1b2c3d4-0002-0005-0001-000000000001', DATE_SUB(NOW(), INTERVAL 2 HOUR), 68.400,  82.00, '{"solar": 67, "wind": 5, "hydro": 10, "natural_gas": 14, "coal": 4}',               'simulation', 'live'),
  (UUID(), 'a1b2c3d4-0002-0005-0001-000000000001', DATE_SUB(NOW(), INTERVAL 1 HOUR), 75.900,  78.00, '{"solar": 62, "wind": 6, "hydro": 10, "natural_gas": 17, "coal": 5}',               'simulation', 'live');

-- AP-SOUTHEAST-1A  (DIRTY — Natural Gas)
INSERT INTO energy_readings (id, zone_id, timestamp, carbon_intensity, renewable_percentage, energy_sources, data_source, data_quality) VALUES
  (UUID(), 'a1b2c3d4-0002-0006-0001-000000000001', DATE_SUB(NOW(), INTERVAL 6 HOUR), 465.800, 22.00, '{"natural_gas": 55, "coal": 18, "nuclear": 5, "solar": 12, "wind": 10}',             'simulation', 'live'),
  (UUID(), 'a1b2c3d4-0002-0006-0001-000000000001', DATE_SUB(NOW(), INTERVAL 5 HOUR), 490.200, 19.00, '{"natural_gas": 58, "coal": 18, "nuclear": 5, "solar": 10, "wind": 9}',              'simulation', 'live'),
  (UUID(), 'a1b2c3d4-0002-0006-0001-000000000001', DATE_SUB(NOW(), INTERVAL 4 HOUR), 510.600, 16.50, '{"natural_gas": 60, "coal": 19, "nuclear": 4.5, "solar": 9, "wind": 7.5}',           'simulation', 'live'),
  (UUID(), 'a1b2c3d4-0002-0006-0001-000000000001', DATE_SUB(NOW(), INTERVAL 3 HOUR), 485.100, 20.00, '{"natural_gas": 57, "coal": 18, "nuclear": 5, "solar": 11, "wind": 9}',              'simulation', 'live'),
  (UUID(), 'a1b2c3d4-0002-0006-0001-000000000001', DATE_SUB(NOW(), INTERVAL 2 HOUR), 475.300, 21.50, '{"natural_gas": 55, "coal": 18, "nuclear": 5.5, "solar": 12, "wind": 9.5}',          'simulation', 'live'),
  (UUID(), 'a1b2c3d4-0002-0006-0001-000000000001', DATE_SUB(NOW(), INTERVAL 1 HOUR), 498.000, 18.00, '{"natural_gas": 59, "coal": 18, "nuclear": 5, "solar": 10, "wind": 8}',              'simulation', 'live');

-- ============================================================
-- NODE METRICS — Current baseline snapshot
-- Green nodes: moderate load (35-55% CPU/memory), plenty of headroom
-- Dirty nodes: higher load (45-70% CPU/memory)
-- ============================================================

-- node-green-1 (US, green zone, 4 CPU / 16 GB)
INSERT INTO node_metrics (id, node_id, timestamp, cpu_usage_cores, cpu_usage_percent, memory_usage_gb, memory_usage_percent, pod_count, network_in_mbps, network_out_mbps) VALUES
  (UUID(), 'a1b2c3d4-0004-0001-0001-000000000001', DATE_SUB(NOW(), INTERVAL 1 HOUR), 1.400, 35.00, 5.600,  35.00, 12, 120.5, 85.3),
  (UUID(), 'a1b2c3d4-0004-0001-0001-000000000001', NOW(),                             1.600, 40.00, 6.400,  40.00, 14, 135.2, 92.1);

-- node-coal-1 (US, dirty zone, 4 CPU / 16 GB) — moderately loaded
INSERT INTO node_metrics (id, node_id, timestamp, cpu_usage_cores, cpu_usage_percent, memory_usage_gb, memory_usage_percent, pod_count, network_in_mbps, network_out_mbps) VALUES
  (UUID(), 'a1b2c3d4-0004-0002-0001-000000000001', DATE_SUB(NOW(), INTERVAL 1 HOUR), 2.600, 65.00, 10.400, 65.00, 28, 250.1, 180.5),
  (UUID(), 'a1b2c3d4-0004-0002-0001-000000000001', NOW(),                             2.800, 70.00, 11.200, 70.00, 30, 265.4, 195.2);

-- node-green-2 (US, green zone, 8 CPU / 32 GB) — light load with headroom
INSERT INTO node_metrics (id, node_id, timestamp, cpu_usage_cores, cpu_usage_percent, memory_usage_gb, memory_usage_percent, pod_count, network_in_mbps, network_out_mbps) VALUES
  (UUID(), 'a1b2c3d4-0004-0003-0001-000000000001', DATE_SUB(NOW(), INTERVAL 1 HOUR), 1.200, 15.00, 4.800,  15.00, 6,  55.3,  32.1),
  (UUID(), 'a1b2c3d4-0004-0003-0001-000000000001', NOW(),                             1.600, 20.00, 6.400,  20.00, 8,  68.7,  41.5);

-- node-eu-green-1 (EU, green zone, 4 CPU / 16 GB)
INSERT INTO node_metrics (id, node_id, timestamp, cpu_usage_cores, cpu_usage_percent, memory_usage_gb, memory_usage_percent, pod_count, network_in_mbps, network_out_mbps) VALUES
  (UUID(), 'a1b2c3d4-0004-0004-0001-000000000001', DATE_SUB(NOW(), INTERVAL 1 HOUR), 1.600, 40.00, 7.200,  45.00, 18, 145.2, 98.5),
  (UUID(), 'a1b2c3d4-0004-0004-0001-000000000001', NOW(),                             1.800, 45.00, 7.680,  48.00, 20, 158.3, 105.8);

-- node-eu-dirty-1 (EU, dirty zone, 4 CPU / 16 GB) — high load
INSERT INTO node_metrics (id, node_id, timestamp, cpu_usage_cores, cpu_usage_percent, memory_usage_gb, memory_usage_percent, pod_count, network_in_mbps, network_out_mbps) VALUES
  (UUID(), 'a1b2c3d4-0004-0005-0001-000000000001', DATE_SUB(NOW(), INTERVAL 1 HOUR), 2.400, 60.00, 9.600,  60.00, 22, 200.1, 155.3),
  (UUID(), 'a1b2c3d4-0004-0005-0001-000000000001', NOW(),                             2.600, 65.00, 10.400, 65.00, 24, 215.4, 168.2);

-- node-eu-dirty-2 (EU, dirty zone, 8 CPU / 32 GB) — moderate load
INSERT INTO node_metrics (id, node_id, timestamp, cpu_usage_cores, cpu_usage_percent, memory_usage_gb, memory_usage_percent, pod_count, network_in_mbps, network_out_mbps) VALUES
  (UUID(), 'a1b2c3d4-0004-0006-0001-000000000001', DATE_SUB(NOW(), INTERVAL 1 HOUR), 4.000, 50.00, 16.000, 50.00, 15, 310.5, 245.2),
  (UUID(), 'a1b2c3d4-0004-0006-0001-000000000001', NOW(),                             4.400, 55.00, 17.600, 55.00, 17, 335.8, 260.1);

-- node-ap-dirty-1 (AP, dirty zone, 4 CPU / 16 GB) — high load
INSERT INTO node_metrics (id, node_id, timestamp, cpu_usage_cores, cpu_usage_percent, memory_usage_gb, memory_usage_percent, pod_count, network_in_mbps, network_out_mbps) VALUES
  (UUID(), 'a1b2c3d4-0004-0007-0001-000000000001', DATE_SUB(NOW(), INTERVAL 1 HOUR), 2.800, 70.00, 11.200, 70.00, 26, 280.3, 210.5),
  (UUID(), 'a1b2c3d4-0004-0007-0001-000000000001', NOW(),                             3.000, 75.00, 12.000, 75.00, 28, 295.1, 225.8);

-- node-ap-green-1 (AP, green zone, 8 CPU / 32 GB) — light load, lots of headroom
INSERT INTO node_metrics (id, node_id, timestamp, cpu_usage_cores, cpu_usage_percent, memory_usage_gb, memory_usage_percent, pod_count, network_in_mbps, network_out_mbps) VALUES
  (UUID(), 'a1b2c3d4-0004-0008-0001-000000000001', DATE_SUB(NOW(), INTERVAL 1 HOUR), 2.000, 25.00, 8.000,  25.00, 10, 95.2,  62.1),
  (UUID(), 'a1b2c3d4-0004-0008-0001-000000000001', NOW(),                             2.400, 30.00, 9.600,  30.00, 12, 110.5, 75.3);

-- ============================================================
-- VERIFICATION QUERIES (uncomment to validate)
-- ============================================================
-- SELECT z.name, z.display_name, e.carbon_intensity, e.renewable_percentage, e.is_green
-- FROM zones z
-- LEFT JOIN energy_readings e ON e.zone_id = z.id
-- WHERE e.timestamp = (SELECT MAX(er.timestamp) FROM energy_readings er WHERE er.zone_id = z.id)
-- ORDER BY e.is_green DESC, e.carbon_intensity ASC;
--
-- SELECT * FROM node_current_status;
--
-- SELECT w.name, w.namespace, n.name as node_name, z.name as zone_name
-- FROM workloads w
-- JOIN nodes n ON w.current_node_id = n.id
-- JOIN zones z ON n.zone_id = z.id
-- ORDER BY z.name;
