# DriftWarden: GitOps drift guardrail for standalone Docker hosts.
#
# Run with the host Docker socket mounted (or simply `make start`):
#   docker run -d --name driftwarden \
#     -v /var/run/docker.sock:/var/run/docker.sock \
#     -v "$PWD/data:/data" -e DATABASE_URL=sqlite:////data/warden.db \
#     --env-file .env -e API_HOST=0.0.0.0 -p 8000:8000 driftwarden:latest
#
# The container name prefix "driftwarden" and its own container id are
# automatically excluded from drift evaluation.
#
# Remediation uses the Docker SDK only (containers.create/start/stop/remove over
# the mounted socket); no docker CLI or compose plugin is needed in the image.
# Mount the host's /etc/hostname at /etc/host_hostname:ro (make start does on
# Linux) or set WARDEN_HOSTNAME so the UI, audit rows and pull requests name
# the real host rather than this container's id.

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src \
    API_HOST=0.0.0.0 \
    API_PORT=8000

WORKDIR /app

# `make start` mounts ./data here and points DATABASE_URL at /data/warden.db.
RUN mkdir -p /data
VOLUME ["/data"]

COPY pyproject.toml README.md ./
COPY src ./src
COPY tests/fixtures ./tests/fixtures

RUN pip install --no-cache-dir --upgrade pip && pip install --no-cache-dir .

LABEL warden.ignore="true" \
      org.opencontainers.image.title="DriftWarden" \
      org.opencontainers.image.description="Bidirectional GitOps state reconciliation daemon for standalone Docker hosts"

EXPOSE 8000

CMD ["sh", "-c", "uvicorn web.app:app --host ${API_HOST} --port ${API_PORT}"]
