from __future__ import annotations

import json
import logging
import os
import re
import tempfile
import threading
import time
from pathlib import Path
from typing import Protocol

from pydantic import ValidationError

from .models import EvaluationRun, EvaluationRunSummary

logger = logging.getLogger(__name__)

_RUN_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")


class EvaluationRunStore(Protocol):
    def save(self, run: EvaluationRun) -> None: ...

    def get(self, run_id: str) -> EvaluationRun | None: ...

    def list(self, limit: int) -> list[EvaluationRunSummary]: ...


class InMemoryEvaluationRunStore:
    """Non-persistent store used by unit tests and as an explicit opt-out."""

    def __init__(self) -> None:
        self._runs: dict[str, EvaluationRun] = {}
        self._lock = threading.Lock()

    def save(self, run: EvaluationRun) -> None:
        with self._lock:
            self._runs[run.runId] = run.model_copy(deep=True)

    def get(self, run_id: str) -> EvaluationRun | None:
        with self._lock:
            run = self._runs.get(run_id)
            return run.model_copy(deep=True) if run else None

    def list(self, limit: int) -> list[EvaluationRunSummary]:
        with self._lock:
            runs = [run.model_copy(deep=True) for run in self._runs.values()]
        return _summaries(runs, limit)


class FileEvaluationRunStore:
    """One JSON document per evaluation run, written with atomic replace.

    The store is shared by every user of the service (no per-user isolation).
    Directory layout is intentionally flat so operators can inspect/backup it:
    ``<store_dir>/<runId>.json``.
    """

    def __init__(self, directory: str | os.PathLike[str]) -> None:
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def save(self, run: EvaluationRun) -> None:
        path = self._path(run.runId)
        payload = run.model_dump(mode="json")
        with self._lock:
            descriptor, temporary = tempfile.mkstemp(
                prefix=f".{path.stem}-", suffix=".tmp", dir=self.directory
            )
            try:
                with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                    json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
                    handle.flush()
                    os.fsync(handle.fileno())
                _replace_with_retry(temporary, path)
            finally:
                if os.path.exists(temporary):
                    try:
                        os.unlink(temporary)
                    except OSError:
                        logger.warning("failed to remove temporary evaluation file %s", temporary)

    def get(self, run_id: str) -> EvaluationRun | None:
        try:
            path = self._path(run_id)
        except ValueError:
            return None
        return self._load(path)

    def list(self, limit: int) -> list[EvaluationRunSummary]:
        if limit <= 0:
            return []
        runs: list[EvaluationRun] = []
        for path in self.directory.glob("*.json"):
            run = self._load(path)
            if run is not None:
                runs.append(run)
        return _summaries(runs, limit)

    def _path(self, run_id: str) -> Path:
        if not isinstance(run_id, str) or _RUN_ID_PATTERN.fullmatch(run_id) is None:
            raise ValueError("invalid evaluation run id")
        return self.directory / f"{run_id}.json"

    def _load(self, path: Path) -> EvaluationRun | None:
        try:
            with path.open("r", encoding="utf-8") as handle:
                payload = json.load(handle)
            return EvaluationRun.model_validate(payload)
        except (OSError, json.JSONDecodeError, ValidationError) as error:
            logger.warning("skipping unreadable evaluation run file %s: %s", path, error)
            return None


def _replace_with_retry(source: str, target: Path, attempts: int = 50, delay: float = 0.01) -> None:
    """Replace atomically, tolerating Windows readers holding the target open.

    ``os.replace`` is atomic on POSIX and Windows, but Windows refuses the
    replace while another handle has the destination open without
    ``FILE_SHARE_DELETE``. Polling readers only hold it briefly, so retry.
    """
    for attempt in range(attempts):
        try:
            os.replace(source, target)
            return
        except PermissionError:
            if attempt == attempts - 1:
                raise
            time.sleep(delay)


def _summaries(runs: list[EvaluationRun], limit: int) -> list[EvaluationRunSummary]:
    ordered = sorted(runs, key=lambda run: (run.createdAt, run.runId), reverse=True)
    return [
        EvaluationRunSummary(
            runId=run.runId,
            datasetId=run.datasetId,
            profileId=run.profileId,
            status=run.status,
            createdAt=run.createdAt,
            metrics=run.metrics,
            error=run.error,
        )
        for run in ordered[:limit]
    ]
