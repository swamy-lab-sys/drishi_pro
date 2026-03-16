# Gunicorn configuration for Render.com
# gevent worker automatically calls gevent.monkey.patch_all() before loading the app
# — no manual monkey_patch() needed, no RLock warnings.

worker_class = "gevent"
workers = 1
worker_connections = 100
timeout = 300
