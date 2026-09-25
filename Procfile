# 1 solo worker: i task di traduzione vivono in un dict in memoria (app.py),
# con 2 processi il polling di /api/status finisce meta' delle volte nel worker
# sbagliato e risponde "Task non trovato". I thread danno comunque concorrenza.
web: gunicorn app:app --bind 0.0.0.0:$PORT --timeout 600 --workers 1 --threads 8
