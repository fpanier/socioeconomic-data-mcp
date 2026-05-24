# syntax=docker/dockerfile:1
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    MCP_TRANSPORT=streamable-http \
    MCP_HOST=0.0.0.0 \
    MCP_PORT=8000

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir .

# MCP_AUTH_TOKEN must be supplied at runtime (the server refuses HTTP without it).
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
    CMD python -c "import os,urllib.request,sys; urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"MCP_PORT\",\"8000\")}/healthz'); " || exit 1

CMD ["python", "-m", "socioeconomic_data_mcp"]
