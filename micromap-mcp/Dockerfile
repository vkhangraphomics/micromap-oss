# micromap-mcp/Dockerfile
FROM python:3.11-slim

WORKDIR /app

# Sibling package — copy it in so we can pip install both
COPY ./micromap-mapforge /tmp/micromap-mapforge
COPY ./micromap-mcp /app

# Install both. micromap-mapforge[omics] (#286 G3) pulls anndata/scipy/h5py so the
# MCP's mapforge_inspect can natively read AnnData (.h5ad/.loom) transcriptomics
# data — otherwise those return an actionable "install [omics]" hint. This adds
# weight to the image; it's deliberate so the deployed inspector is complete.
RUN pip install --no-cache-dir "/tmp/micromap-mapforge[omics]" && \
    pip install --no-cache-dir /app && \
    rm -rf /tmp/micromap-mapforge /root/.cache

EXPOSE 8200
CMD ["python", "-m", "micromap_mcp.server"]
