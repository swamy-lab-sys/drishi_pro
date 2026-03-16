# Gunicorn configuration for Render.com
# eventlet.monkey_patch() must run before ANYTHING else — including gunicorn internals
import eventlet
eventlet.monkey_patch()

worker_class = "eventlet"
workers = 1
timeout = 300
bind = "0.0.0.0:10000"  # overridden by --bind in startCommand
