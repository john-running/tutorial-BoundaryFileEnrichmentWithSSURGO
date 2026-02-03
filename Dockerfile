FROM python:3.11-slim

# System deps (GDAL + Rasterio) and SSL
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates gdal-bin libgdal-dev python3-rasterio && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# Copy app
COPY . /app

# Environment
ENV PORT=8080 \
    PYTHONUNBUFFERED=1 \
    R_FACTOR_TIF=/app/data/R-Factor_CONUS.tif

# Expose port
EXPOSE 8080

# Run server with gunicorn via python -m to avoid PATH issues
CMD ["python", "-m", "gunicorn", "-w", "1", "-k", "gthread", "--threads", "4", "--timeout", "300", "-b", "0.0.0.0:8080", "server:app"]


