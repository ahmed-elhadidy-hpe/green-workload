#!/usr/bin/env python3
"""
Green Workload AI — Migration Trigger Simulation

Simulates realistic energy and node metric changes that trigger workload
migrations. Runs in waves, each wave inserting new energy readings and
node metrics that progressively create migration-worthy conditions.

Usage:
    python simulate_migration_triggers.py                 # Run all 5 waves
    python simulate_migration_triggers.py --wave 1        # Run only wave 1
    python simulate_migration_triggers.py --wave 1,2,3    # Run waves 1-3
    python simulate_migration_triggers.py --verify        # Just show current state
    python simulate_migration_triggers.py --reset         # Delete simulation data and re-run seed
"""

import pymysql
import os
import sys
import time
import argparse
from datetime import datetime

# ── Configuration ──────────────────────────────────────────────────────

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Load .env
env_path = os.path.join(BASE_DIR, ".env")
if os.path.exists(env_path):
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip())

DB_CONFIG = {
    "host": os.environ.get("DB_HOST", "127.0.0.1"),
    "port": int(os.environ.get("DB_PORT", "3306")),
    "user": os.environ.get("DB_USER", "root"),
    "password": os.environ.get("DB_PASSWORD", ""),
    "db": os.environ.get("DB_NAME", "GREEN_WORKLOAD_DB"),
    "charset": "utf8mb4",
    "autocommit": True,
}

# Zone IDs
ZONES = {
    "us-west-2a":       "a1b2c3d4-0002-0001-0001-000000000001",  # GREEN (Solar)
    "us-west-2b":       "a1b2c3d4-0002-0002-0001-000000000001",  # DIRTY (Coal)
    "eu-west-1a":       "a1b2c3d4-0002-0003-0001-000000000001",  # GREEN (Wind)
    "eu-west-1b":       "a1b2c3d4-0002-0004-0001-000000000001",  # DIRTY (Coal/Gas)
    "ap-southeast-1b":  "a1b2c3d4-0002-0005-0001-000000000001",  # GREEN (Solar)
    "ap-southeast-1a":  "a1b2c3d4-0002-0006-0001-000000000001",  # DIRTY (Gas)
}

# Node IDs
NODES = {
    "node-green-1":     "a1b2c3d4-0004-0001-0001-000000000001",  # US, green
    "node-coal-1":      "a1b2c3d4-0004-0002-0001-000000000001",  # US, dirty
    "node-green-2":     "a1b2c3d4-0004-0003-0001-000000000001",  # US, green
    "node-eu-green-1":  "a1b2c3d4-0004-0004-0001-000000000001",  # EU, green
    "node-eu-dirty-1":  "a1b2c3d4-0004-0005-0001-000000000001",  # EU, dirty
    "node-eu-dirty-2":  "a1b2c3d4-0004-0006-0001-000000000001",  # EU, dirty
    "node-ap-dirty-1":  "a1b2c3d4-0004-0007-0001-000000000001",  # AP, dirty
    "node-ap-green-1":  "a1b2c3d4-0004-0008-0001-000000000001",  # AP, green
}

# ── Helpers ────────────────────────────────────────────────────────────

def get_conn():
    return pymysql.connect(**DB_CONFIG)


def insert_energy(cursor, zone_id, carbon_intensity, renewable_pct, sources):
    cursor.execute(
        """INSERT INTO energy_readings
           (id, zone_id, timestamp, carbon_intensity, renewable_percentage,
            energy_sources, data_source, data_quality)
           VALUES (UUID(), %s, NOW(), %s, %s, %s, 'simulation', 'live')""",
        (zone_id, carbon_intensity, renewable_pct, sources),
    )


def insert_metrics(cursor, node_id, cpu_cores, cpu_pct, mem_gb, mem_pct, pods, net_in, net_out):
    cursor.execute(
        """INSERT INTO node_metrics
           (id, node_id, timestamp, cpu_usage_cores, cpu_usage_percent,
            memory_usage_gb, memory_usage_percent, pod_count,
            network_in_mbps, network_out_mbps)
           VALUES (UUID(), %s, NOW(), %s, %s, %s, %s, %s, %s, %s)""",
        (node_id, cpu_cores, cpu_pct, mem_gb, mem_pct, pods, net_in, net_out),
    )


