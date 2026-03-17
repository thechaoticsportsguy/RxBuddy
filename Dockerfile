# Use Python 3.11 slim image for smaller size and faster builds
FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Copy requirements first (Docker layer caching - only reinstalls if requirements change)
COPY requirements-railway.txt .

# Install dependencies
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements-railway.txt

# Copy the rest of the application code
COPY . .

# Expose port (Railway sets PORT env variable)
EXPOSE 8000

# Run the application
# Using shell form to allow $PORT variable expansion
CMD uvicorn backend.main:app --host 0.0.0.0 --port ${PORT:-8000}
