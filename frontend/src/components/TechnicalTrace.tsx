import { useState } from "react";
import type { TraceRecord } from "../api/types";

const KIND_LABELS_ZH: Record<string, string> = {
  LLM_CALL: "模型调用",
  ACTION_PROPOSAL: "动作提议",
  SIMULATION: "模拟执行",
  SANDBOX_HTTP: "Sandbox HTTP",
  SNAPSHOT: "状态快照",
  ORACLE_CHECK: "Oracle 裁决",
};

function formatRecord(record: TraceRecord): string {
  const payload = {
    kind: record.kind,
    step: record.step_id,
    status: record.status,
    model_config_hash: record.model_config_hash ?? undefined,
    prompt_version: record.prompt_version ?? undefined,
    latency_ms: record.latency_ms,
    input_tokens: record.input_tokens,
    output_tokens: record.output_tokens,
    cost: record.cost,
    retry_count: record.retry_count,
    before: record.before_state_hash ?? undefined,
    after: record.after_state_hash ?? undefined,
    error: record.error_type ?? undefined,
    action: record.action_summary,
    tool_result: record.tool_result_summary,
  };
  return JSON.stringify(payload, null, 2);
}

/**
 * Technical trace: collapsed by default so a non-technical reader sees the story first.
 * Expanded, the model calls become a table -- what a technical interviewer actually asks
 * for (latency, tokens, cost) is the thing a wall of raw JSON is worst at answering.
 */
export function StrategyTrace({ records }: { records: TraceRecord[] }) {
  const [open, setOpen] = useState(false);
  const calls = records.filter((record) => record.kind === "LLM_CALL");
  const totals = calls.reduce(
    (accumulated, record) => ({
      latency: accumulated.latency + record.latency_ms,
      input: accumulated.input + record.input_tokens,
      output: accumulated.output + record.output_tokens,
      cost: accumulated.cost + record.cost,
      retries: accumulated.retries + record.retry_count,
    }),
    { latency: 0, input: 0, output: 0, cost: 0, retries: 0 },
  );
  const counts = records.reduce<Record<string, number>>((accumulated, record) => {
    accumulated[record.kind] = (accumulated[record.kind] ?? 0) + 1;
    return accumulated;
  }, {});
  const summary = Object.entries(counts)
    .map(([kind, count]) => `${KIND_LABELS_ZH[kind] ?? kind} ${count}`)
    .join(" · ");

  return (
    <section className="panel" aria-label="技术 Trace">
      <h2>技术 Trace</h2>
      <p className="muted">{`共 ${records.length} 条记录：${summary}`}</p>
      <label>
        <input
          type="checkbox"
          checked={open}
          onChange={(event) => setOpen(event.target.checked)}
        />
        技术模式
      </label>
      {open && (
        <>
          <h3>{`模型调用明细（${calls.length} 次）`}</h3>
          <div className="table-scroll">
            <table className="diff trace-table">
              <thead>
                <tr>
                  <th scope="col">步</th>
                  <th scope="col">Prompt 版本</th>
                  <th scope="col">延迟</th>
                  <th scope="col">输入 tokens</th>
                  <th scope="col">输出 tokens</th>
                  <th scope="col">成本</th>
                  <th scope="col">重试</th>
                  <th scope="col">状态</th>
                </tr>
              </thead>
              <tbody>
                {calls.map((record) => (
                  <tr key={record.trace_id}>
                    <td>{record.step_id}</td>
                    <td>{record.prompt_version ?? "—"}</td>
                    <td>{`${(record.latency_ms / 1000).toFixed(1)}s`}</td>
                    <td>{record.input_tokens}</td>
                    <td>{record.output_tokens}</td>
                    <td>{`$${record.cost.toFixed(5)}`}</td>
                    <td>{record.retry_count}</td>
                    <td className={record.status === "RECEIVED" ? "changed" : undefined}>
                      {record.status}
                    </td>
                  </tr>
                ))}
                {calls.length > 0 && (
                  <tr className="totals">
                    <td colSpan={2}>合计</td>
                    <td>{`${(totals.latency / 1000).toFixed(1)}s`}</td>
                    <td>{totals.input}</td>
                    <td>{totals.output}</td>
                    <td>{`$${totals.cost.toFixed(5)}`}</td>
                    <td>{totals.retries}</td>
                    <td>—</td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
          <details className="technical">
            <summary>{`全部 ${records.length} 条原始记录（JSON）`}</summary>
            {records.map((record) => (
              <pre className="code" key={record.trace_id}>
                {formatRecord(record)}
              </pre>
            ))}
          </details>
        </>
      )}
    </section>
  );
}