def banner(msg):
    width = 70
    print(f"\n{'='*width}")
    print(f"  {msg}")
    print(f"{'='*width}")


def show_state(cursor):
    """Print current zone energy and node status."""
    print("\n── Zone Energy Status ──")
    cursor.execute("""
        SELECT z.name, e.carbon_intensity, e.renewable_percentage, e.is_green, e.timestamp
        FROM zones z
        LEFT JOIN energy_readings e ON e.id = (
            SELECT id FROM energy_readings er WHERE er.zone_id = z.id
            ORDER BY er.timestamp DESC LIMIT 1
        )
        ORDER BY e.is_green DESC, e.carbon_intensity ASC
    """)
    print(f"  {'Zone':<20} {'CO2 g/kWh':>10} {'Renew%':>8} {'Green?':>7}  {'Last Update'}")
    print(f"  {'-'*20} {'-'*10} {'-'*8} {'-'*7}  {'-'*20}")
    for row in cursor.fetchall():
        green = "✅ YES" if row[3] else "❌ NO "
        ts = row[4].strftime("%H:%M:%S") if row[4] else "N/A"
        print(f"  {row[0]:<20} {row[1]:>10.1f} {row[2]:>7.1f}% {green}  {ts}")

    print("\n── Node Status ──")
    cursor.execute("""
        SELECT n.name, z.name AS zone, nm.cpu_usage_percent, nm.memory_usage_percent,
               nm.is_overloaded, nm.pod_count, nm.timestamp
        FROM nodes n
        LEFT JOIN zones z ON n.zone_id = z.id
        LEFT JOIN node_metrics nm ON nm.id = (
            SELECT id FROM node_metrics nm2 WHERE nm2.node_id = n.id
            ORDER BY nm2.timestamp DESC LIMIT 1
        )
        ORDER BY z.name, n.name
    """)
    print(f"  {'Node':<18} {'Zone':<20} {'CPU%':>6} {'Mem%':>6} {'Overloaded':>10} {'Pods':>5}")
    print(f"  {'-'*18} {'-'*20} {'-'*6} {'-'*6} {'-'*10} {'-'*5}")
    for row in cursor.fetchall():
        overloaded = "⚠️  YES" if row[4] else "   NO"
        cpu = f"{row[2]:.1f}" if row[2] else "N/A"
        mem = f"{row[3]:.1f}" if row[3] else "N/A"
        pods = str(row[5]) if row[5] else "N/A"
        print(f"  {row[0]:<18} {row[1] or 'N/A':<20} {cpu:>6} {mem:>6} {overloaded:>10} {pods:>5}")

    print("\n── Workloads on Dirty Zones (migration candidates) ──")
    cursor.execute("""
        SELECT w.name, w.namespace, n.name AS node, z.name AS zone,
               e.carbon_intensity, e.renewable_percentage, w.migration_allowed
        FROM workloads w
        JOIN nodes n ON w.current_node_id = n.id
        JOIN zones z ON n.zone_id = z.id
        LEFT JOIN energy_readings e ON e.id = (
            SELECT id FROM energy_readings er WHERE er.zone_id = z.id
            ORDER BY er.timestamp DESC LIMIT 1
        )
        WHERE e.is_green = 0 OR e.is_green IS NULL
        ORDER BY e.carbon_intensity DESC
    """)
    rows = cursor.fetchall()
    if rows:
        print(f"  {'Workload':<22} {'Namespace':<14} {'Node':<18} {'Zone':<20} {'CO2':>6} {'Migr?':>5}")
        print(f"  {'-'*22} {'-'*14} {'-'*18} {'-'*20} {'-'*6} {'-'*5}")
        for r in rows:
            migr = "YES" if r[6] else "NO"
            print(f"  {r[0]:<22} {r[1]:<14} {r[2]:<18} {r[3]:<20} {r[4]:>6.0f} {migr:>5}")
    else:
        print("  (none — all workloads are on green zones)")
    print()


# ── Simulation Waves ───────────────────────────────────────────────────

