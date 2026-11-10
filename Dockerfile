FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8080 \
    BASE_PATH=/import \
    DATA_DIR=/data

WORKDIR /srv

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY --chmod=755 entrypoint.sh /usr/local/bin/entrypoint.sh
COPY app ./app

RUN groupadd --system --gid 10001 slink \
    && useradd --system --uid 10001 --gid 10001 --home-dir /srv slink \
    && mkdir -p /data \
    && chown slink:slink /data

VOLUME /data
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import os, urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ[\"PORT\"]}{os.environ[\"BASE_PATH\"].rstrip(\"/\")}/healthz', timeout=4)"

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["python", "-m", "app"]
