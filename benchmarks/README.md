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
- `golden-v2`（当前）：仅将 `max_time_seconds` 90 → **300**（依据实测延迟 p95
  校准），Case 内容、期望答案、其他预算与门禁阈值不变。变更原因与依据见
  2026-09-06 真实模型审查报告；旧 golden-v1 运行结果不可复用于 v2 门禁。
