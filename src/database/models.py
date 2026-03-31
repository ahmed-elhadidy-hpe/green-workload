from datetime import datetime
from typing import Optional

from sqlalchemy import (
    Column, String, Integer, Text, DECIMAL, JSON,
    ForeignKey, Computed, Boolean, SmallInteger, text,
)
from sqlalchemy.dialects.mysql import DATETIME
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Region(Base):
    __tablename__ = "regions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, server_default=text("(UUID())"))
    name: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    display_name: Mapped[Optional[str]] = mapped_column(String(200))
    country_code: Mapped[Optional[str]] = mapped_column(String(2))
    latitude: Mapped[Optional[float]] = mapped_column(DECIMAL(9, 6))
    longitude: Mapped[Optional[float]] = mapped_column(DECIMAL(9, 6))
    created_at: Mapped[datetime] = mapped_column(DATETIME(fsp=6), server_default=text("CURRENT_TIMESTAMP(6)"))
    updated_at: Mapped[datetime] = mapped_column(
        DATETIME(fsp=6),
        server_default=text("CURRENT_TIMESTAMP(6)"),
        onupdate=datetime.utcnow,
    )

    zones: Mapped[list["Zone"]] = relationship("Zone", back_populates="region")
    clusters: Mapped[list["Cluster"]] = relationship("Cluster", back_populates="region")


class Zone(Base):
    __tablename__ = "zones"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, server_default=text("(UUID())"))
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    region_id: Mapped[str] = mapped_column(String(36), ForeignKey("regions.id", ondelete="RESTRICT"), nullable=False)
    display_name: Mapped[Optional[str]] = mapped_column(String(200))
    energy_provider: Mapped[Optional[str]] = mapped_column(String(200))
    electricitymap_zone: Mapped[Optional[str]] = mapped_column(String(50))
    watttime_ba: Mapped[Optional[str]] = mapped_column(String(50))
    created_at: Mapped[datetime] = mapped_column(DATETIME(fsp=6), server_default=text("CURRENT_TIMESTAMP(6)"))
    updated_at: Mapped[datetime] = mapped_column(
        DATETIME(fsp=6),
        server_default=text("CURRENT_TIMESTAMP(6)"),
        onupdate=datetime.utcnow,
    )

    region: Mapped["Region"] = relationship("Region", back_populates="zones")
    nodes: Mapped[list["Node"]] = relationship("Node", back_populates="zone")
    energy_readings: Mapped[list["EnergyReading"]] = relationship("EnergyReading", back_populates="zone")


class Cluster(Base):
    __tablename__ = "clusters"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, server_default=text("(UUID())"))
    name: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    display_name: Mapped[Optional[str]] = mapped_column(String(200))
    kubeconfig_secret_ref: Mapped[Optional[str]] = mapped_column(String(500))
    api_endpoint: Mapped[Optional[str]] = mapped_column(String(500))
    region_id: Mapped[Optional[str]] = mapped_column(String(36), ForeignKey("regions.id", ondelete="SET NULL"))
    status: Mapped[str] = mapped_column(String(50), nullable=False, server_default="active")
    kubernetes_version: Mapped[Optional[str]] = mapped_column(String(50))
    managed_by: Mapped[Optional[str]] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DATETIME(fsp=6), server_default=text("CURRENT_TIMESTAMP(6)"))
    updated_at: Mapped[datetime] = mapped_column(
        DATETIME(fsp=6),
        server_default=text("CURRENT_TIMESTAMP(6)"),
        onupdate=datetime.utcnow,
    )

    region: Mapped[Optional["Region"]] = relationship("Region", back_populates="clusters")
    nodes: Mapped[list["Node"]] = relationship("Node", back_populates="cluster")
    workloads: Mapped[list["Workload"]] = relationship("Workload", back_populates="cluster")


class Node(Base):
    __tablename__ = "nodes"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, server_default=text("(UUID())"))
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    cluster_id: Mapped[str] = mapped_column(String(36), ForeignKey("clusters.id", ondelete="CASCADE"), nullable=False)
    zone_id: Mapped[Optional[str]] = mapped_column(String(36), ForeignKey("zones.id", ondelete="SET NULL"))
    provider_id: Mapped[Optional[str]] = mapped_column(String(500))
    instance_type: Mapped[Optional[str]] = mapped_column(String(100))
    operating_system: Mapped[Optional[str]] = mapped_column(String(100))
    kernel_version: Mapped[Optional[str]] = mapped_column(String(100))
    container_runtime: Mapped[Optional[str]] = mapped_column(String(100))
    allocatable_cpu: Mapped[Optional[float]] = mapped_column(DECIMAL(10, 3))
    allocatable_memory_gb: Mapped[Optional[float]] = mapped_column(DECIMAL(10, 3))
    allocatable_pods: Mapped[Optional[int]] = mapped_column(Integer)
    labels: Mapped[Optional[dict]] = mapped_column(JSON, server_default=text("('{}')"))
    taints: Mapped[Optional[dict]] = mapped_column(JSON, server_default=text("('[]')"))
    status: Mapped[str] = mapped_column(String(50), nullable=False, server_default="Ready")
    is_cordoned: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="0")
    is_migration_target: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="1")
    migration_opt_out: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DATETIME(fsp=6), server_default=text("CURRENT_TIMESTAMP(6)"))
    updated_at: Mapped[datetime] = mapped_column(
        DATETIME(fsp=6),
        server_default=text("CURRENT_TIMESTAMP(6)"),
        onupdate=datetime.utcnow,
    )

    cluster: Mapped["Cluster"] = relationship("Cluster", back_populates="nodes")
    zone: Mapped[Optional["Zone"]] = relationship("Zone", back_populates="nodes")
    metrics: Mapped[list["NodeMetric"]] = relationship("NodeMetric", back_populates="node")
    workloads: Mapped[list["Workload"]] = relationship("Workload", back_populates="current_node")