def wave_1(cursor):
    """
    WAVE 1: Dirty zones get dirtier — coal plants ramp up.
    Green zones stay stable. This widens the carbon gap and should
    make the agent prioritize migrating workloads off dirty nodes.
    """
    banner("WAVE 1: Dirty Zones Spike — Coal Plants at Peak Output")
    print("  → us-west-2b:  CO2 goes 725 → 850, renewable drops to 6%")
    print("  → eu-west-1b:  CO2 goes 555 → 680, renewable drops to 8%")
    print("  → ap-south-1a: CO2 goes 498 → 620, renewable drops to 10%")
    print("  → Green zones: stable (no change)")

    # Dirty zones spike
    insert_energy(cursor, ZONES["us-west-2b"], 850.000, 6.00,
                  '{"coal": 68, "natural_gas": 23, "nuclear": 3, "wind": 3, "solar": 3}')
    insert_energy(cursor, ZONES["eu-west-1b"], 680.000, 8.00,
                  '{"coal": 55, "natural_gas": 33, "nuclear": 4, "wind": 5, "solar": 3}')
    insert_energy(cursor, ZONES["ap-southeast-1a"], 620.000, 10.00,
                  '{"natural_gas": 65, "coal": 22, "nuclear": 3, "solar": 6, "wind": 4}')

    # Green zones hold steady
    insert_energy(cursor, ZONES["us-west-2a"], 80.000, 76.00,
                  '{"solar": 50, "wind": 16, "hydro": 10, "natural_gas": 17, "coal": 7}')
    insert_energy(cursor, ZONES["eu-west-1a"], 55.000, 88.00,
                  '{"wind": 60, "hydro": 18, "solar": 10, "natural_gas": 8, "coal": 4}')
    insert_energy(cursor, ZONES["ap-southeast-1b"], 65.000, 82.00,
                  '{"solar": 67, "wind": 5, "hydro": 10, "natural_gas": 14, "coal": 4}')

    print("  ✅ Energy readings inserted")


def wave_2(cursor):
    """
    WAVE 2: Green nodes report low utilization → plenty of capacity to
    receive workloads. Dirty nodes report rising load → nearing overload.
    """
    banner("WAVE 2: Green Nodes Idle, Dirty Nodes Under Pressure")
    print("  → Green nodes: CPU 15-30%, Memory 20-35% — lots of headroom")
    print("  → Dirty nodes: CPU 72-78%, Memory 70-78% — near threshold")

    # Green nodes: very low utilization (ready to receive migrations)
    insert_metrics(cursor, NODES["node-green-1"],    1.000, 25.00,  4.800, 30.00, 10, 85.2,  55.1)
    insert_metrics(cursor, NODES["node-green-2"],    1.200, 15.00,  6.400, 20.00,  6, 45.3,  28.1)
    insert_metrics(cursor, NODES["node-eu-green-1"], 1.200, 30.00,  5.600, 35.00, 14, 98.5,  65.2)
    insert_metrics(cursor, NODES["node-ap-green-1"], 1.600, 20.00,  7.200, 22.50,  8, 72.1,  48.5)

    # Dirty nodes: rising toward overload threshold (80%)
    insert_metrics(cursor, NODES["node-coal-1"],     3.100, 77.50, 12.480, 78.00, 35, 310.5, 235.2)
    insert_metrics(cursor, NODES["node-eu-dirty-1"], 2.900, 72.50, 11.200, 70.00, 28, 255.1, 195.8)
    insert_metrics(cursor, NODES["node-eu-dirty-2"], 5.600, 70.00, 22.400, 70.00, 22, 380.2, 295.1)
    insert_metrics(cursor, NODES["node-ap-dirty-1"], 3.000, 75.00, 12.000, 75.00, 30, 305.8, 230.1)

    print("  ✅ Node metrics inserted")


