import { useState } from "react";
import type { TraceRecord } from "../api/types";

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
 * Technical trace: collapsed by default so non-technical readers see the story
 * first; expanding reveals model version, tool calls, hashes, latency and cost.
 */
export function StrategyTrace({ records }: { records: TraceRecord[] }) {
  const [open, setOpen] = useState(false);
  return (
    <section className="panel" aria-label="技术 Trace">
      <h2>技术 Trace</h2>
      <p className="muted">
        {`共 ${records.length} 条记录。默认折叠技术细节；展开后可见模型配置、工具调用、Hash、延迟与成本。`}
      </p>
      <label>
        <input
          type="checkbox"
          checked={open}
          onChange={(event) => setOpen(event.target.checked)}
        />
        技术模式
      </label>
      {open && (
        <details className="technical" open>
          <summary>展开全部记录</summary>
          {records.map((record) => (
            <pre className="code" key={record.trace_id}>
              {formatRecord(record)}
            </pre>
          ))}
        </details>
      )}
    </section>
  );
}
