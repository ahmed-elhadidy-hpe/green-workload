import pymysql
import os
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Hardcoded UUIDs for deterministic seed data
REGION_ID  = "a1b2c3d4-0001-0001-0001-000000000001"
ZONE1_ID   = "a1b2c3d4-0002-0001-0001-000000000001"
ZONE2_ID   = "a1b2c3d4-0002-0002-0001-000000000001"
CLUSTER_ID = "a1b2c3d4-0003-0001-0001-000000000001"
NODE1_ID   = "a1b2c3d4-0004-0001-0001-000000000001"
NODE2_ID   = "a1b2c3d4-0004-0002-0001-000000000001"


def get_connection_params():
    return {
        "host": os.environ.get("DB_HOST", "127.0.0.1"),
        "port": int(os.environ.get("DB_PORT", "3306")),
        "user": os.environ.get("DB_USER", "root"),
        "password": os.environ.get("DB_PASSWORD", ""),
    }


def setup_database():
    """Create database and run schema."""
    params = get_connection_params()
    db_name = os.environ.get("DB_NAME", "GREEN_WORKLOAD_DB")

    # Load .env if present
    env_path = os.path.join(BASE_DIR, ".env")
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, _, v = line.partition("=")
                    os.environ.setdefault(k.strip(), v.strip())
        params = get_connection_params()
        db_name = os.environ.get("DB_NAME", "GREEN_WORKLOAD_DB")

    print(f"Connecting to MySQL at {params['host']}:{params['port']} as {params['user']}...")

    # Create DB if not exists
    conn = pymysql.connect(**params, charset="utf8mb4", autocommit=True)
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                f"CREATE DATABASE IF NOT EXISTS `{db_name}` "
                f"CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
            )
            print(f"Database '{db_name}' ensured.")
    finally:
        conn.close()

    # Connect to the database
    conn = pymysql.connect(**params, db=db_name, charset="utf8mb4", autocommit=True)
    try:
        schema_path = os.path.join(BASE_DIR, "planning", "db", "mysql_schema.sql")
        print(f"Reading schema from {schema_path}...")
        with open(schema_path, "r") as f:
            sql_content = f.read()

        # Split and execute each statement
        statements = [s.strip() for s in sql_content.split(";") if s.strip()]
        with conn.cursor() as cursor:
            for stmt in statements:
                if not stmt:
                    continue
                try:
                    cursor.execute(stmt)
                except pymysql.err.OperationalError as e:
                    if e.args[0] in (1050, 1060, 1061):
                        print(f"  [SKIP] Already exists: {e.args[1]}")
                    else:
                        print(f"  [WARN] {e}")
                except Exception as e:
                    print(f"  [WARN] Statement failed: {e}")

        print("Schema applied.")

        # Insert seed data
        print("Inserting seed data...")
        seed_statements = [
            f"""INSERT IGNORE INTO regions (id, name, display_name, country_code, latitude, longitude)
                VALUES ('{REGION_ID}', 'us-west-2', 'US West (Oregon)', 'US', 45.5231, -122.6765)""",

            f"""INSERT IGNORE INTO zones (id, name, region_id, display_name, energy_provider,
                electricitymap_zone, watttime_ba)
                VALUES ('{ZONE1_ID}', 'us-west-2a', '{REGION_ID}', 'US West 2a (Solar)',
                'Pacific Gas & Electric', 'US-CAL-CISO', 'CAISO_NP26')""",

            f"""INSERT IGNORE INTO zones (id, name, region_id, display_name, energy_provider,
                electricitymap_zone, watttime_ba)
                VALUES ('{ZONE2_ID}', 'us-west-2b', '{REGION_ID}', 'US West 2b (Coal)',
                'AEP', 'US-MIDA-PJM', 'PJM_DOM')""",

            f"""INSERT IGNORE INTO clusters (id, name, display_name, api_endpoint, region_id,
                status, managed_by)
                VALUES ('{CLUSTER_ID}', 'test-cluster', 'Test Cluster',
                'https://k8s.example.com', '{REGION_ID}', 'active', 'EKS')""",

            f"""INSERT IGNORE INTO nodes (id, name, cluster_id, zone_id, instance_type,
                status, is_migration_target)
                VALUES ('{NODE1_ID}', 'node-green-1', '{CLUSTER_ID}', '{ZONE1_ID}',
                'm5.xlarge', 'Ready', 1)""",

            f"""INSERT IGNORE INTO nodes (id, name, cluster_id, zone_id, instance_type,
                status, is_migration_target)
                VALUES ('{NODE2_ID}', 'node-coal-1', '{CLUSTER_ID}', '{ZONE2_ID}',
                'm5.xlarge', 'Ready', 1)""",
        ]

        with conn.cursor() as cursor:
            for stmt in seed_statements:
                try:
                    cursor.execute(stmt)
                    print(f"  [OK] {stmt.strip()[:80]}...")
                except Exception as e:
                    print(f"  [WARN] Seed insert: {e}")

        print("\nDatabase setup complete!")
        print(f"  Region: us-west-2 ({REGION_ID})")
        print(f"  Zone 1 (Green/Solar): us-west-2a ({ZONE1_ID})")
        print(f"  Zone 2 (Coal): us-west-2b ({ZONE2_ID})")
        print(f"  Cluster: test-cluster ({CLUSTER_ID})")
        print(f"  Node 1 (green zone): node-green-1 ({NODE1_ID})")
        print(f"  Node 2 (coal zone): node-coal-1 ({NODE2_ID})")
    finally:
        conn.close()


if __name__ == "__main__":
    setup_database()