def wave_3(cursor):
    """
    WAVE 3: Carbon intensity gap widens further. Dirty zones hit extreme
    levels. This creates a >20% carbon difference (safety check threshold).
    """
    banner("WAVE 3: Extreme Carbon Divergence")
    print("  → Dirty zones: CO2 skyrockets (900-1000+ gCO2/kWh)")
    print("  → Green zones: CO2 drops further (40-55 gCO2/kWh)")
    print("  → Carbon gap: >900 gCO2/kWh difference → triggers migration")

    # Dirty zones at extreme levels
    insert_energy(cursor, ZONES["us-west-2b"], 980.000, 4.00,
                  '{"coal": 72, "natural_gas": 22, "nuclear": 2, "wind": 2, "solar": 2}')
    insert_energy(cursor, ZONES["eu-west-1b"], 820.000, 5.00,
                  '{"coal": 62, "natural_gas": 30, "nuclear": 3, "wind": 3, "solar": 2}')
    insert_energy(cursor, ZONES["ap-southeast-1a"], 750.000, 7.00,
                  '{"natural_gas": 70, "coal": 20, "nuclear": 3, "solar": 4, "wind": 3}')

    # Green zones at peak renewables
    insert_energy(cursor, ZONES["us-west-2a"], 45.000, 92.00,
                  '{"solar": 62, "wind": 20, "hydro": 10, "natural_gas": 5, "coal": 3}')
    insert_energy(cursor, ZONES["eu-west-1a"], 38.000, 95.00,
                  '{"wind": 68, "hydro": 20, "solar": 7, "natural_gas": 3, "coal": 2}')
    insert_energy(cursor, ZONES["ap-southeast-1b"], 42.000, 93.00,
                  '{"solar": 78, "wind": 5, "hydro": 10, "natural_gas": 5, "coal": 2}')

    print("  ✅ Energy readings inserted — extreme carbon gap established")


def wave_4(cursor):
    """
    WAVE 4: Dirty nodes breach the overload threshold (>80% CPU or Memory).
    Combined with dirty energy, this creates an urgent migration scenario.
    """
    banner("WAVE 4: Dirty Nodes Overloaded — Critical Migration Trigger")
    print("  → Dirty nodes: CPU 82-92%, Memory 83-90% — OVERLOADED")
    print("  → Green nodes: CPU 18-32%, Memory 20-35% — ample capacity")
    print("  → Combined with extreme carbon gap → urgent migrations needed")

    # Dirty nodes: OVERLOADED (cpu > 80% or memory > 80%)
    insert_metrics(cursor, NODES["node-coal-1"],     3.500, 87.50, 14.080, 88.00, 42, 380.5, 290.2)
    insert_metrics(cursor, NODES["node-eu-dirty-1"], 3.300, 82.50, 13.280, 83.00, 35, 320.1, 255.8)
    insert_metrics(cursor, NODES["node-eu-dirty-2"], 7.200, 90.00, 28.800, 90.00, 30, 450.2, 365.1)
    insert_metrics(cursor, NODES["node-ap-dirty-1"], 3.700, 92.50, 14.400, 90.00, 38, 395.8, 310.1)

    # Green nodes: still very healthy
    insert_metrics(cursor, NODES["node-green-1"],    1.100, 27.50,  5.120, 32.00, 11, 90.2,  58.1)
    insert_metrics(cursor, NODES["node-green-2"],    1.440, 18.00,  7.040, 22.00,  7, 52.3,  32.1)
    insert_metrics(cursor, NODES["node-eu-green-1"], 1.280, 32.00,  5.440, 34.00, 15, 105.5, 72.2)
    insert_metrics(cursor, NODES["node-ap-green-1"], 1.920, 24.00,  8.320, 26.00,  9, 80.1,  52.5)

    print("  ✅ Node metrics inserted — dirty nodes are now OVERLOADED")


