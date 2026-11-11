// Generated from FastAPI OpenAPI; transport and stable JSON only.
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
export type HealthResponse = { ok: boolean; service: string; backend: string; available: boolean; indexVersion?: string; reason?: string };
export type ProfilesResponse = { profiles: Record<string, unknown>[] };
export type DatasetsResponse = { datasets: EvaluationDataset[] };

export class AgenticRetrievalClient {
  constructor(private readonly baseUrl: string) {}
  private async request<T>(path: string, body?: unknown): Promise<T> {
    const response = await fetch(`${this.baseUrl.replace(/\/$/, '')}${path}`, { method: body ? 'POST' : 'GET', headers: body ? { 'content-type': 'application/json' } : undefined, body: body ? JSON.stringify(body) : undefined });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    return response.json() as Promise<T>;
  }
  retrieve(request: RetrieveRequest): Promise<RetrieveResponse> { return this.request('/v1/retrieve', request); }
  answer(request: AnswerRequest): Promise<AnswerResponse> { return this.request('/v1/answers', request); }
  evaluationDatasets(): Promise<DatasetsResponse> { return this.request('/v1/evaluation-datasets'); }
  evaluationRun(request: EvaluationRequest): Promise<EvaluationResponse> { return this.request('/v1/evaluations/runs', request); }
  evaluationStatus(runId: string): Promise<EvaluationResponse> { return this.request(`/v1/evaluations/runs/${encodeURIComponent(runId)}`); }
  profiles(): Promise<ProfilesResponse> { return this.request('/v1/profiles'); }
  health(): Promise<HealthResponse> { return this.request('/healthz'); }
}
