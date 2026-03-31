"""
Green Workload AI — main entry point.

Usage:
    python main.py                  # start the scheduled agent loop
    python main.py --once           # run a single evaluation cycle and exit
    python main.py --setup          # create/update the database schema
"""
import asyncio
import argparse
import sys
import structlog
import logging

from config.settings import settings


def _configure_logging() -> None:
    logging.basicConfig(
        format="%(message)s",
        level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO),
    )
    structlog.configure(
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO)
        ),
    )


async def _run_once() -> None:
    from src.agent.agent import GreenWorkloadAgent
    agent = GreenWorkloadAgent()
    result = await agent.run_cycle()
    print(f"Cycle result: {result}")


def _run_setup() -> None:
    import setup_db
    setup_db.setup_database()


async def _main(args: argparse.Namespace) -> None:
    _configure_logging()
    log = structlog.get_logger()

    if args.setup:
        _run_setup()
        return

    if args.once:
        log.info("Running single evaluation cycle")
        await _run_once()
        return

    log.info(
        "Starting Green Workload AI scheduler",
        model=settings.OLLAMA_MODEL,
        interval=settings.SCHEDULE_INTERVAL_MINUTES,
        dry_run=settings.DRY_RUN,
    )
    from src.scheduler.scheduler import run_scheduler_forever
    await run_scheduler_forever()


def main() -> None:
    parser = argparse.ArgumentParser(description="Green Workload AI")
    parser.add_argument("--once", action="store_true", help="Run a single evaluation cycle and exit")
    parser.add_argument("--setup", action="store_true", help="Run database setup and exit")
    args = parser.parse_args()

    try:
        asyncio.run(_main(args))
    except KeyboardInterrupt:
        print("\nShutdown requested — exiting.")
        sys.exit(0)


if __name__ == "__main__":
    main()
