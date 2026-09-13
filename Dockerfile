# ---------------------------------------------------------------------------
# Imagen de producción — Amigo Secreto
# Build:  docker build -t amigo-secreto .
# Run:    docker run -p 8000:8000 --env-file .env -v $(pwd)/data:/app/data amigo-secreto
# ---------------------------------------------------------------------------
FROM python:3.12-slim

# Salida sin buffer (los logs aparecen en tiempo real) y sin .pyc.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Las dependencias se copian primero para aprovechar la caché de capas:
# si solo cambia el código, Docker no reinstala los paquetes.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
RUN mkdir -p /app/data

# Usuario sin privilegios: nunca ejecutar como root en producción.
RUN useradd --create-home --uid 1001 appuser \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; \
        sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health').status==200 else 1)"

# $PORT lo inyectan plataformas como Render, Railway o Fly.io.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
