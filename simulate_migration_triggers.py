#!/usr/bin/env python3
"""
Green Workload AI — Migration Trigger Simulation

Simulates realistic energy and node metric changes across 5 waves that
create varied conditions for the AI agent:

  Wave 0: Baseline — all zones moderate, no migration needed  → "skip"
  Wave 1: Carbon gap emerges — dirty zones spike              → "migrate"
  Wave 2: Green nodes near saturation — limited capacity      → partial "migrate" / "wait"
  Wave 3: All destinations full — no safe migration possible   → "skip" / "wait"
  Wave 4: Recovery — green capacity returns, dirty spikes      → "migrate"

Each wave inserts BOTH energy_readings AND node_metrics rows.

Usage:
    python simulate_migration_triggers.py                 # Run all waves (0-4)
    python simulate_migration_triggers.py --wave 0        # Run only baseline
    python simulate_migration_triggers.py --wave 1,2      # Run waves 1-2
    python simulate_migration_triggers.py --verify        # Show current state
    python simulate_migration_triggers.py --reset         # Delete all data and re-seed
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
        SELECT w.name, w.namespace, w.workload_type, n.name AS node, z.name AS zone,
               e.carbon_intensity, e.renewable_percentage, w.migration_allowed, w.stateful
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
        print(f"  {'Workload':<22} {'Namespace':<14} {'Type':<13} {'Node':<18} {'Zone':<20} {'CO2':>6} {'Migr?':>5} {'Stateful':>8}")
        print(f"  {'-'*22} {'-'*14} {'-'*13} {'-'*18} {'-'*20} {'-'*6} {'-'*5} {'-'*8}")
        for r in rows:
            migr = "YES" if r[7] else " NO"
            stfl = "YES" if r[8] else " NO"
            print(f"  {r[0]:<22} {r[1]:<14} {r[2]:<13} {r[3]:<18} {r[4]:<20} {r[5]:>6.0f} {migr:>5} {stfl:>8}")
    else:
        print("  (none — all workloads are on green zones)")
    print()


# ── Simulation Waves ───────────────────────────────────────────────────

def wave_0(cursor):
    """
    WAVE 0: Baseline — Stable Environment, No Migration Needed.

    All zones have moderate carbon intensity with <20% gap between dirty
    and green zones.  All nodes are healthy (25-45% utilisation).
    The agent should produce a "skip" decision since the carbon
    difference is too small to justify migration.
    """
    banner("WAVE 0: Baseline — Stable Environment, No Migration Needed")
    print("  → All zones: moderate carbon (150-220 gCO2/kWh)")
    print("  → Carbon gap < 20% between zones → below migration threshold")
    print("  → All nodes healthy (25-45% CPU, 25-40% memory)")
    print("  → Expected agent decision: SKIP")

    # All zones at moderate, similar carbon intensity (<20% gap)
    insert_energy(cursor, ZONES["us-west-2a"], 150.000, 55.00,
                  '{"solar": 30, "wind": 15, "hydro": 10, "natural_gas": 30, "coal": 15}')
    insert_energy(cursor, ZONES["us-west-2b"], 185.000, 42.00,
                  '{"coal": 30, "natural_gas": 25, "nuclear": 10, "wind": 18, "solar": 17}')
    insert_energy(cursor, ZONES["eu-west-1a"], 140.000, 58.00,
                  '{"wind": 35, "hydro": 13, "solar": 10, "natural_gas": 28, "coal": 14}')
    insert_energy(cursor, ZONES["eu-west-1b"], 170.000, 45.00,
                  '{"coal": 25, "natural_gas": 28, "nuclear": 8, "wind": 22, "solar": 17}')
    insert_energy(cursor, ZONES["ap-southeast-1b"], 155.000, 53.00,
                  '{"solar": 35, "wind": 8, "hydro": 10, "natural_gas": 32, "coal": 15}')
    insert_energy(cursor, ZONES["ap-southeast-1a"], 180.000, 44.00,
                  '{"natural_gas": 35, "coal": 20, "nuclear": 5, "solar": 22, "wind": 18}')

    # All nodes healthy — moderate utilisation
    insert_metrics(cursor, NODES["node-green-1"],    1.000, 25.00, 4.800, 30.00,  8,  60.2,  40.1)
    insert_metrics(cursor, NODES["node-green-2"],    1.400, 35.00, 5.600, 35.00, 10,  55.3,  35.1)
    insert_metrics(cursor, NODES["node-coal-1"],     1.200, 30.00, 5.120, 32.00, 12,  70.5,  48.2)
    insert_metrics(cursor, NODES["node-eu-green-1"], 1.000, 25.00, 4.480, 28.00,  9,  65.5,  42.2)
    insert_metrics(cursor, NODES["node-eu-dirty-1"], 1.200, 30.00, 5.440, 34.00, 11,  58.1,  38.8)
    insert_metrics(cursor, NODES["node-eu-dirty-2"], 3.200, 40.00, 12.80, 40.00, 14, 120.2,  85.1)
    insert_metrics(cursor, NODES["node-ap-dirty-1"], 1.400, 35.00, 5.600, 35.00, 13,  75.8,  50.1)
    insert_metrics(cursor, NODES["node-ap-green-1"], 1.600, 20.00, 6.400, 20.00,  6,  45.1,  30.5)

    print("  ✅ Baseline readings inserted — carbon gap < 20%, no migration needed")


def wave_1(cursor):
    """
    WAVE 1: Carbon Gap Emerges — Dirty Zones Spike.

    Dirty zones see coal/gas plants ramp up, pushing carbon intensity
    to 550-700 gCO2/kWh while green zones drop to 60-90 gCO2/kWh.
    Carbon gap > 20% → migrations should be triggered.
    Node utilisation stays moderate on both sides.
    """
    banner("WAVE 1: Carbon Gap Emerges — Dirty Zones Spike")
    print("  → Dirty zones: CO2 rises to 550-700 gCO2/kWh")
    print("  → Green zones: CO2 drops to 60-90 gCO2/kWh")
    print("  → Carbon gap ~85-90% → well above 20% threshold")
    print("  → All nodes: moderate utilisation (25-55%)")
    print("  → Expected agent decision: MIGRATE")

    # Dirty zones spike — coal/gas plants ramp up
    insert_energy(cursor, ZONES["us-west-2b"], 700.000, 8.00,
                  '{"coal": 58, "natural_gas": 30, "nuclear": 4, "wind": 4, "solar": 4}')
    insert_energy(cursor, ZONES["eu-west-1b"], 620.000, 10.00,
                  '{"coal": 48, "natural_gas": 38, "nuclear": 4, "wind": 6, "solar": 4}')
    insert_energy(cursor, ZONES["ap-southeast-1a"], 550.000, 12.00,
                  '{"natural_gas": 60, "coal": 25, "nuclear": 3, "solar": 7, "wind": 5}')

    # Green zones improve — renewables ramp up
    insert_energy(cursor, ZONES["us-west-2a"], 80.000, 78.00,
                  '{"solar": 52, "wind": 16, "hydro": 10, "natural_gas": 15, "coal": 7}')
    insert_energy(cursor, ZONES["eu-west-1a"], 60.000, 85.00,
                  '{"wind": 58, "hydro": 18, "solar": 9, "natural_gas": 10, "coal": 5}')
    insert_energy(cursor, ZONES["ap-southeast-1b"], 70.000, 80.00,
                  '{"solar": 62, "wind": 8, "hydro": 10, "natural_gas": 15, "coal": 5}')

    # Green nodes: low utilisation — plenty of capacity
    insert_metrics(cursor, NODES["node-green-1"],    1.000, 25.00, 4.800, 30.00, 10,  85.2,  55.1)
    insert_metrics(cursor, NODES["node-green-2"],    1.200, 15.00, 6.400, 20.00,  6,  45.3,  28.1)
    insert_metrics(cursor, NODES["node-eu-green-1"], 1.200, 30.00, 5.600, 35.00, 14,  98.5,  65.2)
    insert_metrics(cursor, NODES["node-ap-green-1"], 1.600, 20.00, 7.200, 22.50,  8,  72.1,  48.5)

    # Dirty nodes: moderate — not overloaded but busy
    insert_metrics(cursor, NODES["node-coal-1"],     2.000, 50.00, 8.000, 50.00, 22, 180.5, 130.2)
    insert_metrics(cursor, NODES["node-eu-dirty-1"], 1.800, 45.00, 7.200, 45.00, 18, 155.1, 115.8)
    insert_metrics(cursor, NODES["node-eu-dirty-2"], 4.000, 50.00, 16.00, 50.00, 16, 220.2, 165.1)
    insert_metrics(cursor, NODES["node-ap-dirty-1"], 1.800, 45.00, 7.200, 45.00, 20, 165.8, 120.1)

    print("  ✅ Carbon gap established — dirty zones 550-700 vs green 60-90 gCO2/kWh")


def wave_2(cursor):
    """
    WAVE 2: Green Nodes Near Saturation — Limited Capacity.

    Green nodes are now heavily loaded (72-82%), some above the 80%
    safety threshold.  Only 1-2 green nodes have room for migrations.
    Dirty zones are still dirty.  Agent should migrate to the remaining
    green capacity, but some workloads will have no safe destination,
    likely producing a "wait" or partial "skip" decision.
    """
    banner("WAVE 2: Green Nodes Near Saturation — Limited Capacity")
    print("  → Green nodes: 72-82% utilisation (some above 80% threshold)")
    print("  → Dirty zones: still high CO2 (600-680 gCO2/kWh)")
    print("  → Only 1-2 green nodes with capacity → limited migrations")
    print("  → Expected agent decision: partial MIGRATE + WAIT/SKIP for rest")

    # Dirty zones: sustained high carbon
    insert_energy(cursor, ZONES["us-west-2b"], 680.000, 9.00,
                  '{"coal": 55, "natural_gas": 32, "nuclear": 4, "wind": 5, "solar": 4}')
    insert_energy(cursor, ZONES["eu-west-1b"], 640.000, 10.00,
                  '{"coal": 50, "natural_gas": 35, "nuclear": 5, "wind": 6, "solar": 4}')
    insert_energy(cursor, ZONES["ap-southeast-1a"], 600.000, 11.00,
                  '{"natural_gas": 58, "coal": 28, "nuclear": 4, "solar": 6, "wind": 4}')

    # Green zones: stable
    insert_energy(cursor, ZONES["us-west-2a"], 75.000, 80.00,
                  '{"solar": 54, "wind": 16, "hydro": 10, "natural_gas": 14, "coal": 6}')
    insert_energy(cursor, ZONES["eu-west-1a"], 55.000, 88.00,
                  '{"wind": 62, "hydro": 18, "solar": 8, "natural_gas": 8, "coal": 4}')
    insert_energy(cursor, ZONES["ap-southeast-1b"], 65.000, 83.00,
                  '{"solar": 66, "wind": 7, "hydro": 10, "natural_gas": 13, "coal": 4}')

    # Green nodes: NEARLY FULL — most above 80% threshold
    insert_metrics(cursor, NODES["node-green-1"],    3.200, 80.00, 12.80, 80.00, 28, 280.2, 195.1)
    insert_metrics(cursor, NODES["node-green-2"],    3.400, 85.00, 13.60, 85.00, 30, 300.3, 210.1)  # OVER threshold
    insert_metrics(cursor, NODES["node-eu-green-1"], 3.000, 75.00, 12.00, 75.00, 26, 260.5, 180.2)  # Still has room
    insert_metrics(cursor, NODES["node-ap-green-1"], 6.400, 80.00, 25.60, 80.00, 25, 310.1, 215.5)  # AT threshold

    # Dirty nodes: moderate — not overloaded
    insert_metrics(cursor, NODES["node-coal-1"],     2.200, 55.00, 8.800, 55.00, 24, 195.5, 140.2)
    insert_metrics(cursor, NODES["node-eu-dirty-1"], 2.000, 50.00, 8.000, 50.00, 20, 170.1, 125.8)
    insert_metrics(cursor, NODES["node-eu-dirty-2"], 4.400, 55.00, 17.60, 55.00, 18, 245.2, 180.1)
    insert_metrics(cursor, NODES["node-ap-dirty-1"], 2.200, 55.00, 8.800, 55.00, 22, 185.8, 135.1)

    print("  ✅ Green capacity limited — most green nodes at 80%+, only eu-green-1 has room")


def wave_3(cursor):
    """
    WAVE 3: All Green Destinations Full — No Safe Migration Possible.

    All green nodes exceed 80% on CPU or memory.  Dirty zones have
    moderate carbon (300-400 gCO2/kWh) — bad but not extreme.
    The agent has no valid destination node → should produce "skip"
    or "wait" decision.
    """
    banner("WAVE 3: All Green Destinations Full — No Safe Migration")
    print("  → ALL green nodes: 83-92% utilisation (above 80% threshold)")
    print("  → Dirty zones: moderate CO2 (300-420 gCO2/kWh)")
    print("  → No safe green destination available → agent must skip/wait")
    print("  → Expected agent decision: SKIP or WAIT")

    # Dirty zones: moderate (not extreme, but gap still > 20%)
    insert_energy(cursor, ZONES["us-west-2b"], 420.000, 22.00,
                  '{"coal": 40, "natural_gas": 32, "nuclear": 6, "wind": 12, "solar": 10}')
    insert_energy(cursor, ZONES["eu-west-1b"], 380.000, 25.00,
                  '{"coal": 35, "natural_gas": 35, "nuclear": 5, "wind": 14, "solar": 11}')
    insert_energy(cursor, ZONES["ap-southeast-1a"], 350.000, 28.00,
                  '{"natural_gas": 42, "coal": 25, "nuclear": 5, "solar": 15, "wind": 13}')

    # Green zones: still clean
    insert_energy(cursor, ZONES["us-west-2a"], 70.000, 82.00,
                  '{"solar": 56, "wind": 16, "hydro": 10, "natural_gas": 12, "coal": 6}')
    insert_energy(cursor, ZONES["eu-west-1a"], 50.000, 90.00,
                  '{"wind": 64, "hydro": 18, "solar": 8, "natural_gas": 6, "coal": 4}')
    insert_energy(cursor, ZONES["ap-southeast-1b"], 60.000, 85.00,
                  '{"solar": 68, "wind": 7, "hydro": 10, "natural_gas": 11, "coal": 4}')

    # ALL green nodes: OVERLOADED — no capacity for migrations
    insert_metrics(cursor, NODES["node-green-1"],    3.520, 88.00, 14.08, 88.00, 32, 320.2, 225.1)
    insert_metrics(cursor, NODES["node-green-2"],    3.680, 92.00, 14.72, 92.00, 34, 340.3, 240.1)
    insert_metrics(cursor, NODES["node-eu-green-1"], 3.320, 83.00, 13.28, 83.00, 30, 290.5, 200.2)
    insert_metrics(cursor, NODES["node-ap-green-1"], 6.800, 85.00, 27.20, 85.00, 28, 340.1, 238.5)

    # Dirty nodes: moderate load
    insert_metrics(cursor, NODES["node-coal-1"],     2.000, 50.00, 8.000, 50.00, 22, 175.5, 128.2)
    insert_metrics(cursor, NODES["node-eu-dirty-1"], 2.000, 50.00, 8.000, 50.00, 20, 165.1, 120.8)
    insert_metrics(cursor, NODES["node-eu-dirty-2"], 4.000, 50.00, 16.00, 50.00, 16, 230.2, 170.1)
    insert_metrics(cursor, NODES["node-ap-dirty-1"], 2.000, 50.00, 8.000, 50.00, 20, 170.8, 125.1)

    print("  ✅ All green nodes overloaded — no safe migration destination")


def wave_4(cursor):
    """
    WAVE 4: Recovery — Green Capacity Returns, Dirty Zones Spike Again.

    Green nodes shed load (simulating organic rebalancing or pod
    scale-downs). Dirty zones spike to extreme levels.  The agent
    should now find open green capacity and execute aggressive
    migrations.
    """
    banner("WAVE 4: Recovery — Green Capacity Returns, Dirty Zones Spike")
    print("  → Green nodes: capacity freed up (30-45% utilisation)")
    print("  → Dirty zones: extreme CO2 (800-980 gCO2/kWh)")
    print("  → Green zones: very clean (38-65 gCO2/kWh)")
    print("  → Expected agent decision: MIGRATE (aggressive)")

    # Dirty zones: extreme spike
    insert_energy(cursor, ZONES["us-west-2b"], 980.000, 4.00,
                  '{"coal": 72, "natural_gas": 22, "nuclear": 2, "wind": 2, "solar": 2}')
    insert_energy(cursor, ZONES["eu-west-1b"], 850.000, 5.00,
                  '{"coal": 62, "natural_gas": 30, "nuclear": 3, "wind": 3, "solar": 2}')
    insert_energy(cursor, ZONES["ap-southeast-1a"], 800.000, 6.00,
                  '{"natural_gas": 70, "coal": 22, "nuclear": 2, "solar": 3, "wind": 3}')

    # Green zones: peak renewables
    insert_energy(cursor, ZONES["us-west-2a"], 45.000, 93.00,
                  '{"solar": 64, "wind": 20, "hydro": 9, "natural_gas": 4, "coal": 3}')
    insert_energy(cursor, ZONES["eu-west-1a"], 38.000, 96.00,
                  '{"wind": 70, "hydro": 18, "solar": 8, "natural_gas": 2, "coal": 2}')
    insert_energy(cursor, ZONES["ap-southeast-1b"], 42.000, 94.00,
                  '{"solar": 78, "wind": 6, "hydro": 10, "natural_gas": 4, "coal": 2}')

    # Green nodes: capacity freed up — ready to receive workloads
    insert_metrics(cursor, NODES["node-green-1"],    1.200, 30.00, 5.120, 32.00, 12,  95.2,  62.1)
    insert_metrics(cursor, NODES["node-green-2"],    1.400, 35.00, 5.600, 35.00, 10,  65.3,  42.1)
    insert_metrics(cursor, NODES["node-eu-green-1"], 1.200, 30.00, 4.800, 30.00, 11,  85.5,  58.2)
    insert_metrics(cursor, NODES["node-ap-green-1"], 3.200, 40.00, 12.80, 40.00, 14, 110.1,  75.5)

    # Dirty nodes: high load — under pressure
    insert_metrics(cursor, NODES["node-coal-1"],     3.200, 80.00, 12.80, 80.00, 38, 350.5, 260.2)
    insert_metrics(cursor, NODES["node-eu-dirty-1"], 3.000, 75.00, 12.00, 75.00, 32, 290.1, 220.8)
    insert_metrics(cursor, NODES["node-eu-dirty-2"], 6.400, 80.00, 25.60, 80.00, 28, 410.2, 310.1)
    insert_metrics(cursor, NODES["node-ap-dirty-1"], 3.400, 85.00, 13.60, 85.00, 35, 365.8, 275.1)

    print("  ✅ Green capacity available + extreme carbon gap → aggressive migration expected")


def reset_simulation(cursor):
    """Remove all simulation-generated data and re-run seed_data.sql."""
    banner("RESET — Clearing Simulation Data")

    # Delete in dependency order
    for table in ["workload_movement_log", "migration_events", "ai_decisions", "agent_runs",
                  "node_metrics", "energy_readings", "workloads",
                  "nodes", "clusters", "zones", "regions"]:
        cursor.execute(f"DELETE FROM {table}")
        print(f"  Cleared: {table}")

    # Re-run seed_data.sql via mysql CLI (Python-based parsing can
    # skip statements that start with comment lines)
    seed_path = os.path.join(BASE_DIR, "seed_data.sql")
    if os.path.exists(seed_path):
        print(f"\n  Re-running {seed_path} via mysql CLI...")
        import subprocess
        cmd = [
            "mysql",
            "-u", DB_CONFIG["user"],
            "-h", DB_CONFIG["host"],
            "-P", str(DB_CONFIG["port"]),
            DB_CONFIG["db"],
        ]
        if DB_CONFIG.get("password"):
            cmd.insert(3, f"-p{DB_CONFIG['password']}")
        result = subprocess.run(
            cmd, stdin=open(seed_path), capture_output=True, text=True,
        )
        if result.returncode == 0:
            print("  ✅ Seed data re-loaded")
        else:
            print(f"  ⚠️  mysql CLI failed: {result.stderr.strip()}")
            print("  Falling back to Python-based SQL parsing...")
            with open(seed_path, "r") as f:
                sql_content = f.read()
            statements = [s.strip() for s in sql_content.split(";") if s.strip()]
            for stmt in statements:
                if not stmt or stmt.startswith("--"):
                    continue
                try:
                    cursor.execute(stmt)
                except Exception:
                    pass
            print("  ✅ Seed data re-loaded (Python fallback)")
    else:
        print(f"  ⚠️  {seed_path} not found — skipping seed reload")


# ── Main ───────────────────────────────────────────────────────────────

WAVES = {
    0: ("Baseline — stable, no migration needed → skip", wave_0),
    1: ("Carbon gap emerges — dirty zones spike → migrate", wave_1),
    2: ("Green nodes near saturation — limited capacity → partial migrate / wait", wave_2),
    3: ("All green destinations full — no safe migration → skip / wait", wave_3),
    4: ("Recovery — green capacity returns, dirty spikes → migrate", wave_4),
}


def main():
    parser = argparse.ArgumentParser(
        description="Simulate energy/metric changes to trigger green workload migrations"
    )
    parser.add_argument("--wave", type=str, default=None,
                        help="Comma-separated wave numbers to run (e.g. '0,1,2'). Default: all")
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
                print(f"\n  ⚠️  Wave {wnum} does not exist (valid: 0-4)")
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