class EnergyReading(Base):
    __tablename__ = "energy_readings"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, server_default=text("(UUID())"))
    zone_id: Mapped[str] = mapped_column(String(36), ForeignKey("zones.id", ondelete="CASCADE"), nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DATETIME(fsp=6), nullable=False)
    carbon_intensity: Mapped[Optional[float]] = mapped_column(DECIMAL(8, 3))
    renewable_percentage: Mapped[Optional[float]] = mapped_column(DECIMAL(5, 2))
    energy_sources: Mapped[Optional[dict]] = mapped_column(JSON)
    is_green: Mapped[Optional[int]] = mapped_column(
        SmallInteger,
        Computed("(renewable_percentage >= 50)", persisted=True),
    )
    data_source: Mapped[Optional[str]] = mapped_column(String(100))
    data_quality: Mapped[Optional[str]] = mapped_column(String(50))
    created_at: Mapped[datetime] = mapped_column(DATETIME(fsp=6), server_default=text("CURRENT_TIMESTAMP(6)"))

    zone: Mapped["Zone"] = relationship("Zone", back_populates="energy_readings")


class NodeMetric(Base):
    __tablename__ = "node_metrics"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, server_default=text("(UUID())"))
    node_id: Mapped[str] = mapped_column(String(36), ForeignKey("nodes.id", ondelete="CASCADE"), nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DATETIME(fsp=6), nullable=False)
    cpu_usage_cores: Mapped[Optional[float]] = mapped_column(DECIMAL(10, 3))
    cpu_usage_percent: Mapped[Optional[float]] = mapped_column(DECIMAL(5, 2))
    memory_usage_gb: Mapped[Optional[float]] = mapped_column(DECIMAL(10, 3))
    memory_usage_percent: Mapped[Optional[float]] = mapped_column(DECIMAL(5, 2))
    pod_count: Mapped[Optional[int]] = mapped_column(Integer)
    network_in_mbps: Mapped[Optional[float]] = mapped_column(DECIMAL(10, 3))
    network_out_mbps: Mapped[Optional[float]] = mapped_column(DECIMAL(10, 3))
    is_overloaded: Mapped[Optional[int]] = mapped_column(
        SmallInteger,
        Computed("(cpu_usage_percent > 80 OR memory_usage_percent > 80)", persisted=True),
    )
    created_at: Mapped[datetime] = mapped_column(DATETIME(fsp=6), server_default=text("CURRENT_TIMESTAMP(6)"))

    node: Mapped["Node"] = relationship("Node", back_populates="metrics")


class Workload(Base):
    __tablename__ = "workloads"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, server_default=text("(UUID())"))
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    namespace: Mapped[str] = mapped_column(String(200), nullable=False, server_default="default")
    cluster_id: Mapped[str] = mapped_column(String(36), ForeignKey("clusters.id", ondelete="CASCADE"), nullable=False)
    workload_type: Mapped[str] = mapped_column(String(50), nullable=False)
    current_node_id: Mapped[Optional[str]] = mapped_column(String(36), ForeignKey("nodes.id", ondelete="SET NULL"))
    replica_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    priority: Mapped[str] = mapped_column(String(50), nullable=False, server_default="normal")
    migration_allowed: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="1")
    stateful: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="0")
    labels: Mapped[Optional[dict]] = mapped_column(JSON, server_default=text("('{}')"))
    annotations: Mapped[Optional[dict]] = mapped_column(JSON, server_default=text("('{}')"))
    resource_requests_cpu: Mapped[Optional[float]] = mapped_column(DECIMAL(10, 3))
    resource_requests_memory_gb: Mapped[Optional[float]] = mapped_column(DECIMAL(10, 3))
    last_seen_at: Mapped[Optional[datetime]] = mapped_column(DATETIME(fsp=6))
    created_at: Mapped[datetime] = mapped_column(DATETIME(fsp=6), server_default=text("CURRENT_TIMESTAMP(6)"))
    updated_at: Mapped[datetime] = mapped_column(
        DATETIME(fsp=6),
        server_default=text("CURRENT_TIMESTAMP(6)"),
        onupdate=datetime.utcnow,
    )

    cluster: Mapped["Cluster"] = relationship("Cluster", back_populates="workloads")
    current_node: Mapped[Optional["Node"]] = relationship("Node", back_populates="workloads")
    migration_events: Mapped[list["MigrationEvent"]] = relationship("MigrationEvent", back_populates="workload")


