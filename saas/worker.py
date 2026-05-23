"""
worker.py

RQ worker entry point for CA Assist background agent jobs.

Usage (from the saas/ directory with venv active):

    # Start Redis first (WSL, Docker, or Memurai on Windows):
    #   wsl redis-server  OR  docker run -p 6379:6379 redis

    # Then start the worker:
    python worker.py

    # Or with explicit queue name:
    python worker.py --queue ca_agent

Environment variables (same as app.py):
    REDIS_URL   — default: redis://localhost:6379/0
    CA_DB_PATH  — path to the SQLite database (default: ca_saas.db)

The worker imports tasks.ai_task which in turn uses db.py directly,
so no Flask app context is required.  The worker can run on a remote
server; only DB_PATH must point to the same SQLite file (or a shared
network mount / future Postgres upgrade).
"""

import logging
import os
import sys

# ── Bootstrap: make sure saas/ sibling modules are importable ─────────────
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

# Load .env if present (mirrors app.py behaviour)
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(_HERE, ".env"))
except ImportError:
    pass

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
logger = logging.getLogger("ca_worker")

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
QUEUE_NAME = "ca_agent"


def main():
    import argparse

    parser = argparse.ArgumentParser(description="CA Assist RQ Worker")
    parser.add_argument(
        "--queue", default=QUEUE_NAME,
        help=f"RQ queue name to listen on (default: {QUEUE_NAME})"
    )
    parser.add_argument(
        "--redis", default=REDIS_URL,
        help=f"Redis URL (default: {REDIS_URL})"
    )
    parser.add_argument(
        "--burst", action="store_true",
        help="Exit after all current jobs are processed (useful for testing)"
    )
    args = parser.parse_args()

    try:
        import redis as _redis_lib
        from rq import Queue, Worker, Connection
    except ImportError:
        logger.error(
            "rq and redis packages are required. Run:\n"
            "  pip install rq redis"
        )
        sys.exit(1)

    redis_conn = _redis_lib.from_url(args.redis)

    # Verify Redis is reachable before blocking
    try:
        redis_conn.ping()
        logger.info("Connected to Redis at %s", args.redis)
    except Exception as exc:
        logger.error(
            "Cannot reach Redis at %s: %s\n\n"
            "Start Redis first:\n"
            "  Option A (WSL):    wsl redis-server\n"
            "  Option B (Docker): docker run -p 6379:6379 redis\n"
            "  Option C (Win):    Install Memurai (https://www.memurai.com)\n",
            args.redis, exc,
        )
        sys.exit(1)

    queue = Queue(args.queue, connection=redis_conn)
    logger.info(
        "Worker listening on queue '%s' — waiting for jobs...\n"
        "  To enqueue a job: POST /work/<id>/run in the web app.\n"
        "  Press Ctrl+C to stop.\n",
        args.queue,
    )

    with Connection(redis_conn):
        worker = Worker([queue])
        worker.work(burst=args.burst)


if __name__ == "__main__":
    main()
