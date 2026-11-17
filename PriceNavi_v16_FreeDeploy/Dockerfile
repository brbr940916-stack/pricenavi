FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY cloud_app.py /app/cloud_app.py
COPY backup_db.py /app/backup_db.py

COPY icons /app/icons
RUN pip install --no-cache-dir "cryptography>=42,<46" && useradd --create-home --uid 10001 appuser && mkdir -p /data && chown -R appuser:appuser /data /app
USER appuser
ENV DATABASE_PATH=/data/price_tracker.db HOST=0.0.0.0 PORT=8080
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=3)" || exit 1
CMD ["python", "cloud_app.py"]
