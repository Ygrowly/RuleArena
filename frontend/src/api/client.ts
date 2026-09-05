import type {
  AttackRun,
  CompileResultDto,
  CounterexampleRecord,
  TemplateDto,
  TraceRecord,
} from "./types";

export class ApiError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function parseDetail(response: Response): Promise<string> {
  try {
    const body = (await response.json()) as { detail?: unknown };
    if (typeof body.detail === "string") return body.detail;
    if (Array.isArray(body.detail)) return JSON.stringify(body.detail);
    if (body.detail && typeof body.detail === "object") {
      const detail = body.detail as { message?: unknown; code?: unknown };
      if (typeof detail.message === "string") return detail.message;
      return JSON.stringify(body.detail);
    }
  } catch {
    // fall through to generic message
  }
  return `${response.status} ${response.statusText}`;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, init);
  if (!response.ok) {
    throw new ApiError(response.status, await parseDetail(response));
  }
  return (await response.json()) as T;
}

export async function listTemplates(): Promise<TemplateDto[]> {
  const body = await request<{ templates: TemplateDto[] }>("/api/templates");
  return body.templates;
}

export async function compileRule(
  templateId: string,
  chineseModification: string,
): Promise<CompileResultDto> {
  return request<CompileResultDto>("/api/policies/compile", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ template_id: templateId, chinese_modification: chineseModification }),
  });
}

export async function confirmPolicy(policyId: string): Promise<RuleVersionResponse> {
  return request<RuleVersionResponse>(`/api/policies/${policyId}/confirm`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({}),
  });
}

export interface RuleVersionResponse {
  version_id: string;
  policy_id: string;
  version: number;
  template_id: string;
  content_hash: string;
  prompt_version: string;
}

export async function createRun(
  payload: {
    rule_version_id: string;
    scenario_version_id: string;
    sandbox_version: string;
    oracle_version: string;
    budget: AttackRun["budget"];
    random_seed: number;
  },
  idempotencyKey: string,
): Promise<AttackRun> {
  return request<AttackRun>("/api/runs", {
    method: "POST",
    headers: { "Content-Type": "application/json", "Idempotency-Key": idempotencyKey },
    body: JSON.stringify(payload),
  });
}

export async function getRun(runId: string): Promise<AttackRun> {
  return request<AttackRun>(`/api/runs/${runId}`);
}

export async function cancelRun(runId: string): Promise<void> {
  await request(`/api/runs/${runId}/cancel`, { method: "POST" });
}

export async function getCounterexamples(runId: string): Promise<CounterexampleRecord[]> {
  const body = await request<{ counterexamples: CounterexampleRecord[] }>(
    `/api/runs/${runId}/counterexamples`,
  );
  return body.counterexamples;
}

export interface TraceResponse {
  trace: TraceRecord[];
  leakage_blocked: number;
}

export async function getTrace(runId: string): Promise<TraceResponse> {
  return request<TraceResponse>(`/api/runs/${runId}/trace`);
}
