ARG NODE_IMAGE=node:22-alpine
ARG PYTHON_IMAGE=python:3.12-slim-bookworm
ARG NGINX_IMAGE=nginx:1.28-alpine
FROM ${NODE_IMAGE} AS frontend-build
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM ${PYTHON_IMAGE} AS backend
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 UV_LINK_MODE=copy PATH=/app/.venv/bin:$PATH
WORKDIR /app
RUN pip install --no-cache-dir uv==0.7.21 && useradd --uid 10001 --create-home nexusdesk && mkdir -p /data/credentials && chown -R nexusdesk:nexusdesk /data
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY backend/src ./src
COPY backend/migrations ./migrations
COPY backend/alembic.ini ./
COPY deploy/container ./deploy
RUN uv sync --frozen --no-dev --no-editable
USER nexusdesk
CMD ["uvicorn", "agent_platform.apps.api.main:app", "--host", "0.0.0.0", "--port", "8000"]

FROM backend AS quickstart
USER root
RUN apt-get update && apt-get install -y --no-install-recommends nginx supervisor tini && rm -rf /var/lib/apt/lists/*
COPY --from=frontend-build /build/dist /app/web
COPY deploy/quickstart/supervisord.conf /app/deploy/supervisord.conf
COPY deploy/quickstart/nginx.conf.template /app/deploy/nginx.conf.template
USER nexusdesk
EXPOSE 8080
ENTRYPOINT ["/usr/bin/tini", "--", "python", "/app/deploy/bootstrap.py", "quickstart"]

FROM ${NGINX_IMAGE} AS web
COPY --from=frontend-build /build/dist /usr/share/nginx/html
COPY deploy/production/nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 8080
