from __future__ import annotations

import json
import re
from typing import Any, Protocol

from psycopg import connect
from psycopg.rows import dict_row

from .models import IndexRecord, Provenance


class SchemaMissingError(RuntimeError):
    """The configured PGVector schema, table, or column is not initialized."""


_SCHEMA_ERROR_SQLSTATES = {"42P01", "3F000", "42703"}


class RetrievalBackend(Protocol):
    kind: str
    version: str

    def dense_search(self, query_vector: list[float], limit: int) -> list[tuple[IndexRecord, float]]: ...

    def lexical_search(self, query: str, limit: int) -> list[tuple[IndexRecord, float]]: ...

    def health(self) -> dict[str, Any]: ...


def _identifier(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
        raise ValueError("PG schema/table must be a simple identifier")
    return value


def _provenance(row: dict[str, Any]) -> Provenance:
    segments = row.get("source_segments") or []
    if isinstance(segments, str):
        try:
            segments = json.loads(segments)
        except json.JSONDecodeError:
            segments = []
    if not isinstance(segments, list):
        segments = []
    source_ids: list[str] = []
    page: int | None = None
    for segment in segments:
        if not isinstance(segment, dict):
            continue
        source_id = segment.get("contentBlockId") or segment.get("sourceBlockId") or segment.get("blockId")
        if source_id and source_id not in source_ids:
            source_ids.append(str(source_id))
        if page is None and isinstance(segment.get("page"), int):
            page = segment["page"]
    version = row.get("source_document_version")
    return Provenance(documentId=row.get("document_id"), documentVersion=version, sourceBlockIds=source_ids, page=page)


def _record(row: dict[str, Any]) -> IndexRecord:
    payload = row.get("content_payload") or {}
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            payload = {}
    payload = payload if isinstance(payload, dict) else {}
    chunk_type = row.get("chunk_type") or payload.get("contentType") or "text"
    if chunk_type not in {"text", "image", "table"}:
        chunk_type = "text"
    asset_uri = row.get("image_object_uri")
    table_ref = payload.get("tableVersionId") or payload.get("tableRef")
    parent = payload.get("parentChunk")
    parent = parent if isinstance(parent, dict) and isinstance(parent.get("text"), str) and parent["text"].strip() else None
    provenance_row = {**row, "source_segments": parent["sourceSegments"]} if parent and isinstance(parent.get("sourceSegments"), list) else row
    return IndexRecord(
        chunkId=str(row["chunk_id"]),
        contentType=chunk_type,
        text=parent["text"] if parent else str(row.get("final_text") or ""),
        parentChunkId=str(parent["id"]) if parent and parent.get("id") else None,
        matchedText=str(row.get("final_text") or "") if parent else None,
        provenance=_provenance(provenance_row),
        assetRefs=[str(asset_uri)] if asset_uri else [],
        tableRef=str(table_ref) if table_ref else None,
    )


class PgVectorAdapter:
    kind = "pgvector"

    def __init__(self, settings: Any):
        self.settings = settings
        self.schema = _identifier(settings.pg_schema)
        self.table = _identifier(settings.pg_vector_table)
        table_match = re.fullmatch(r"vector_records_v2_d(\d+)", self.table)
        if table_match and int(table_match.group(1)) != settings.embedding_dimensions:
            raise ValueError("PGVector table dimension does not match embedding dimensions")
        self.version = f"pgvector:{self.schema}.{self.table}:{settings.pg_embedding_profile_id}"
        self._bm25_ready: bool | None = None

    def _connect(self):
        if not all([self.settings.pg_host, self.settings.pg_user, self.settings.pg_password]):
            raise RuntimeError("PGVector credentials are not configured")
        return connect(host=self.settings.pg_host, port=self.settings.pg_port, dbname=self.settings.pg_database, user=self.settings.pg_user, password=self.settings.pg_password, sslmode=self.settings.pg_sslmode, connect_timeout=self.settings.pg_connect_timeout_seconds, row_factory=dict_row)

    def _query(self, sql: str, params: tuple[Any, ...]) -> list[dict[str, Any]]:
        try:
            with self._connect() as connection:
                with connection.cursor() as cursor:
                    cursor.execute("SET TRANSACTION READ ONLY")
                    cursor.execute(sql, params)
                    return list(cursor.fetchall())
        except Exception as error:
            if getattr(error, "sqlstate", None) in _SCHEMA_ERROR_SQLSTATES:
                raise SchemaMissingError("PGVector schema is not initialized") from error
            raise RuntimeError("PGVector backend unavailable") from error

    def _scope(self) -> tuple[str, list[Any]]:
        filters = ["r.deleted_at IS NULL", "r.embedding_profile_id = %s", "r.vector_dimensions = %s", "r.schema_version = '2.0'", "d.deleted_at IS NULL"]
        params: list[Any] = [self.settings.pg_embedding_profile_id, self.settings.embedding_dimensions]
        return " AND ".join(filters), params

    def dense_search(self, query_vector: list[float], limit: int) -> list[tuple[IndexRecord, float]]:
        vector = "[" + ",".join(str(float(value)) for value in query_vector) + "]"
        qualified = f'"{self.schema}"."{self.table}"'
        where, scope_params = self._scope()
        sql = f"""SELECT r.target_id, r.document_id, r.chunk_id, r.chunk_type, r.final_text,
            r.source_document_version, r.source_segments, r.image_object_uri, r.content_payload,
            1 - (r.embedding <=> %s::vector) AS score
            FROM {qualified} r
            JOIN "{self.schema}"."documents" d ON d.target_id = r.target_id AND d.document_id = r.document_id
            WHERE {where}
            ORDER BY r.embedding <=> %s::vector, r.target_id, r.document_id, r.chunk_id
            LIMIT %s"""
        rows = self._query(sql, (vector, *scope_params, vector, limit))
        return [(_record(row), float(row["score"])) for row in rows]

    def lexical_search(self, query: str, limit: int) -> list[tuple[IndexRecord, float]]:
        qualified = f'"{self.schema}"."{self.table}"'
        key_field = _identifier(self.settings.pg_bm25_key_field)
        index_name = _identifier(self.settings.pg_bm25_index)
        where, scope_params = self._scope()
        sql = f"""SELECT r.target_id, r.document_id, r.chunk_id, r.chunk_type, r.final_text,
            r.source_document_version, r.source_segments, r.image_object_uri, r.content_payload,
            paradedb.score(r.{key_field}) AS score
            FROM {qualified} r
            JOIN "{self.schema}"."documents" d ON d.target_id = r.target_id AND d.document_id = r.document_id
            WHERE {where} AND r.final_text ||| %s
            ORDER BY paradedb.score(r.{key_field}) DESC, r.target_id, r.document_id, r.chunk_id
            LIMIT %s"""
        try:
            self._ensure_bm25_ready()
            rows = self._query(sql, (*scope_params, query, limit))
        except SchemaMissingError:
            raise
        except RuntimeError as error:
            raise RuntimeError(f"PGVector BM25 index unavailable: {index_name}") from error
        return [(_record(row), float(row.get("score", 0.0))) for row in rows]

    def _ensure_bm25_ready(self) -> None:
        if self._bm25_ready is True:
            return
        qualified = f'"{self.schema}"."{self.table}"'
        key_field = _identifier(self.settings.pg_bm25_key_field)
        index_name = _identifier(self.settings.pg_bm25_index)
        try:
            rows = self._query(
                f"""SELECT
                    to_regclass(%s) IS NOT NULL AS index_exists,
                    count(*) = count(DISTINCT {key_field}) AS key_unique
                    FROM {qualified}
                    WHERE deleted_at IS NULL""",
                (f"{self.schema}.{index_name}",),
            )
        except SchemaMissingError:
            raise
        except RuntimeError as error:
            raise RuntimeError(f"PGVector BM25 index unavailable: {index_name}") from error
        state = rows[0] if rows else {}
        if not state.get("index_exists"):
            raise RuntimeError(f"PGVector BM25 index unavailable: {index_name}")
        if not state.get("key_unique"):
            raise RuntimeError(f"PGVector BM25 key field is not unique: {key_field}")
        self._bm25_ready = True

    def _schema_ready(self) -> bool:
        required = ("target_id", "target_spu_id", "deleted_at", "embedding_profile_id", "vector_dimensions", "embedding", "source_segments", "source_document_version")
        placeholders = ",".join("%s" for _ in required)
        rows = self._query(
            f"""SELECT
                to_regclass(%s) IS NOT NULL AS table_exists,
                to_regclass(%s) IS NOT NULL AS documents_exists,
                (SELECT count(*) FROM information_schema.columns WHERE table_schema=%s AND table_name=%s AND column_name IN ({placeholders})) = %s AS columns_ok,
                EXISTS (SELECT 1 FROM pg_indexes WHERE schemaname=%s AND tablename=%s AND position('hnsw' in indexdef) > 0 AND position('embedding' in indexdef) > 0) AS hnsw_ok""",
            (f"{self.schema}.{self.table}", f"{self.schema}.documents", self.schema, self.table, *required, len(required), self.schema, self.table),
        )
        state = rows[0] if rows else {}
        return all(state.get(key) for key in ("table_exists", "documents_exists", "columns_ok", "hnsw_ok"))

    def _corpus_count(self, cap: int = 1000) -> tuple[int, bool]:
        where, params = self._scope()
        qualified = f'"{self.schema}"."{self.table}"'
        rows = self._query(
            f"""SELECT count(*) AS count FROM (
                SELECT 1 FROM {qualified} r
                JOIN "{self.schema}"."documents" d ON d.target_id = r.target_id AND d.document_id = r.document_id
                WHERE {where}
                LIMIT %s
            ) capped""",
            (*params, cap + 1),
        )
        count = int(rows[0]["count"]) if rows else 0
        return (min(count, cap), count > cap)

    def health(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"backend": self.kind, "indexVersion": self.version}
        try:
            schema_ready = self._schema_ready()
        except Exception as error:
            cause = error.__cause__
            cause_text = str(cause or error).lower()
            auth_failure = getattr(cause, "sqlstate", None) == "28P01" or "password authentication failed" in cause_text or "authentication failed" in cause_text
            reason = "credentials_invalid" if auth_failure else "connection_failed"
            return {**payload, "available": False, "connectionAvailable": False, "schemaReady": False, "corpusCount": 0, "corpusCountCapped": False, "dataStatus": "unreachable", "reason": reason}
        if not schema_ready:
            return {**payload, "available": False, "connectionAvailable": True, "schemaReady": False, "corpusCount": 0, "corpusCountCapped": False, "dataStatus": "schema_missing", "reason": "schema_mismatch"}
        try:
            corpus_count, capped = self._corpus_count()
        except SchemaMissingError:
            return {**payload, "available": False, "connectionAvailable": True, "schemaReady": False, "corpusCount": 0, "corpusCountCapped": False, "dataStatus": "schema_missing", "reason": "schema_mismatch"}
        except Exception:
            corpus_count, capped = 0, False
        return {**payload, "available": True, "connectionAvailable": True, "schemaReady": True, "corpusCount": corpus_count, "corpusCountCapped": capped, "dataStatus": "ready" if corpus_count else "empty_corpus"}
