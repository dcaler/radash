FROM python:3.11-slim

WORKDIR /app

# Copy requirements and install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY app/ ./app/

# Create directory for SQLite database + emitted output (briefs, fill lists).
# This is the only writable location; every source is mounted read-only.
RUN mkdir -p /app/data/output

# Expose port 8261 (trundlr is 8251)
EXPOSE 8261

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8261"]
