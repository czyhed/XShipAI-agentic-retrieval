import json
import os
import shutil
import subprocess
from pathlib import Path
from urllib.request import urlopen


TS = """// Generated from FastAPI OpenAPI; transport and stable JSON only.
import type { components } from './openapi';
export type ProfileId = components['schemas']['RetrieveRequest']['profileId'];
export type RetrieveRequest = components['schemas']['RetrieveRequest'];
export type AnswerRequest = components['schemas']['AnswerRequest'];
export type EvaluationRequest = components['schemas']['EvaluationRequest'];
export type Provenance = components['schemas']['Provenance'];
export type Candidate = components['schemas']['Candidate'];
export type RetrieveResponse = components['schemas']['RetrieveResponse'];
export type Citation = components['schemas']['Citation'];
export type AnswerResponse = components['schemas']['AnswerResponse'];
export type EvaluationDataset = { id: string; name: string; queryCount: number };
export type EvaluationRun = components['schemas']['EvaluationRun'];
export type EvaluationResponse = EvaluationRun;
export type EvaluationRunSummary = { runId: string; datasetId: string; profileId: ProfileId; status: EvaluationRun['status']; createdAt: string; metrics: Record<string, unknown>; error?: string | null };
export type EvaluationRunsResponse = { runs: EvaluationRunSummary[] };
export type DataStatus = 'ready' | 'empty_corpus' | 'schema_missing' | 'unreachable';
export type HealthResponse = { ok: boolean; service: string; backend: string; available: boolean; connectionAvailable?: boolean; schemaReady?: boolean; corpusCount?: number; corpusCountCapped?: boolean; dataStatus?: DataStatus; indexVersion?: string; reason?: string };
export type ProfilesResponse = { profiles: Record<string, unknown>[] };
export type DatasetsResponse = { datasets: EvaluationDataset[] };

export class AgenticRetrievalClient {
  constructor(private readonly baseUrl: string) {}
  private async request<T>(path: string, body?: unknown): Promise<T> {
    const response = await fetch(`${this.baseUrl.replace(/\\/$/, '')}${path}`, { method: body ? 'POST' : 'GET', headers: body ? { 'content-type': 'application/json' } : undefined, body: body ? JSON.stringify(body) : undefined });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    return response.json() as Promise<T>;
  }
  retrieve(request: RetrieveRequest): Promise<RetrieveResponse> { return this.request('/v1/retrieve', request); }
  answer(request: AnswerRequest): Promise<AnswerResponse> { return this.request('/v1/answers', request); }
  evaluationDatasets(): Promise<DatasetsResponse> { return this.request('/v1/evaluation-datasets'); }
  evaluationRun(request: EvaluationRequest): Promise<EvaluationResponse> { return this.request('/v1/evaluations/runs', request); }
  evaluationRuns(limit = 50): Promise<EvaluationRunsResponse> { return this.request(`/v1/evaluations/runs?limit=${encodeURIComponent(limit)}`); }
  evaluationStatus(runId: string): Promise<EvaluationResponse> { return this.request(`/v1/evaluations/runs/${encodeURIComponent(runId)}`); }
  profiles(): Promise<ProfilesResponse> { return this.request('/v1/profiles'); }
  health(): Promise<HealthResponse> { return this.request('/healthz'); }
}
"""

JS = """// Generated runtime companion for the TypeScript SDK.
export class AgenticRetrievalClient {
  constructor(baseUrl) { this.baseUrl = baseUrl; }
  async request(path, body) {
    const response = await fetch(`${this.baseUrl.replace(/\\/$/, '')}${path}`, { method: body ? 'POST' : 'GET', headers: body ? {'content-type': 'application/json'} : undefined, body: body ? JSON.stringify(body) : undefined });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    return response.json();
  }
  retrieve(request) { return this.request('/v1/retrieve', request); }
  answer(request) { return this.request('/v1/answers', request); }
  evaluationDatasets() { return this.request('/v1/evaluation-datasets'); }
  evaluationRun(request) { return this.request('/v1/evaluations/runs', request); }
  evaluationRuns(limit = 50) { return this.request(`/v1/evaluations/runs?limit=${encodeURIComponent(limit)}`); }
  evaluationStatus(runId) { return this.request(`/v1/evaluations/runs/${encodeURIComponent(runId)}`); }
  profiles() { return this.request('/v1/profiles'); }
  health() { return this.request('/healthz'); }
}
"""


def main() -> None:
    base_url = os.getenv("AGENTIC_RETRIEVAL_URL", "http://127.0.0.1:8090")
    with urlopen(base_url.rstrip("/") + "/openapi.json", timeout=5) as response:
        json.loads(response.read())
    Path("sdk").mkdir(exist_ok=True)
    npx = shutil.which("npx")
    if npx:
        subprocess.run([npx, "--yes", "openapi-typescript@7.8.0", base_url.rstrip("/") + "/openapi.json", "-o", "sdk/openapi.d.ts"], check=True)
    Path("sdk/index.ts").write_text(TS, encoding="utf-8")
    Path("sdk/index.js").write_text(JS, encoding="utf-8")


if __name__ == "__main__":
    main()