class AgentRun(Base):
    __tablename__ = "agent_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, server_default=text("(UUID())"))
    started_at: Mapped[datetime] = mapped_column(DATETIME(fsp=6), nullable=False)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DATETIME(fsp=6))
    status: Mapped[str] = mapped_column(String(50), nullable=False, server_default="running")
    clusters_evaluated: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    workloads_evaluated: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    migrations_initiated: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    error_message: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DATETIME(fsp=6), server_default=text("CURRENT_TIMESTAMP(6)"))

    ai_decisions: Mapped[list["AiDecision"]] = relationship("AiDecision", back_populates="agent_run")


class AiDecision(Base):
    __tablename__ = "ai_decisions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, server_default=text("(UUID())"))
    agent_run_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False
    )
    timestamp: Mapped[datetime] = mapped_column(DATETIME(fsp=6), nullable=False)
    model_name: Mapped[Optional[str]] = mapped_column(String(200))
    input_context: Mapped[Optional[dict]] = mapped_column(JSON)
    reasoning: Mapped[Optional[str]] = mapped_column(Text)
    decision_type: Mapped[Optional[str]] = mapped_column(String(50))
    recommended_actions: Mapped[Optional[dict]] = mapped_column(JSON)
    safety_check_passed: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="1")
    safety_check_notes: Mapped[Optional[str]] = mapped_column(Text)
    execution_started: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DATETIME(fsp=6), server_default=text("CURRENT_TIMESTAMP(6)"))

    agent_run: Mapped["AgentRun"] = relationship("AgentRun", back_populates="ai_decisions")
    migration_events: Mapped[list["MigrationEvent"]] = relationship("MigrationEvent", back_populates="ai_decision")


class MigrationEvent(Base):
    __tablename__ = "migration_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, server_default=text("(UUID())"))
    workload_id: Mapped[Optional[str]] = mapped_column(String(36), ForeignKey("workloads.id", ondelete="SET NULL"))
    workload_name: Mapped[str] = mapped_column(String(200), nullable=False)
    namespace: Mapped[str] = mapped_column(String(200), nullable=False)
    cluster_id: Mapped[Optional[str]] = mapped_column(String(36), ForeignKey("clusters.id", ondelete="SET NULL"))
    source_node_id: Mapped[Optional[str]] = mapped_column(String(36), ForeignKey("nodes.id", ondelete="SET NULL"))
    destination_node_id: Mapped[Optional[str]] = mapped_column(String(36), ForeignKey("nodes.id", ondelete="SET NULL"))
    source_zone_id: Mapped[Optional[str]] = mapped_column(String(36), ForeignKey("zones.id", ondelete="SET NULL"))
    destination_zone_id: Mapped[Optional[str]] = mapped_column(String(36), ForeignKey("zones.id", ondelete="SET NULL"))
    migration_type: Mapped[str] = mapped_column(String(50), nullable=False, server_default="affinity")
    status: Mapped[str] = mapped_column(String(50), nullable=False, server_default="pending")
    ai_decision_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("ai_decisions.id", ondelete="SET NULL")
    )
    trigger_reason: Mapped[Optional[str]] = mapped_column(Text)
    source_carbon_intensity: Mapped[Optional[float]] = mapped_column(DECIMAL(8, 3))
    destination_carbon_intensity: Mapped[Optional[float]] = mapped_column(DECIMAL(8, 3))
    carbon_savings_estimate: Mapped[Optional[float]] = mapped_column(DECIMAL(8, 3))
    started_at: Mapped[Optional[datetime]] = mapped_column(DATETIME(fsp=6))
    completed_at: Mapped[Optional[datetime]] = mapped_column(DATETIME(fsp=6))
    duration_seconds: Mapped[Optional[int]] = mapped_column(Integer)
    error_message: Mapped[Optional[str]] = mapped_column(Text)
    rollback_attempted: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DATETIME(fsp=6), server_default=text("CURRENT_TIMESTAMP(6)"))
    updated_at: Mapped[datetime] = mapped_column(
        DATETIME(fsp=6),
        server_default=text("CURRENT_TIMESTAMP(6)"),
        onupdate=datetime.utcnow,
    )

    workload: Mapped[Optional["Workload"]] = relationship("Workload", back_populates="migration_events")
    ai_decision: Mapped[Optional["AiDecision"]] = relationship("AiDecision", back_populates="migration_events")
