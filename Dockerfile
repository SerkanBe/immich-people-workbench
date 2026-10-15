FROM python:3.13-slim

LABEL org.opencontainers.image.title="Immich People Workbench" \
      org.opencontainers.image.source="https://github.com/SerkanBe/immich-people-workbench" \
      org.opencontainers.image.licenses="AGPL-3.0-only"

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
RUN groupadd --gid 10001 workbench \
    && useradd --uid 10001 --gid workbench --no-create-home workbench \
    && mkdir /data && chown workbench:workbench /data

COPY people_workbench.py LICENSE /app/
COPY web/ /app/web/
USER 10001:10001
EXPOSE 8766
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8766/healthz', timeout=3).close()"]
ENTRYPOINT ["python", "/app/people_workbench.py"]
CMD ["--host", "0.0.0.0", "--port", "8766", "--state-dir", "/data", "--no-browser"]
