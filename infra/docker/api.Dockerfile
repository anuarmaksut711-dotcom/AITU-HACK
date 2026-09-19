FROM ghcr.io/astral-sh/uv:0.8.22@sha256:9874eb7afe5ca16c363fe80b294fe700e460df29a55532bbfea234a0f12eddb1 AS uv
FROM python:3.12-slim-bookworm@sha256:782412e85d0f0984994c290652577d4018aff08145c85b262bb63dc0c7522254 AS build
COPY --from=uv /uv /uvx /bin/
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_HTTP_TIMEOUT=300
WORKDIR /app
COPY apps/api/pyproject.toml apps/api/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY apps/api/ ./
RUN uv sync --frozen --no-dev

FROM python:3.12-slim-bookworm@sha256:782412e85d0f0984994c290652577d4018aff08145c85b262bb63dc0c7522254 AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PATH="/app/.venv/bin:$PATH"
RUN groupadd --gid 10001 app && useradd --uid 10001 --gid app --no-create-home app
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /data/audio && chown 10001:10001 /data/audio
WORKDIR /app
COPY --from=build --chown=10001:10001 /app /app
# Sherpa's Linux wheel requires ORT's exact VERS_1.28.2 symbol version.
COPY infra/docker/install-sherpa-runtime.py /tmp/install-sherpa-runtime.py
RUN python /tmp/install-sherpa-runtime.py && ldconfig && rm /tmp/install-sherpa-runtime.py
USER 10001:10001
EXPOSE 8000
CMD [".venv/bin/uvicorn", "aimeet_api.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log", "--no-proxy-headers"]
