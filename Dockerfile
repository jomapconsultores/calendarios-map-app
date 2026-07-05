# ── Calendarios MAP · imagen para Coolify ───────────────────────────────────
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=5000

WORKDIR /app

# curl: lo usa el healthcheck de Coolify dentro del contenedor.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 5000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,os; urllib.request.urlopen('http://127.0.0.1:'+os.getenv('PORT','5000')+'/health').read()" || exit 1

# Respeta $PORT que inyecte Coolify; por defecto 5000
CMD ["sh", "-c", "gunicorn run:app --workers=2 --timeout=120 --bind=0.0.0.0:${PORT:-5000}"]
