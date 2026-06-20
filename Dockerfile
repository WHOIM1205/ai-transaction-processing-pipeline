# Single application image, used by BOTH the `api` and `worker` services.
#
# WHY ONE IMAGE FOR TWO SERVICES
# ------------------------------
# The API and the Celery worker run the exact same codebase; only the start
# command differs (set per-service in docker-compose.yml). One image means one
# build, one set of dependencies, and guaranteed parity between the process
# that enqueues work and the process that runs it.

FROM python:3.12-slim

# Predictable, container-friendly Python behaviour:
#   - don't write .pyc files (smaller, cleaner layers)
#   - unbuffered stdout/stderr so logs appear immediately in `docker logs`
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

WORKDIR /app

# `curl` is needed by the API container's healthcheck (defined in compose).
# Installed in its own layer and apt lists cleaned to keep the image small.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# Install dependencies first, in a separate layer, so they are cached and only
# re-installed when requirements.txt changes (not on every code edit).
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the project (app package, alembic config, etc.).
COPY . .

EXPOSE 8000

# Default command runs the API. The worker service overrides this in compose.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
