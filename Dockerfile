# DriftWarden: GitOps drift guardrail for standalone Docker hosts.
#
# Run with the host Docker socket mounted:
#   docker run -d --name driftwarden \
#     -v /var/run/docker.sock:/var/run/docker.sock \
#     --env-file .env -p 8000:8000 driftwarden:latest
#
# The container name prefix "driftwarden" and its own container id are
# automatically excluded from drift evaluation.

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src \
    API_HOST=0.0.0.0 \
    API_PORT=8000

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
COPY tests/fixtures ./tests/fixtures

RUN pip install --no-cache-dir --upgrade pip && pip install --no-cache-dir .

LABEL warden.ignore="true" \
      org.opencontainers.image.title="DriftWarden" \
      org.opencontainers.image.description="Bidirectional GitOps state reconciliation daemon for standalone Docker hosts"

EXPOSE 8000

CMD ["sh", "-c", "uvicorn web.app:app --host ${API_HOST} --port ${API_PORT}"]
