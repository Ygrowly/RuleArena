import { ACTION_LABELS_ZH, actionLabel, diffSnapshots } from "../domain/diff";
import { ORACLE_STATUS_LABELS_ZH, invariantTitle } from "../domain/invariants";
import { describeStatus } from "../domain/outcome";
import type {
  CounterexampleRecord,
  FrozenAction,
  FrozenReplay,
  OracleFinding,
} from "../api/types";

/** The Oracle's coverage, stated as a count so a reader sees it without expanding. */
function findingsSummary(findings: OracleFinding[]): string {
  const counts = findings.reduce<Record<string, number>>((accumulated, finding) => {
    accumulated[finding.status] = (accumulated[finding.status] ?? 0) + 1;
    return accumulated;
  }, {});
  const parts = Object.entries(counts)
    .map(([status, count]) => `${count} ${ORACLE_STATUS_LABELS_ZH[status] ?? status}`)
    .join(" / ");
  return `Oracle 检查的全部 ${findings.length} 条不变量（${parts}）`;
}

/**
 * The Oracle's own statement of what broke, quoted rather than paraphrased.
 *
 * The path came from a search; this is the part that is not an opinion, so it is shown
 * as the verdict rather than summarised by the UI.
 */
export function OracleVerdict({
  findings,
  target,
}: {
  findings?: OracleFinding[];
  target: string;
}) {
  const finding = findings?.find((item) => item.invariant === target) ?? findings?.[0];
  if (!finding) {
    return null;
  }
  return (
    <blockquote className="verdict">
      <p className="verdict-label">
        {`Oracle 裁决 · ${invariantTitle(finding.invariant)} · ${finding.status}`}
      </p>
      <p className="verdict-text">{finding.explanation}</p>
    </blockquote>
  );
}

export function ActionPath({ actions }: { actions: FrozenAction[] }) {
  return (
    <ol className="path">
      {actions.map((action, index) => (
        <li key={action.idempotency_key ?? index}>
          <span className="step-no" aria-hidden="true">
            {index + 1}
          </span>
          <span>
            <strong>{ACTION_LABELS_ZH[action.action_type] ?? action.action_type}</strong>
            <span className="muted">{` ${actionLabel(action)}`}</span>
          </span>
        </li>
      ))}
    </ol>
  );
}

export function StateDiff({
  snapshots,
  violatingStep,
}: {
  snapshots: { state_hash: string; state: FrozenReplay["snapshots"][number]["state"] }[];
  violatingStep?: number;
}) {
  if (snapshots.length < 2) {
    return <p className="muted">没有可展示的状态变化。</p>;
  }
  const rows = snapshots
    .slice(1)
    .map((snapshot, index) => ({
      step: index + 1,
      rows: diffSnapshots(snapshots[index], snapshot),
    }));
  return (
    <div>
      {rows.map(({ step, rows: stepRows }) => {
        const violating = step === violatingStep;
        return (
          <div key={step} className={violating ? "step-diff violating" : "step-diff"}>
            <h3>
              {`第 ${step} 步之后`}
              {violating && <span className="violation-tag">违规发生在此步</span>}
            </h3>
            <table className="diff">
              <thead>
                <tr>
                  <th scope="col">资产 / 指标</th>
                  <th scope="col">变化前</th>
                  <th scope="col">变化后</th>
                </tr>
              </thead>
              <tbody>
                {stepRows
                  .filter((row) => row.changed)
                  .map((row) => (
                    <tr key={row.label}>
                      <td>{row.label}</td>
                      <td>{row.before}</td>
                      <td className={violating ? "changed violating" : "changed"}>
                        {row.after}
                      </td>
                    </tr>
                  ))}
              </tbody>
            </table>
          </div>
        );
      })}
    </div>
  );
}

export function CounterexampleEvidence({
  counterexample,
  replay,
  fixedReplay,
  replayStability = "3/3",
}: {
  counterexample: CounterexampleRecord;
  replay: FrozenReplay;
  fixedReplay?: FrozenReplay;
  replayStability?: string;
}) {
  const fixedClean =
    fixedReplay !== undefined &&
    fixedReplay.classification !== "CONFIRMED_VIOLATION";
  return (
    <section className="panel" aria-label="反例证据">
      <h2>{`证据：最小反例 ${counterexample.invariant_id}`}</h2>
      <p>
        <span className="chip ok">{`重放稳定性 ${replayStability}`}</span>
        <span className="chip">{`Oracle: ${replay.target_invariant}`}</span>
        {fixedReplay && (
          <span className={`chip ${fixedClean ? "ok" : "warn"}`}>
            {fixedClean
              ? "Fixed v2 回归：旧反例不再成立"
              : "Fixed v2 回归：仍触发违规（异常）"}
          </span>
        )}
      </p>
      <OracleVerdict findings={replay.findings} target={replay.target_invariant} />
      {replay.findings && replay.findings.length > 0 && (
        <details className="technical">
          <summary>{findingsSummary(replay.findings)}</summary>
          <ul className="findings">
            {replay.findings.map((finding) => (
              <li
                key={finding.invariant}
                className={finding.status === "VIOLATED" ? "violated" : undefined}
              >
                <span className="finding-status">
                  {ORACLE_STATUS_LABELS_ZH[finding.status] ?? finding.status}
                </span>
                <span>{invariantTitle(finding.invariant)}</span>
                <code>{finding.invariant}</code>
              </li>
            ))}
          </ul>
        </details>
      )}
      <h3>最小动作序列（Delta Minimization）</h3>
      <ActionPath actions={replay.actions} />
      <h3>每步状态 Diff（真实 Sandbox 快照）</h3>
      <StateDiff snapshots={replay.snapshots} violatingStep={replay.actions.length} />
      <h3>回执与事件</h3>
      <ul className="muted">
        {replay.receipts.slice(0, 6).map((receipt, index) => (
          <li key={index}>
            {`Receipt ${String(receipt.receipt_id ?? index)}: ${String(receipt.status ?? "OK")}`}
          </li>
        ))}
        {replay.events.slice(0, 6).map((event, index) => (
          <li key={`e${index}`}>{`Event: ${String(event.event_type ?? event.type ?? "domain")}`}</li>
        ))}
      </ul>
    </section>
  );
}

export function OutcomeBanner({
  status,
  outcome,
}: {
  status: Parameters<typeof describeStatus>[0];
  outcome: Parameters<typeof describeStatus>[1];
}) {
  const state = describeStatus(status, outcome);
  return (
    <div className={`banner ${state.tone}`} role="status" aria-live="polite">
      <strong>{state.label}</strong> — {state.detail}
    </div>
  );
}
