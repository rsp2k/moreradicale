FROM python:3.12-slim

# Install dependencies
RUN apt-get update && apt-get install -y \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Create radicale user
RUN useradd --system --user-group --home-dir /data --shell /bin/false radicale

# Copy our Radicale code WITH RFC 6638 support
WORKDIR /app
COPY radicale/ /app/radicale/
COPY pyproject.toml /app/
COPY README.md /app/

# Install our Radicale
RUN pip install --no-cache-dir .

# Create data directory
RUN mkdir -p /data /config && chown -R radicale:radicale /data

# Switch to radicale user
USER radicale

# Expose port
EXPOSE 5232

# Health check
HEALTHCHECK --interval=10s --timeout=5s --retries=3 \
  CMD curl -f http://localhost:5232/ || exit 1

# Run Radicale
CMD ["radicale", "--config", "/config/config"]
