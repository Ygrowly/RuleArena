# Benchmark 实测报告（golden-v4）

> 数据全部来自 PostgreSQL 中 append-only 的原始 BenchmarkRun；每个聚合项可在 `control.benchmark_run` 中按 Run ID 复算。hidden suite 只输出聚合指标，不披露任何单 Case 期望答案。本表**只取 golden-v4**：跨版本的 case 环境不同，数字不可混排。

## development suite

| Baseline | reps | 发现率 | 误报 | 候选确认 | 重放稳定 | elapsed mean/med/p95 (s) | tokens mean | cost mean ($) |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| RANDOM | 1 | 0/14 [0–22%] (0%) | 0/7 [0–35%] | 0/91 [0–4%] | N/A | 60.03 / 67.11 / 76.38 | 0 | 0.0000 |
| BFS | 1 | 2/14 [4–40%] (14%) | 0/7 [0–35%] | 2/91 [1–8%] | 6/6 [61–100%] | 65.42 / 73.81 / 85.72 | 0 | 0.0000 |
| SINGLE_AGENT | 0 | N/A | N/A | N/A | N/A | N/A | N/A | N/A |
| MULTI_STRATEGY | 0 | N/A | N/A | N/A | N/A | N/A | N/A | N/A |

### development 每 Case 明细（公开 suite）

| Case | Random | BFS | Single | Multi |
| --- | --- | --- | --- | --- |
| dev-membership-01 | — | — | — | — |
| dev-membership-02 | — | — | — | — |
| dev-membership-03 | — | ✅ | — | — |
| dev-membership-04 | — | — | — | — |
| dev-membership-05 | — | — | — | — |
| dev-membership-06 | — | — | — | — |
| dev-membership-07 | — | ✅ | — | — |
| dev-promotion-01 | — | — | — | — |
| dev-promotion-02 | — | — | — | — |
| dev-promotion-03 | — | — | — | — |
| dev-promotion-04 | — | — | — | — |
| dev-promotion-05 | — | — | — | — |
| dev-promotion-06 | — | — | — | — |
| dev-promotion-07 | — | — | — | — |
| dev-refund-01 | — | — | — | — |
| dev-refund-02 | — | — | — | — |
| dev-refund-03 | — | — | — | — |
| dev-refund-04 | — | — | — | — |
| dev-refund-05 | — | — | — | — |
| dev-refund-06 | — | — | — | — |
| dev-refund-07 | — | — | — | — |

### Run ID 溯源

- RANDOM: `b3b0181f-7cfc-40fb-b62a-c884d8219618` (reps=1, seed=20260831)
- BFS: `5589d5d5-4ff5-4086-851c-be7a6c758247` (reps=1, seed=20260831)

## hidden suite

| Baseline | reps | 发现率 | 误报 | 候选确认 | 重放稳定 | elapsed mean/med/p95 (s) | tokens mean | cost mean ($) |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| RANDOM | 0 | N/A | N/A | N/A | N/A | N/A | N/A | N/A |
| BFS | 0 | N/A | N/A | N/A | N/A | N/A | N/A | N/A |
| SINGLE_AGENT | 0 | N/A | N/A | N/A | N/A | N/A | N/A | N/A |
| MULTI_STRATEGY | 0 | N/A | N/A | N/A | N/A | N/A | N/A | N/A |

### Run ID 溯源

