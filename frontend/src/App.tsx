import { useEffect, useState } from "react";
import type { FrozenDemo } from "./api/types";
import { FrozenDemoView } from "./components/FrozenDemo";
import { LiveRunView } from "./components/LiveRun";

type View = "frozen" | "live";

const STEPS = ["选择规则", "确认规则", "Arena 运行", "查看证据", "修复回归"];

export function App() {
  const [view, setView] = useState<View>("frozen");
  const [demo, setDemo] = useState<FrozenDemo | null>(null);
  const [demoError, setDemoError] = useState<string | null>(null);

  useEffect(() => {
    fetch("/frozen/golden-run.json")
      .then((response) => {
        if (!response.ok) throw new Error(`frozen demo unavailable (${response.status})`);
        return response.json() as Promise<FrozenDemo>;
      })
      .then(setDemo)
      .catch((cause: unknown) => setDemoError(String(cause)));
  }, []);

  return (
    <>
      <header className="hero">
        <h1>RuleArena</h1>
        <p className="tagline">
          AI 搜索电商规则的异常操作组合，真实 API 重放，确定性 Oracle 裁决。
        </p>
      </header>
      <main>
        <nav className="view-switch" aria-label="视图切换">
          <button
            type="button"
            className={view === "frozen" ? "primary" : ""}
            onClick={() => setView("frozen")}
          >
            冻结黄金案例
          </button>
          <button
            type="button"
            className={view === "live" ? "primary" : ""}
            onClick={() => setView("live")}
          >
            实时运行
          </button>
        </nav>
        <ol className="stepper" aria-label="黄金用户旅程">
          {STEPS.map((step, index) => (
            <li key={step}>{`${index + 1}. ${step}`}</li>
          ))}
        </ol>
        {view === "frozen" ? (
          <FrozenDemoView demo={demo} error={demoError ?? undefined} />
        ) : (
          <LiveRunView />
        )}
        <footer className="muted" style={{ padding: "12px 0" }}>
          Outcome 语义说明：「预算内未发现违规」不等于「规则安全」；已确认违规唯一来源是真实
          Sandbox 重放后的确定性 Oracle。
        </footer>
      </main>
    </>
  );
}
