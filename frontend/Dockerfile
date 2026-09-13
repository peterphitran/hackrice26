# Demo server image. Build context is the repository root so both the backend
# source and the static page are available.
FROM python:3.13-slim

# git is a hard requirement, not a convenience: revision resolution, diff parsing
# and the recorded demo's repository seeding all shell out to it.
RUN apt-get update \
 && apt-get install -y --no-install-recommends git \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY backend /app/backend
COPY frontend /app/frontend

# Installing the project itself pulls its declared dependencies, so the image can
# never drift from pyproject.toml.
RUN pip install --no-cache-dir --upgrade pip && pip install --no-cache-dir /app/backend

# scripts/ and fixtures/ are not wheel packages, so the demo runs from source.
ENV PYTHONPATH=/app/backend \
    PYTHONUNBUFFERED=1 \
    GIT_CONFIG_GLOBAL=/app/.gitconfig

# The recorded demo seeds throwaway repositories, and git refuses to commit
# without an identity. safe.directory keeps git from rejecting mounted repos
# that belong to a different uid inside the container.
RUN git config --file /app/.gitconfig user.name "Lou Demo" \
 && git config --file /app/.gitconfig user.email "demo@lou.local" \
 && git config --file /app/.gitconfig safe.directory '*'

EXPOSE 8100
CMD ["python", "-m", "uvicorn", "frontend.server:app", \
     "--host", "0.0.0.0", "--port", "8100", "--log-level", "warning"]