def wave_5(cursor):
    """
    WAVE 5: Simulate post-migration scenario. Some workloads have been
    moved to green nodes (update current_node_id). Dirty zone energy
    improves slightly (evening out). This shows the system at rest.
    """
    banner("WAVE 5: Post-Migration — Workloads Relocated to Green Nodes")
    print("  → Moving 4 high-priority workloads to green nodes")
    print("  → Dirty zones stabilizing (CO2 dropping)")
    print("  → Green nodes absorbing load (CPU rising to 45-55%)")

    # Simulate workload migrations (move workloads from dirty → green nodes)
    migrations = [
        # (workload_name, namespace, cluster_id, from_node, to_node, from_zone, to_zone)
        ("api-gateway",      "production", "a1b2c3d4-0003-0001-0001-000000000001",
         NODES["node-coal-1"],     NODES["node-green-2"],
         ZONES["us-west-2b"],      ZONES["us-west-2a"],      980.0, 45.0),
        ("payment-processor","production", "a1b2c3d4-0003-0001-0001-000000000001",
         NODES["node-coal-1"],     NODES["node-green-1"],
         ZONES["us-west-2b"],      ZONES["us-west-2a"],      980.0, 45.0),
        ("web-frontend",     "production", "a1b2c3d4-0003-0002-0001-000000000001",
         NODES["node-eu-dirty-1"], NODES["node-eu-green-1"],
         ZONES["eu-west-1b"],      ZONES["eu-west-1a"],      820.0, 38.0),
        ("image-processor",  "media",      "a1b2c3d4-0003-0003-0001-000000000001",
         NODES["node-ap-dirty-1"], NODES["node-ap-green-1"],
         ZONES["ap-southeast-1a"], ZONES["ap-southeast-1b"], 750.0, 42.0),
    ]

    workload_ids = {
        "api-gateway":       "a1b2c3d4-0005-0001-0001-000000000001",
        "payment-processor": "a1b2c3d4-0005-0003-0001-000000000001",
        "web-frontend":      "a1b2c3d4-0005-0007-0001-000000000001",
        "image-processor":   "a1b2c3d4-0005-0012-0001-000000000001",
    }

    for wname, ns, cid, src_node, dst_node, src_zone, dst_zone, src_co2, dst_co2 in migrations:
        wid = workload_ids[wname]

        # Update workload to new node
        cursor.execute(
            "UPDATE workloads SET current_node_id = %s, updated_at = NOW() WHERE id = %s",
            (dst_node, wid)
        )

        # Record migration event
        cursor.execute(
            """INSERT INTO migration_events
               (id, workload_id, workload_name, namespace, cluster_id,
                source_node_id, destination_node_id, source_zone_id, destination_zone_id,
                migration_type, status, trigger_reason,
                source_carbon_intensity, destination_carbon_intensity,
                carbon_savings_estimate, started_at, completed_at, duration_seconds)
               VALUES (UUID(), %s, %s, %s, %s, %s, %s, %s, %s,
                       'affinity', 'completed',
                       'Workload on high-carbon zone; green node available with capacity',
                       %s, %s, %s, DATE_SUB(NOW(), INTERVAL 2 MINUTE), NOW(), 120)""",
            (wid, wname, ns, cid, src_node, dst_node, src_zone, dst_zone,
             src_co2, dst_co2, src_co2 - dst_co2)
        )
        print(f"  ✅ {wname} → migrated from dirty to green node (saved {src_co2 - dst_co2:.0f} gCO2/kWh)")

    # Green nodes absorb the new workloads (load increases)
    insert_metrics(cursor, NODES["node-green-1"],    2.200, 55.00, 9.600,  60.00, 22, 180.2, 125.1)
    insert_metrics(cursor, NODES["node-green-2"],    3.200, 40.00, 14.400, 45.00, 18, 145.3, 98.1)
    insert_metrics(cursor, NODES["node-eu-green-1"], 2.400, 60.00, 9.280,  58.00, 26, 195.5, 138.2)
    insert_metrics(cursor, NODES["node-ap-green-1"], 3.600, 45.00, 14.400, 45.00, 18, 165.1, 110.5)

    # Dirty nodes: load drops after workload departure
    insert_metrics(cursor, NODES["node-coal-1"],     1.800, 45.00,  7.200, 45.00, 20, 180.5, 130.2)
    insert_metrics(cursor, NODES["node-eu-dirty-1"], 1.600, 40.00,  6.400, 40.00, 16, 155.1, 115.8)
    insert_metrics(cursor, NODES["node-eu-dirty-2"], 5.600, 70.00, 22.400, 70.00, 22, 380.2, 295.1)
    insert_metrics(cursor, NODES["node-ap-dirty-1"], 1.200, 30.00,  4.800, 30.00, 14, 125.8, 90.1)

    # Dirty zones stabilize somewhat
    insert_energy(cursor, ZONES["us-west-2b"], 650.000, 15.00,
                  '{"coal": 50, "natural_gas": 30, "nuclear": 5, "wind": 8, "solar": 7}')
    insert_energy(cursor, ZONES["eu-west-1b"], 580.000, 18.00,
                  '{"coal": 45, "natural_gas": 32, "nuclear": 5, "wind": 10, "solar": 8}')
    insert_energy(cursor, ZONES["ap-southeast-1a"], 520.000, 20.00,
                  '{"natural_gas": 55, "coal": 20, "nuclear": 5, "solar": 12, "wind": 8}')

    print("  ✅ Post-migration state recorded")


