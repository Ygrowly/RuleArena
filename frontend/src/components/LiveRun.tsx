import { useCallback, useEffect, useRef, useState } from "react";
import {
  ApiError,
  cancelRun,
  compileRule,
  confirmPolicy,
  createRun,
  getCounterexamples,
  getRun,
  getTrace,
  listTemplates,
} from "../api/client";
import type {
  AttackRun,
  CompileResultDto,
  CounterexampleRecord,
  TemplateDto,
  TraceRecord,
} from "../api/types";
import { describeStatus, STRATEGY_META } from "../domain/outcome";
import { OutcomeBanner } from "./Evidence";
import { StrategyTrace } from "./TechnicalTrace";

const DEFAULT_BUDGET = {
  max_steps: 12,
  max_tokens: 12000,
  max_cost: 1.5,
  max_time_seconds: 90,
};

type Phase = "pick" | "compiled" | "ambiguous" | "running" | "done";

export function LiveRunView() {
  const [templates, setTemplates] = useState<TemplateDto[]>([]);
  const [templateId, setTemplateId] = useState<string>("");
  const [modification, setModification] = useState("");
  const [phase, setPhase] = useState<Phase>("pick");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [compiled, setCompiled] = useState<CompileResultDto | null>(null);
  const [run, setRun] = useState<AttackRun | null>(null);
  const [versionId, setVersionId] = useState<string>("");
  const [traces, setTraces] = useState<TraceRecord[]>([]);
  const [counterexamples, setCounterexamples] = useState<CounterexampleRecord[]>([]);
  const idempotencyKey = useRef<string>("");

  useEffect(() => {
    listTemplates()
      .then((items) => {
        setTemplates(items);
        setTemplateId(items[0]?.id ?? "");
      })
      .catch((cause: unknown) => setError(String(cause)));
  }, []);

  const poll = useCallback(async (runId: string) => {
    const [currentRun, trace, cex] = await Promise.all([
      getRun(runId),
      getTrace(runId).catch(() => ({ trace: [] as TraceRecord[], leakage_blocked: 0 })),
      getCounterexamples(runId).catch(() => []),
    ]);
    setRun(currentRun);
    setTraces(trace.trace);
    setCounterexamples(cex);
    const state = describeStatus(currentRun.status, currentRun.outcome);
    if (!state.busy) {
      setPhase("done");
      return true;
    }
    return false;
  }, []);

  // Authoritative polling loop; SSE events are a live projection on top.
  useEffect(() => {
    if (run === null || phase !== "running") return;
    let cancelled = false;
    const timer = setInterval(() => {
      poll(run.run_id)
        .then((terminal) => {
          if (terminal && !cancelled) setPhase("done");
        })
        .catch(() => undefined);
    }, 2000);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [run, phase, poll]);

  async function handleCompile() {
    setBusy(true);
    setError(null);
    try {
      const result = await compileRule(templateId, modification);
      setCompiled(result);
      if (result.status === "COMPILED") setPhase("compiled");
      else if (result.status === "NEEDS_CONFIRMATION") setPhase("ambiguous");
      else setPhase("pick");
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : String(cause));
    } finally {
      setBusy(false);
    }
  }

  async function handleConfirm() {
    if (compiled === null) return;
    setBusy(true);
    setError(null);
    try {
      const version = await confirmPolicy(compiled.policy_id);
      setVersionId(version.version_id);
      setPhase("compiled");
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : String(cause));
    } finally {
      setBusy(false);
    }
  }

  async function handleStart() {
    if (compiled === null || compiled.rule_spec === null) return;
    if (versionId === "") return;
    // Guard against double clicks: one stable idempotency key per attempt.
    if (idempotencyKey.current === "") {
      idempotencyKey.current = crypto.randomUUID();
    }
    setBusy(true);
    setError(null);
    try {
      const created = await createRun(
        {
          rule_version_id: versionId,
          scenario_version_id: `${compiled.template_id}-v1`,
          sandbox_version: "vulnerable",
          oracle_version: "1.0",
          budget: DEFAULT_BUDGET,
          random_seed: 20260905,
        },
        idempotencyKey.current,
      );
      setRun(created);
      setPhase("running");
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : String(cause));
    } finally {
      setBusy(false);
    }
  }

  async function handleCancel() {
    if (run === null) return;
    setBusy(true);
    try {
      await cancelRun(run.run_id);
      await poll(run.run_id);
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : String(cause));
    } finally {
      setBusy(false);
    }
  }

  const state = run ? describeStatus(run.status, run.outcome) : null;
  const perStrategy = summarizeStrategyProgress(traces);

  return (
    <div>
      <section className="panel" aria-label="选择规则">
        <h2>1 · 选择规则模板</h2>
        <label htmlFor="template">业务模板</label>{" "}
        <select
          id="template"
          value={templateId}
          onChange={(event) => setTemplateId(event.target.value)}
        >
          {templates.map((template) => (
            <option key={template.id} value={template.id}>
              {template.label}
            </option>
          ))}
        </select>
        <p className="muted">
          {templates.find((template) => template.id === templateId)?.description}
        </p>
        <label htmlFor="modification">自然语言规则修改</label>
        <textarea
          id="modification"
          value={modification}
          placeholder={
            templates.find((template) => template.id === templateId)?.example_modification
          }
          onChange={(event) => setModification(event.target.value)}
        />
        <button type="button" className="primary" disabled={busy} onClick={handleCompile}>
          编译规则
        </button>
      </section>

      {compiled && (
        <section className="panel" aria-label="确认规则">
          <h2>2 · 确认规则</h2>
          {compiled.errors.length > 0 && (
            <div className="banner danger">{compiled.errors.join("；")}</div>
          )}
          {phase === "ambiguous" && (
            <div className="banner warn">
              规则存在歧义，需要显式确认后才能创建可运行版本：
              <ul>
                {compiled.questions.map((question) => (
                  <li key={question.question_id}>{`${question.field_path}: ${question.question}`}</li>
                ))}
              </ul>
              <button type="button" className="primary" disabled={busy} onClick={handleConfirm}>
                我已确认以上歧义，冻结该版本
              </button>
            </div>
          )}
          {phase === "compiled" && (
            <div className="banner success">规则已确认并冻结（content hash 绑定）。</div>
          )}
          {compiled.rule_spec && <pre className="code">{JSON.stringify(compiled.rule_spec, null, 2)}</pre>}
        </section>
      )}

      {phase === "compiled" && (
        <section className="panel" aria-label="启动运行">
          <h2>3 · 启动 Arena 运行</h2>
          <p className="muted">
            {`预算：${DEFAULT_BUDGET.max_steps} 步 / ${DEFAULT_BUDGET.max_tokens} tokens / $${DEFAULT_BUDGET.max_cost} / ${DEFAULT_BUDGET.max_time_seconds}s · 在 vulnerable Profile 上搜索反例。`}
          </p>
          <button type="button" className="primary" disabled={busy} onClick={handleStart}>
            启动实时运行
          </button>
        </section>
      )}

      {(phase === "running" || phase === "done") && run && state && (
        <section className="panel" aria-label="Arena 运行">
          <h2>3 · Arena 运行</h2>
          <OutcomeBanner status={run.status} outcome={run.outcome} />
          <div className="strategy-grid">
            {Object.entries(STRATEGY_META).map(([strategy, meta]) => {
              const progress = perStrategy.get(strategy);
              return (
                <div
                  key={strategy}
                  className="strategy-card"
                  style={{ borderTopColor: meta.color, color: meta.color }}
                >
                  <strong style={{ color: "var(--ink)" }}>{meta.label}</strong>
                  <div className="muted">{meta.focus}</div>
                  {progress ? (
                    <>
                      <div className="muted">{`已执行动作 ${progress.actions} / 预算 ${DEFAULT_BUDGET.max_steps} 步`}</div>
                      <div className="meter" aria-hidden="true">
                        <div style={{ width: `${Math.min(100, (progress.actions / DEFAULT_BUDGET.max_steps) * 100)}%` }} />
                      </div>
                    </>
                  ) : (
                    <div className="muted">{state?.busy ? "等待调度…" : "—"}</div>
                  )}
                </div>
              );
            })}
          </div>
          {phase === "running" && (
            <button type="button" disabled={busy} onClick={handleCancel}>
              取消运行
            </button>
          )}
        </section>
      )}

      {error && (
        <p className="error-text" role="alert">
          {error}
        </p>
      )}

      {phase === "done" && counterexamples.length > 0 && (
        <section className="panel">
          <h2>4 · 查看证据</h2>
          <p className="muted">
            实时运行的证据以 Control API 返回的 Counterexample 与 Trace 为准；
            <button type="button" onClick={() => setPhase("done")}>
              刷新
            </button>
          </p>
        </section>
      )}

      {(phase === "done" || phase === "running") && traces.length > 0 && (
        <StrategyTrace records={traces} />
      )}
    </div>
  );
}

function summarizeStrategyProgress(records: TraceRecord[]): Map<string, { actions: number }> {
  const map = new Map<string, { actions: number }>();
  for (const record of records) {
    if (record.kind !== "SIMULATION" && record.kind !== "SANDBOX_HTTP") continue;
    const strategy = record.strategy_id ?? "VALUE_FLOW";
    const current = map.get(strategy) ?? { actions: 0 };
    current.actions += 1;
    map.set(strategy, current);
  }
  return map;
}
