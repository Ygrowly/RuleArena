# RuleArena Benchmark assets

- `development-v1.json` contains the 16 public development cases and their replay evidence.
- `hidden-manifest.json` contains only non-answer metadata for the 8 hidden cases.
- The full hidden suite is not stored in the public repository. Deployment mounts it and sets
  `RULEARENA_PROCESS_ROLE=evaluation` plus `RULEARENA_HIDDEN_SUITE_PATH` for the evaluation job.

The Attack Runtime has no dependency on `rulearena-evaluation`, no hidden-case loader, and no file
tool. This prevents accidental prompt, trace, SSE, or public API exposure. A repository maintainer can
still alter source code to exfiltrate a mounted file; process/container permissions are therefore the
final deployment boundary, not Python object privacy.

## 版本历史

- `golden-v1`：初始 24 Case（16 development + 8 hidden），预算 12 步 / 12k tokens /
  $1.5 / **90s**。真实模型实测（deepseek-v3.2）显示 LLM 每步延迟 5–10s，90s
  结构性不足：Agent baseline 平均 75.8s / 13.75 步即被时间预算截断，从未提交
  候选；非 LLM 的 Random/BFS 不受影响，对比不公平。
- `golden-v2`：仅将 `max_time_seconds` 90 → **300**（依据实测延迟 p95
  校准），Case 内容、期望答案、其他预算与门禁阈值不变。变更原因与依据见
  2026-09-06 真实模型审查报告；旧 golden-v1 运行结果不可复用于 v2 门禁。
- `golden-v3`（当前）：仅将 `max_tokens` 12000 → **100000**，Case 内容、期望答案、
  门禁阈值不变。依据是实测校准：真实模型每步消耗 input+output 的 p95 为 **2735
  tokens**，因此声明的 12 步预算需要 `12 × 2735 = 32820` tokens，而原配置只够
  `12000 / 2267 ≈ 5.3` 步——**"12 步"与"12k tokens"两个数字本身互相矛盾**，任何
  策略都不可能走到声明的步数。新值按 `同时搜索的策略数(3) × 12 步 × p95(2735)
  = 98460` 取整，使 SINGLE 与 MULTI 的每个策略都能走满 12 步。旧 golden-v1/v2
  运行结果不可复用于 v3 门禁，且**跨版本数字不可混排**。
