import { useState } from "react";
import type { FrozenDemo } from "../api/types";
import { CounterexampleEvidence, OutcomeBanner } from "./Evidence";
import { StrategyTrace } from "./TechnicalTrace";

export function FrozenDemoView({ demo }: { demo: FrozenDemo | null; error?: string }) {
  const [showRawSpec, setShowRawSpec] = useState(false);
  if (!demo) {
    return (
      <section className="panel">
        <p className="muted">正在加载冻结黄金案例…</p>
      </section>
    );
  }
  const counterexample = demo.counterexamples[0];
  const steps = demo.evidence.vulnerable.actions.length;
  return (
    <div>
      <section className="panel" aria-label="冻结案例说明">
        <h2>冻结黄金案例（已完成的真实运行）</h2>
        <p className="muted">{demo.provenance.honesty}</p>
        <div className="columns">
          <div>
            <h3>自然语言规则</h3>
            <p>{demo.rule.chinese_modification}</p>
          </div>
          <div>
            <h3>冻结 RuleSpec（只读）</h3>
            <button type="button" onClick={() => setShowRawSpec((value) => !value)}>
              {showRawSpec ? "收起" : "展开"} JSON
            </button>
            {showRawSpec && (
              <pre className="code">{JSON.stringify(demo.rule.rule_spec, null, 2)}</pre>
            )}
          </div>
        </div>
      </section>

      <section className="panel">
        <h2>运行结论</h2>
        <OutcomeBanner status={demo.run.status} outcome={demo.run.outcome} />
        <p className="muted">
          {`Run ${demo.run.run_id.slice(0, 8)}… · sandbox=${demo.run.sandbox_version} · oracle=${demo.run.oracle_version} · 最小反例 ${steps} 步`}
        </p>
      </section>

      {counterexample && (
        <CounterexampleEvidence
          counterexample={counterexample}
          replay={demo.evidence.vulnerable}
          fixedReplay={demo.evidence.fixed_regression}
        />
      )}

      <section className="panel">
        <h2>修复回归</h2>
        <p>
          同一最小反例在 <strong>vulnerable</strong> Profile 上由 Oracle 判定违规；切换{" "}
          <strong>Fixed v2</strong> 后重放，旧反例不再成立，正常退款路径仍通过。
        </p>
        <p className="muted">
          {`vulnerable=${demo.evidence.vulnerable.classification} · fixed=${demo.evidence.fixed_regression.classification}`}
        </p>
      </section>

      <StrategyTrace records={demo.trace} />
    </div>
  );
}
