# micromap-mapforge/Dockerfile — CLI image for `mapforge`.
#
# Build (from the micromap-mapforge directory):
#   docker build -t mapforge:dev .
#
# Run against a local Neo4j (host networking on Linux, bridged on macOS/Windows):
#   docker run --rm -v $(pwd)/bundle:/work mapforge:dev \
#       resolve --bundle /work \
#       --neo4j-uri bolt://host.docker.internal:7687 \
#       --neo4j-user neo4j --neo4j-password test
#
# The container's WORKDIR is /work — mount your bundle there. Inputs and
# outputs both live in the bind-mounted directory; nothing the CLI writes
# persists inside the image.

FROM python:3.11-slim

# Build-arg surfaces the Neo4j driver pin in image metadata for fleet audits.
# Update when bumping the driver pin in pyproject.toml.
ARG NEO4J_DRIVER_PIN=">=5.15,<7"
LABEL org.opencontainers.image.title="MicroMap MapForge CLI" \
      org.opencontainers.image.source="https://github.com/vkhangraphomics/MicroMap" \
      org.opencontainers.image.description="Reviewable ingestion pipeline for biomedical KGs." \
      io.graphomics.mapforge.neo4j-driver="${NEO4J_DRIVER_PIN}"

# Build dependencies for any C-extension wheels that don't have manylinux
# binaries on Python 3.11 slim (rapidfuzz, pyarrow). Removed in the same
# layer to keep the image lean.
RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc g++ \
    && rm -rf /var/lib/apt/lists/*

# Install MapForge from a copy of the source tree. We deliberately do NOT
# `pip install -e .` here — production-style install means the image ships
# without writable source under site-packages.
COPY pyproject.toml /pkg/
COPY micromap_mapforge /pkg/micromap_mapforge
RUN pip install --no-cache-dir /pkg \
    && apt-get purge -y --auto-remove gcc g++ \
    && rm -rf /pkg /root/.cache /var/lib/apt/lists/*

# Non-root user (uid 1000 matches the conventional Linux first user, so files
# the container writes to bind-mounted volumes own correctly on the host).
RUN useradd --uid 1000 --create-home --shell /bin/bash mapforge
WORKDIR /work
RUN chown mapforge:mapforge /work
USER mapforge

# Smoke test at build time — fails the build early if the install is broken.
RUN mapforge --help > /dev/null

# Pre-set UTF-8 stdout encoding so `mapforge --help` and the import-kg
# subcommand's Unicode docstrings render in non-UTF-8 terminals
# (the runtime fix from #115 also handles this, but the env var is the
# defense-in-depth for containers piped through cp1252-only shells).
ENV PYTHONIOENCODING=utf-8

ENTRYPOINT ["mapforge"]
CMD ["--help"]
