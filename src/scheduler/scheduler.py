import asyncio
import structlog
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from config.settings import settings
from src.agent.agent import GreenWorkloadAgent

log = structlog.get_logger()

_agent: GreenWorkloadAgent | None = None


async def _run_evaluation() -> None:
    """Scheduled callback: run one agent evaluation cycle."""
    log.info("Scheduled evaluation cycle started")
    global _agent
    if _agent is None:
        _agent = GreenWorkloadAgent()
    try:
        result = await _agent.run_cycle()
        log.info("Scheduled evaluation complete", **result)
    except Exception as e:
        log.error("Scheduled evaluation failed", error=str(e))


def build_scheduler() -> AsyncIOScheduler:
    """Build and return a configured APScheduler instance."""
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        _run_evaluation,
        trigger=IntervalTrigger(seconds=settings.SCHEDULE_INTERVAL_SECONDS),
        id="green_workload_eval",
        name="Green Workload Evaluation",
        replace_existing=True,
        max_instances=1,
    )
    return scheduler


async def run_scheduler_forever() -> None:
    """Start the scheduler and keep the event loop alive."""
    scheduler = build_scheduler()
    scheduler.start()
    log.info(
        "Scheduler started",
        interval_seconds=settings.SCHEDULE_INTERVAL_SECONDS,
        dry_run=settings.DRY_RUN,
    )
    # Run once immediately on startup
    # await _run_evaluation()
    try:
        while True:
            await asyncio.sleep(60)
    except (KeyboardInterrupt, SystemExit):
        log.info("Scheduler shutting down")
        scheduler.shutdown()
