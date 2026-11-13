FROM python:3.12-slim AS base

RUN apt-get update && apt-get install -y --no-install-recommends git curl && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY techscan/ techscan/
COPY config/examples/ config/examples/

RUN pip install --no-cache-dir ".[all]"

# Default to anthropic backend in containers (claude-cli needs interactive login)
ENV TECHSCAN_DEFAULT_BACKEND=anthropic

ENTRYPOINT ["techscan"]
CMD ["--help"]
