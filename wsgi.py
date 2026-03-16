"""
Minimal test app — confirms gunicorn can bind on Render.
If this works, the issue is in server.py imports.
"""
from flask import Flask, jsonify

app = Flask(__name__)

@app.route('/health')
def health():
    return jsonify({'status': 'ok', 'message': 'gunicorn is working'})

@app.route('/')
def index():
    return '<h1>Drishi Pro — gunicorn OK</h1><p>Real app loading next.</p>'
