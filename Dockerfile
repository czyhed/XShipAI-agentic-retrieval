FROM python:3.12-slim

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

COPY pyproject.toml uv.lock ./
RUN pip install --no-cache-dir uv==0.12.3 \
    && uv sync --frozen --no-dev

COPY rag_service ./rag_service
COPY web ./web
COPY sdk ./sdk

EXPOSE 8090
CMD ["uvicorn", "rag_service.main:app", "--host", "0.0.0.0", "--port", "8090"]
