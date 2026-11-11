// Generated runtime companion for the TypeScript SDK.
export class AgenticRetrievalClient {
  constructor(baseUrl) { this.baseUrl = baseUrl; }
  async request(path, body) {
    const response = await fetch(`${this.baseUrl.replace(/\/$/, '')}${path}`, { method: body ? 'POST' : 'GET', headers: body ? {'content-type': 'application/json'} : undefined, body: body ? JSON.stringify(body) : undefined });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    return response.json();
  }
  retrieve(request) { return this.request('/v1/retrieve', request); }
  answer(request) { return this.request('/v1/answers', request); }
  evaluationDatasets() { return this.request('/v1/evaluation-datasets'); }
  evaluationRun(request) { return this.request('/v1/evaluations/runs', request); }
  evaluationStatus(runId) { return this.request(`/v1/evaluations/runs/${encodeURIComponent(runId)}`); }
  profiles() { return this.request('/v1/profiles'); }
  health() { return this.request('/healthz'); }
}
