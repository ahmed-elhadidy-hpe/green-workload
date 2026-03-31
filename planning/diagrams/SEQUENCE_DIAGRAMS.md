# Sequence Diagrams — Green Workload AI

All diagrams use [Mermaid](https://mermaid.js.org/) syntax.

---

## Diagram 1 — Main Agent Evaluation Cycle

The core autonomous loop that runs every ~10 minutes.

```mermaid
sequenceDiagram
    autonumber
    participant Scheduler as Scheduler<br/>(K8s CronJob)
    participant Agent as AI Agent<br/>(LLM Engine)
    participant DBMCP as Internal DB MCP
    participant GreenMCP as Green Energy MCP
    participant K8sMCP as Kubernetes MCP
    participant EnergyAPI as Electricity Maps<br/>/ WattTime API
    participant K8s as K8s Clusters

    Scheduler->>Agent: trigger_evaluation_cycle()

    Agent->>DBMCP: create_agent_run()
    DBMCP-->>Agent: {agent_run_id}

    par Fetch energy state
        Agent->>GreenMCP: get_all_zones_energy_status()
        GreenMCP->>EnergyAPI: Query carbon intensity (all zones)
        EnergyAPI-->>GreenMCP: {zone: carbon_intensity, renewable_pct, ...}
        GreenMCP-->>Agent: [{zone_id, is_green, carbon_intensity, renewable_pct}]
    and Fetch node state
        Agent->>K8sMCP: get_all_nodes_with_metrics()
        K8sMCP->>K8s: kubectl top nodes + node status (all clusters)
        K8s-->>K8sMCP: node metrics + conditions
        K8sMCP-->>Agent: [{node, cpu_pct, memory_pct, pod_count, status}]
    and Fetch topology
        Agent->>DBMCP: get_node_zone_mapping()
        DBMCP-->>Agent: [{node_id, zone_id, cluster_id, migration_allowed}]
    end

    Agent->>Agent: Build context snapshot<br/>(zone energy + node load + topology)

    Note over Agent: LLM Reasoning:<br/>• Which zones are green right now?<br/>• Which nodes are on non-green zones?<br/>• Which of those nodes have migratable workloads?<br/>• Are there green-zone nodes with capacity?<br/>• Apply safety rules (PDB, StatefulSet, load)

    Agent->>DBMCP: record_ai_decision(reasoning, recommended_actions)
    DBMCP-->>Agent: {decision_id}

    alt Migrations recommended AND safety checks pass
        loop For each recommended migration
            Agent->>K8sMCP: validate_migration_feasibility(workload, dest_node)
            K8sMCP-->>Agent: {feasible: true/false, reason}

            alt Feasible
                Agent->>K8sMCP: execute_migration(workload, dest_node, dest_zone)
                Note over K8sMCP: 1. Patch node affinity/selector<br/>2. Trigger rolling update<br/>3. Monitor rollout
                K8sMCP-->>Agent: {migration_id, status: "in_progress"}
                Agent->>DBMCP: record_migration_event(migration_id, status, carbon_savings)
            else Not feasible
                Agent->>DBMCP: record_skip(workload_id, reason)
            end
        end
    else No migration needed
        Agent->>DBMCP: record_no_action(reason: "all workloads on green zones or no capacity")
    end

    Agent->>DBMCP: complete_agent_run(agent_run_id, summary)
    Scheduler->>Scheduler: sleep until next interval
```

---

## Diagram 2 — Energy Zone Monitoring (Continuous Background Process)

The Green Energy MCP continuously polls carbon intensity APIs and updates the DB.

```mermaid
sequenceDiagram
    autonumber
    participant Monitor as Green Energy MCP<br/>(Background Monitor)
    participant EnergyAPI as Electricity Maps<br/>/ WattTime API
    participant DBMCP as Internal DB MCP
    participant Agent as AI Agent

    loop Every 5 minutes
        Monitor->>DBMCP: get_all_configured_zones()
        DBMCP-->>Monitor: [{zone_id, electricitymap_zone, watttime_ba}]

        loop For each zone
            Monitor->>EnergyAPI: get_carbon_intensity(zone_code)
            EnergyAPI-->>Monitor: {carbon_intensity, renewable_pct, sources, timestamp}

            Monitor->>Monitor: Validate data freshness<br/>(reject if timestamp > 15 min old)

            alt Data is fresh and valid
                Monitor->>DBMCP: upsert_energy_reading(zone_id, reading)

                alt Zone greenness changed (green↔non-green transition)
                    Monitor->>DBMCP: emit_zone_transition_event(zone_id, old_state, new_state)
                    Note over Monitor,Agent: Optional: wake AI Agent early<br/>for urgent re-evaluation
                end
            else Data stale or API error
                Monitor->>DBMCP: record_energy_data_gap(zone_id, error)
                Note over Monitor: Use last known value with<br/>data_quality='estimated'
            end
        end
    end
```

---

## Diagram 3 — Migration Execution & Status Monitoring

Detailed flow of a single workload migration.

```mermaid
sequenceDiagram
    autonumber
    participant Agent as AI Agent
    participant K8sMCP as Kubernetes MCP
    participant K8s as K8s API Server
    participant DBMCP as Internal DB MCP

    Agent->>K8sMCP: validate_migration_feasibility(workload_ref, dest_node_name)

    K8sMCP->>K8s: GET PodDisruptionBudget for workload namespace
    K8s-->>K8sMCP: pdb_list
    K8sMCP->>K8s: GET node/{dest_node} status
    K8s-->>K8sMCP: node_status
    K8sMCP->>K8s: GET workload spec (Deployment/StatefulSet)
    K8s-->>K8sMCP: workload_spec

    K8sMCP->>K8sMCP: Safety checks:<br/>• dest node Ready?<br/>• dest node not cordoned?<br/>• dest node has capacity?<br/>• PDB allows 1 disruption?<br/>• StatefulSet has opt-in annotation?

    alt All checks pass
        K8sMCP-->>Agent: {feasible: true}

        Agent->>K8sMCP: execute_migration(workload_ref, dest_node_name, migration_type)
        Agent->>DBMCP: record_migration_start(workload_id, src_node, dest_node)

        K8sMCP->>K8s: PATCH workload spec<br/>(add nodeAffinity for dest_node)
        K8s-->>K8sMCP: patch accepted

        K8sMCP->>K8s: GET rollout status (watch)
        Note over K8sMCP,K8s: Wait for new pod scheduled on dest_node<br/>and old pod terminated

        loop Poll every 15 seconds (max 10 min)
            K8sMCP->>K8s: GET deployment rollout status
            K8s-->>K8sMCP: {replicas, readyReplicas, updatedReplicas}

            alt Rollout complete
                K8sMCP-->>Agent: {status: "completed", duration_s: N}
                Agent->>DBMCP: update_migration_status(migration_id, "completed", duration_s)
            else Rollout still in progress
                K8sMCP->>K8sMCP: continue polling
            else Timeout exceeded
                K8sMCP-->>Agent: {status: "failed", reason: "timeout"}
                Agent->>K8sMCP: rollback_migration(workload_ref)
                K8sMCP->>K8s: PATCH workload spec (remove nodeAffinity constraint)
                K8s-->>K8sMCP: patch accepted
                Agent->>DBMCP: update_migration_status(migration_id, "rolled_back")
            end
        end

    else Safety check failed
        K8sMCP-->>Agent: {feasible: false, reason: "PDB violation / node not ready / insufficient capacity"}
        Agent->>DBMCP: record_migration_skip(workload_id, reason)
    end
```

---

## Diagram 4 — Node Metrics Collection

Background process that populates `node_metrics` for AI context.

```mermaid
sequenceDiagram
    autonumber
    participant Collector as Node Metrics Collector<br/>(K8s MCP background)
    participant K8s as K8s Clusters
    participant DBMCP as Internal DB MCP

    loop Every 2 minutes
        Collector->>DBMCP: get_active_clusters()
        DBMCP-->>Collector: [{cluster_id, api_endpoint, kubeconfig_ref}]

        loop For each cluster
            Collector->>K8s: kubectl top nodes
            K8s-->>Collector: [{node, cpu_cores, cpu_pct, memory_gb, memory_pct}]

            Collector->>K8s: kubectl get nodes -o json
            K8s-->>Collector: [{node, status, conditions, pod_count}]

            Collector->>DBMCP: bulk_insert_node_metrics(cluster_id, metrics[])
            Collector->>DBMCP: update_node_status(cluster_id, node_statuses[])
        end
    end
```

---

## Diagram 5 — System Bootstrap & Configuration

One-time setup and initial data population.

```mermaid
sequenceDiagram
    autonumber
    participant Admin as Platform Admin
    participant DBMCP as Internal DB MCP
    participant K8sMCP as Kubernetes MCP
    participant GreenMCP as Green Energy MCP

    Admin->>DBMCP: register_region(name, country, lat, lon)
    Admin->>DBMCP: register_zones(region_id, [{name, electricitymap_zone, watttime_ba}])
    Admin->>DBMCP: register_cluster(name, api_endpoint, kubeconfig_secret_ref, region_id)

    DBMCP-->>Admin: cluster_id

    Admin->>K8sMCP: discover_nodes(cluster_id)
    K8sMCP->>K8sMCP: kubectl get nodes -o json
    K8sMCP-->>Admin: [{node_name, instance_type, labels}]

    Admin->>DBMCP: bulk_register_nodes(cluster_id, nodes[], zone_mapping[])

    Note over Admin,GreenMCP: Initial energy data backfill

    Admin->>GreenMCP: backfill_energy_history(zone_ids[], lookback_hours=24)
    GreenMCP-->>Admin: {readings_inserted: N}

    Admin->>Admin: Verify node_current_status view has data
    Admin->>Admin: Start Green Energy MCP monitor
    Admin->>Admin: Start K8s Metrics Collector
    Admin->>Admin: Deploy AI Agent CronJob
    Note over Admin: System is now autonomous
```
