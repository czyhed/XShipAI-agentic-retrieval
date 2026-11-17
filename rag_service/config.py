from dataclasses import dataclass
import os
from pathlib import Path
from dotenv import load_dotenv

# Load local defaults without overriding explicit process-level configuration.
load_dotenv(override=False)


def _env(name: str, fallback: str | None = None, default: str | None = None) -> str | None:
    value = os.getenv(name)
    if value:
        return value
    if fallback:
        value = os.getenv(fallback)
        if value:
            return value
    return default


@dataclass(frozen=True)
class Settings:
    retrieval_provider: str = os.getenv("RAG_RETRIEVAL_PROVIDER", "pgvector")
    pg_embedding_profile_id: str = os.getenv("RAG_PG_EMBEDDING_PROFILE_ID", "qwen3.7-text-embedding")
    pg_connect_timeout_seconds: int = int(os.getenv("RAG_PG_CONNECT_TIMEOUT_SECONDS", "5"))
    embedding_model_version: str = os.getenv("RAG_EMBEDDING_MODEL_VERSION", "deterministic-embedding-v1")
    rewrite_model_version: str = os.getenv("RAG_REWRITE_MODEL_VERSION", "deterministic-rewrite-v1")
    rewrite_base_url: str | None = os.getenv("RAG_REWRITE_BASE_URL")
    rewrite_api_key: str | None = os.getenv("RAG_REWRITE_API_KEY")
    rewrite_model: str = os.getenv("RAG_REWRITE_MODEL", "deterministic-rewrite-v1")
    rewrite_timeout_ms: int = int(os.getenv("RAG_REWRITE_TIMEOUT_MS", "10000"))
    embedding_provider: str = os.getenv("RAG_EMBEDDING_PROVIDER", "fake")
    embedding_base_url: str | None = _env("RAG_EMBEDDING_BASE_URL", "EMBEDDING_BASE_URL")
    embedding_api_key: str | None = _env("RAG_EMBEDDING_API_KEY", "EMBEDDING_API_KEY")
    embedding_model: str = _env("RAG_EMBEDDING_MODEL", "EMBEDDING_MODEL", "deterministic-embedding-v1") or "deterministic-embedding-v1"
    embedding_dimensions: int = int(os.getenv("RAG_EMBEDDING_DIMENSIONS", "1024"))
    embedding_timeout_ms: int = int(os.getenv("RAG_EMBEDDING_TIMEOUT_MS", "10000"))
    answer_base_url: str | None = _env("RAG_ANSWER_BASE_URL", "CONTEXT_BASE_URL")
    answer_api_key: str | None = _env("RAG_ANSWER_API_KEY", "CONTEXT_API_KEY")
    answer_provider: str = os.getenv("RAG_ANSWER_PROVIDER", "disabled")
    answer_model: str = _env("RAG_ANSWER_MODEL", "CONTEXT_MODEL", "deterministic-answer-v1") or "deterministic-answer-v1"
    answer_model_version: str = os.getenv("RAG_ANSWER_MODEL_VERSION", "deterministic-answer-v1")
    answer_timeout_ms: int = int(os.getenv("RAG_ANSWER_TIMEOUT_MS", "30000"))
    rerank_url: str | None = os.getenv("RAG_RERANK_API_URL")
    rerank_api_key: str | None = os.getenv("RAG_RERANK_API_KEY")
    rerank_model_version: str = os.getenv("RAG_RERANK_MODEL_VERSION", "external-rerank-v1")
    rerank_timeout_ms: int = int(os.getenv("RAG_RERANK_TIMEOUT_MS", "3000"))
    ragas_url: str | None = os.getenv("RAGAS_API_URL")
    ragas_api_key: str | None = os.getenv("RAGAS_API_KEY")
    ragas_timeout_ms: int = int(os.getenv("RAGAS_TIMEOUT_MS", "10000"))
    langfuse_host: str | None = os.getenv("LANGFUSE_HOST")
    langfuse_public_key: str | None = os.getenv("LANGFUSE_PUBLIC_KEY")
    langfuse_secret_key: str | None = os.getenv("LANGFUSE_SECRET_KEY")
    langfuse_timeout_ms: int = int(os.getenv("LANGFUSE_TIMEOUT_MS", "5000"))
    judge_url: str | None = os.getenv("RAG_JUDGE_API_URL")
    judge_api_key: str | None = os.getenv("RAG_JUDGE_API_KEY")
    judge_model_version: str = os.getenv("RAG_JUDGE_MODEL_VERSION", "judge-v1")
    judge_timeout_ms: int = int(os.getenv("RAG_JUDGE_TIMEOUT_MS", "10000"))
    minio_endpoint: str | None = _env("RAG_MINIO_ENDPOINT", "MINIO_ENDPOINT")
    minio_bucket: str | None = _env("RAG_MINIO_BUCKET", "MINIO_BUCKET")
    minio_access_key: str | None = _env("RAG_MINIO_ACCESS_KEY", "MINIO_ACCESS_KEY")
    minio_secret_key: str | None = _env("RAG_MINIO_SECRET_KEY", "MINIO_SECRET_KEY")
    minio_region: str = _env("RAG_MINIO_REGION", "MINIO_REGION", "us-east-1") or "us-east-1"
    minio_secure: bool = os.getenv("RAG_MINIO_SECURE", "false").lower() == "true"
    minio_prefix: str = os.getenv("RAG_MINIO_PREFIX", "agentic-retrieval/")
    pg_host: str | None = _env("RAG_PG_HOST", "PG_HOST")
    pg_port: int = int(_env("RAG_PG_PORT", "PG_PORT", "5432") or "5432")
    pg_database: str = _env("RAG_PG_DATABASE", None, "rag_database") or "rag_database"
    pg_user: str | None = _env("RAG_PG_USER", "PG_USER")
    pg_password: str | None = _env("RAG_PG_PASSWORD", "PG_PASSWORD")
    pg_sslmode: str = os.getenv("RAG_PG_SSLMODE", "prefer")
    pg_schema: str = os.getenv("RAG_PG_SCHEMA", "rag")
    pg_vector_table: str = os.getenv("RAG_PG_VECTOR_TABLE", "vector_records_v2_d1024")
    pg_bm25_index: str = os.getenv("RAG_PG_BM25_INDEX", "vector_records_v2_d1024_bm25_idx")
    pg_bm25_key_field: str = os.getenv("RAG_PG_BM25_KEY_FIELD", "retrieval_key")
    rag_eval_store_dir: str = os.getenv("RAG_EVAL_STORE_DIR") or str(
        Path(__file__).resolve().parent.parent / "data" / "evaluation-runs"
    )


settings = Settings()
