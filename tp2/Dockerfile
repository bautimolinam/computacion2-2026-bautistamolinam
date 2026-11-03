FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    DIR_DATOS=/app/datos

WORKDIR /app

# Primero solo las dependencias: si cambia el código, esta capa se reutiliza.
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app ./app
RUN mkdir -p /app/datos

EXPOSE 8000

# Forma "exec" (lista): python queda como PID 1 y recibe SIGTERM directamente
# (con la forma shell, /bin/sh sería PID 1 y no reenviaría la señal).
CMD ["python", "-m", "app.main"]
