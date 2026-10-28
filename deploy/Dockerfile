FROM python:3.13-slim-bookworm@sha256:93498f36dc7e24ef233bfdf6cc17d4b8bd32d94ee737143e906460d5f5a914c5
RUN python -m pip install --no-cache-dir uv==0.11.23

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_PYTHON_DOWNLOADS=never \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH" \
    OPENBLAS_NUM_THREADS=1 \
    OMP_NUM_THREADS=1 \
    STITCHFORGE_DATA_DIR=/data \
    STITCHFORGE_CHART_FONT=/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates fonts-noto-cjk libgomp1 \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 stitchforge \
    && useradd --uid 10001 --gid 10001 --no-create-home stitchforge

WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY packages ./packages
COPY schemas ./schemas
RUN uv sync --frozen --no-dev --no-editable --no-cache
COPY catalogs ./catalogs
COPY apps/web/dist ./web
COPY deploy ./deploy

USER 10001:10001
CMD ["python", "-m", "deploy.server"]
