# Container image for SSA MCP Server; build the generated database into the image.
FROM python:3.12-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1     PYTHONDONTWRITEBYTECODE=1     PORT=8080     HOST=0.0.0.0     MCP_TRANSPORT=http

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/
COPY data/ ./data/
COPY scripts/ ./scripts/
COPY main.py .
COPY pyproject.toml .
RUN python scripts/baby_name_ingest.py

EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3     CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health')" || exit 1

CMD ["python", "main.py", "--transport", "http", "--port", "8080", "--host", "0.0.0.0"]
