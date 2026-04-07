from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    DB_HOST: str = "127.0.0.1"
    DB_PORT: int = 3306
    DB_USER: str = "root"
    DB_PASSWORD: str = ""
    DB_NAME: str = "GREEN_WORKLOAD_DB"
    OLLAMA_BASE_URL: str = "http://localhost:11434/v1"
    OLLAMA_MODEL: str = "llama3.1:8b"
    ELECTRICITY_MAPS_API_KEY: str = ""
    WATTTIME_USERNAME: str = ""
    WATTTIME_PASSWORD: str = ""
    SCHEDULE_INTERVAL_SECONDS: int = 20
    DRY_RUN: bool = False
    LOG_LEVEL: str = "DEBUG"
    KUBECONFIG: str = "~/.kube/config"
    MAX_CONCURRENT_MIGRATIONS: int = 5
    NODE_CPU_THRESHOLD: float = 80.0
    NODE_MEMORY_THRESHOLD: float = 80.0
    MIN_RENEWABLE_PCT: float = 50.0
    ENERGY_DATA_STALENESS_MINUTES: int = 15
    GREEN_ENERGY_MCP_CMD: str = "python"
    GREEN_ENERGY_MCP_ARGS: str = "-m src.mcp_servers.green_energy.server"
    K8S_MCP_CMD: str = "python"
    K8S_MCP_ARGS: str = "-m src.mcp_servers.kubernetes_mcp.server"
    DB_MCP_CMD: str = "python"
    DB_MCP_ARGS: str = "-m src.mcp_servers.internal_db.server"
    SIMULATED_MIGRATION_EXEC_TIME_BETWEEN_SEC: tuple = (5, 10)

    class Config:
        env_file = ".env"
        extra = "ignore"


settings = Settings()
