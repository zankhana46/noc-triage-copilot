# Shared image for the api and ui services (see docker-compose.yml, which
# overrides the command per service). Not used for Phase 4/5 (GPU work
# runs on a rented GPU box — see requirements-finetune.txt / -serving.txt).
FROM python:3.12-slim

RUN pip install --no-cache-dir uv==0.13.5

WORKDIR /app

COPY pyproject.toml uv.lock* ./
RUN uv sync --extra dev --no-install-project

COPY . .
RUN uv sync --extra dev

ENV PATH="/app/.venv/bin:${PATH}"

EXPOSE 8000 8501
