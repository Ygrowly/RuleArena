import { ACTION_LABELS_ZH, actionLabel, diffSnapshots } from "../domain/diff";
import { describeStatus } from "../domain/outcome";
import type { CounterexampleRecord, FrozenAction, FrozenReplay } from "../api/types";

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
}: {
  snapshots: { state_hash: string; state: FrozenReplay["snapshots"][number]["state"] }[];
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
      {rows.map(({ step, rows: stepRows }) => (
        <div key={step}>
          <h3>{`第 ${step} 步之后`}</h3>
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
                    <td className="changed">{row.after}</td>
                  </tr>
                ))}
            </tbody>
          </table>
        </div>
      ))}
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
      <h2>证据：最小反例 {counterexample.invariant_id}</h2>
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
      <h3>最小动作序列（Delta Minimization）</h3>
      <ActionPath actions={replay.actions} />
      <h3>每步状态 Diff（真实 Sandbox 快照）</h3>
      <StateDiff snapshots={replay.snapshots} />
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
