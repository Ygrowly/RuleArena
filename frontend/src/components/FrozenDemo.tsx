import { useState } from "react";
import type { FrozenDemo } from "../api/types";
import { invariantTitle } from "../domain/invariants";
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
  const foundByModel = demo.provenance.search_mode === "live_model";
  return (
    <div>
      <section className="panel result-hero" aria-label="冻结案例说明">
        <p className="result-kicker">冻结黄金案例（已完成的真实运行）</p>
        <h2 className="result-headline">
          {foundByModel
            ? `模型在 ${steps} 步内搜索到一条可复现的${invariantTitle(counterexample.invariant_id)}违规`
            : `一条 ${steps} 步可复现的${invariantTitle(
                counterexample.invariant_id,
              )}违规（路径由确定性脚本给出，裁决来自真实重放与 Oracle）`}
        </h2>
        <ul className="result-facts">
          <li>动作经真实 Commerce Sandbox HTTP API 重放</li>
          <li>是否违规由确定性 Oracle 判定，模型只负责提议路径</li>
          <li>独立干净数据空间重放 3/3 稳定</li>
          <li>切换到 Fixed v2 后，同一路径不再成立</li>
        </ul>
        <OutcomeBanner status={demo.run.status} outcome={demo.run.outcome} />
        <p className="muted">{demo.provenance.honesty}</p>
      </section>

      <section className="panel" aria-label="规则与契约">
        <h2>规则与契约</h2>
        <div className="columns">
          <div>
            <h3>自然语言规则</h3>
            <p>{demo.rule.chinese_modification}</p>
          </div>
          <div>
            <h3>冻结 RuleSpec（只读）</h3>
            <p className="muted">
              编译后不可变，content hash 绑定；歧义必须人工确认后才能进入搜索。
            </p>
            <button type="button" onClick={() => setShowRawSpec((value) => !value)}>
              {showRawSpec ? "收起" : "展开"} JSON
            </button>
            {showRawSpec && (
              <pre className="code">{JSON.stringify(demo.rule.rule_spec, null, 2)}</pre>
            )}
          </div>
        </div>
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
