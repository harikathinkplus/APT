"""
Gunicorn production configuration for the Adaptive Practice Tool API.

Usage:
    gunicorn -c gunicorn.conf.py app:app

Each worker is a full Uvicorn async event loop — capable of handling thousands
of concurrent connections with non-blocking asyncpg DB calls.

Sizing guide:
    workers = (2 × CPU_cores) + 1   (common rule of thumb for async workers)
    DB_POOL_MAX × WORKERS < Postgres max_connections (default 100)
"""
import os

# ── Binding ────────────────────────────────────────────────────────────────────
bind = f"0.0.0.0:{os.getenv('PORT', '5000')}"

# ── Workers ────────────────────────────────────────────────────────────────────
# UvicornWorker: each worker runs a full asyncio event loop
worker_class = "uvicorn.workers.UvicornWorker"
workers = int(os.getenv("WORKERS", 4))

# ── Timeouts & connections ─────────────────────────────────────────────────────
timeout = 30          # seconds before a worker is killed and restarted
keepalive = 5         # seconds to keep idle connections open
worker_connections = 1000  # max simultaneous connections per worker

# ── Logging ────────────────────────────────────────────────────────────────────
loglevel = os.getenv("LOG_LEVEL", "info")
accesslog = "-"   # stdout
errorlog  = "-"   # stderr

# ── Graceful restart ───────────────────────────────────────────────────────────
graceful_timeout = 30   # seconds to finish in-flight requests before hard kill
max_requests = 1000     # recycle workers after N requests (prevents memory leaks)
max_requests_jitter = 50  # randomise recycling to avoid thundering-herd restarts