def reset_simulation(cursor):
    """Remove all simulation-generated data and re-run seed_data.sql."""
    banner("RESET — Clearing Simulation Data")

    # Delete in dependency order
    for table in ["workload_movement_log", "migration_events", "ai_decisions", "agent_runs",
                  "node_metrics", "energy_readings", "workloads",
                  "nodes", "clusters", "zones", "regions"]:
        cursor.execute(f"DELETE FROM {table}")
        print(f"  Cleared: {table}")

    # Re-run seed_data.sql
    seed_path = os.path.join(BASE_DIR, "seed_data.sql")
    if os.path.exists(seed_path):
        print(f"\n  Re-running {seed_path}...")
        with open(seed_path, "r") as f:
            sql_content = f.read()

        statements = [s.strip() for s in sql_content.split(";") if s.strip()]
        for stmt in statements:
            if not stmt or stmt.startswith("--"):
                continue
            try:
                cursor.execute(stmt)
            except Exception as e:
                # Skip comment-only or empty blocks
                if "syntax" not in str(e).lower():
                    pass
        print("  ✅ Seed data re-loaded")
    else:
        print(f"  ⚠️  {seed_path} not found — skipping seed reload")


# ── Main ───────────────────────────────────────────────────────────────

WAVES = {
    1: ("Dirty zones spike — coal plants ramp up", wave_1),
    2: ("Green nodes idle, dirty nodes pressured", wave_2),
    3: ("Extreme carbon divergence", wave_3),
    4: ("Dirty nodes overloaded — critical trigger", wave_4),
    5: ("Post-migration — workloads relocated", wave_5),
}


def main():
    parser = argparse.ArgumentParser(
        description="Simulate energy/metric changes to trigger green workload migrations"
    )
    parser.add_argument("--wave", type=str, default=None,
                        help="Comma-separated wave numbers to run (e.g. '1,2,3'). Default: all")
    parser.add_argument("--verify", action="store_true",
                        help="Just show current DB state without making changes")
    parser.add_argument("--reset", action="store_true",
                        help="Clear all data and re-run seed_data.sql")
    parser.add_argument("--pause", type=int, default=0,
                        help="Seconds to pause between waves (for live demo)")
    args = parser.parse_args()

    conn = get_conn()
    cursor = conn.cursor()

    try:
        if args.verify:
            banner("Current Database State")
            show_state(cursor)
            return

        if args.reset:
            reset_simulation(cursor)
            show_state(cursor)
            return

        # Determine which waves to run
        if args.wave:
            wave_nums = [int(w.strip()) for w in args.wave.split(",")]
        else:
            wave_nums = list(WAVES.keys())

        banner("Green Workload AI — Migration Trigger Simulation")
        print(f"  Running waves: {wave_nums}")
        print(f"  Database: {DB_CONFIG['db']} @ {DB_CONFIG['host']}:{DB_CONFIG['port']}")
        print(f"  Pause between waves: {args.pause}s")

        for i, wnum in enumerate(wave_nums):
            if wnum not in WAVES:
                print(f"\n  ⚠️  Wave {wnum} does not exist (valid: 1-5)")
                continue

            desc, func = WAVES[wnum]
            func(cursor)
            show_state(cursor)

            if args.pause > 0 and i < len(wave_nums) - 1:
                print(f"\n  ⏳ Pausing {args.pause}s before next wave...")
                time.sleep(args.pause)

        banner("Simulation Complete")
        print("  Run 'python main.py --once' to trigger the migration agent")
        print("  Run 'python simulate_migration_triggers.py --verify' to check state")
        print("  Run 'python simulate_migration_triggers.py --reset' to start over\n")

    finally:
        cursor.close()
        conn.close()


if __name__ == "__main__":
    main()
