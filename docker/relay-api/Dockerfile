# GitHub Relay - Relay API Dockerfile
FROM python:3.12-slim

# Set working directory
WORKDIR /app

# Install dependencies
COPY relay-api/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY relay-api/src/ ./src/

# Create non-root user for security
RUN useradd -m -u 1000 appuser
RUN chown -R appuser:appuser /app
USER appuser

# Expose port
EXPOSE 8000

# Run the application
CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]
