"""Standalone entry point for background queue workers.

Runs as a long-lived process (no HTTP server) consuming jobs from Redis Streams.
Scale this via ECS service desired-count; queue-depth auto-scaling drives it up
when pending messages accumulate.

Usage (from ECS task command):
    python -m shared.worker_entrypoint --concurrency 4 --queues default,ai,emails,webhooks
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import signal
import sys

logger = logging.getLogger(__name__)

_stop_event: asyncio.Event | None = None


def _handle_signal(sig, frame):
    logger.info("worker_entrypoint_signal_received signal=%s", sig)
    if _stop_event:
        _stop_event.set()


async def run(*, queues: list[str], concurrency: int) -> None:
    global _stop_event
    _stop_event = asyncio.Event()

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    # Lazy imports — keep startup fast and avoid circular imports at module level
    from shared.background_queue import start_background_queue_worker, stop_background_queue_worker, BackgroundQueue
    from shared.database import create_database

    logger.info("background_worker_starting queues=%s concurrency=%s", queues, concurrency)
    db_pool = create_database(application_name="background-worker")
    await db_pool.initialize()

    handles = []
    for queue_name in queues:
        q = BackgroundQueue(stream_name=queue_name)
        handle = await q.start_worker(db_pool)
        if handle:
            handles.append(handle)
            logger.info("background_worker_queue_started queue=%s", queue_name)

    if not handles:
        logger.error("background_worker_no_queues_started — exiting")
        sys.exit(1)

    logger.info("background_worker_ready handles=%s", len(handles))

    await _stop_event.wait()
    logger.info("background_worker_stopping")

    for handle in handles:
        await stop_background_queue_worker(handle)

    if hasattr(db_pool, "close"):
        await db_pool.close()

    logger.info("background_worker_stopped")


def main() -> None:
    parser = argparse.ArgumentParser(description="Pulse background queue worker")
    parser.add_argument("--queues", default="default", help="Comma-separated queue names")
    parser.add_argument("--concurrency", type=int, default=4, help="Per-queue consumer concurrency")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    queue_list = [q.strip() for q in args.queues.split(",") if q.strip()]
    asyncio.run(run(queues=queue_list, concurrency=args.concurrency))


if __name__ == "__main__":
    main()
