FROM python:3.11-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1

# Instala dependencias del sistema y pip
COPY requirements.txt .
RUN apt-get update && \
        apt-get install -y --no-install-recommends gcc libpq-dev && \
        pip install --no-cache-dir -r requirements.txt && \
        apt-get remove -y gcc && \
        apt-get autoremove -y && \
        rm -rf /var/lib/apt/lists/*

# Copia el código de la aplicación
COPY . .

EXPOSE 8000
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
