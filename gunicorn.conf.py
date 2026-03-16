# Gunicorn configuration for Render.com
# Using sync + threading workers — gevent conflicts with sqlite3/background threads

workers = 1
threads = 4
timeout = 120
worker_class = "gthread"
