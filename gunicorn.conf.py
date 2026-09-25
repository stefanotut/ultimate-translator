"""
Gunicorn settings (read automatically by `gunicorn app:app`; command-line options win).

Threads matter: a sync worker serves one request at a time, so a slow 100 MB upload
would block its worker and be killed by the timeout. With gthread workers uploads,
page loads and progress polling run side by side.
"""
import os

bind = f"0.0.0.0:{os.getenv('PORT', '5001')}"
worker_class = 'gthread'
workers = int(os.getenv('WEB_CONCURRENCY', '2'))
threads = int(os.getenv('GUNICORN_THREADS', '8'))
timeout = 600
graceful_timeout = 60
