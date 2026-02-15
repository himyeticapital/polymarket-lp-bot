FROM python:3.12-slim

WORKDIR /app

# Install dependencies first (cached layer)
COPY pyproject.toml .
RUN pip install --no-cache-dir .

# Copy application code
COPY bot/ bot/
COPY scripts/ scripts/

EXPOSE 8080

CMD ["python", "-m", "bot"]
