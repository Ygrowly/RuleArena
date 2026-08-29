# 阶段 5 Review Spec：最终独立验收

角色：独立 Release Reviewer  
模式：默认只读；把在线 Demo 当成陌生用户与攻击者共同审查。  
结论：`RELEASE`、`RELEASE WITH ACCEPTED RISKS` 或 `BLOCK`。

## 1. 审查输入

阅读整个规格包、所有阶段 Review、最终 diff、README、ADR、安全模型、Benchmark 原始结果、部署配置和线上 URL。确认前序 P0/P1 的关闭证据。

## 2. 三条审查视角

### HR/产品视角

- 30 秒内能否理解场景、问题和结果？
- 首屏是否突出可信闭环，而非多 Agent 名词堆砌？
- 黄金 Case 是否有清晰的“损失/异常权益—动作路径—修复”故事？

### 技术面试官视角

- 能否从 UI/README 追到 RuleSpec、Runtime、Simulator、真实 API、Oracle、Trace 与评测？
- 是否清楚说明为什么不是 Prompt/Skill，以及为何多 Agent 可能或不可能产生边际收益？
- 指标和架构取舍是否有实际证据，还是营销表述？

### 生产/安全视角

- 公共请求能否访问 Sandbox、内部端点、隐藏集、Trace 敏感内容或无限消耗模型？
- 刷新、重试、超时、并发和 Worker 崩溃是否保持幂等与权威状态？
- 部署配置、迁移、健康、回滚和日志是否可运维？

## 3. 亲自完成黄金旅程

在全新浏览器会话执行：模板 → 修改规则 → 歧义确认 → Live/Frozen Arena → 最小反例 → Trace → Fixed 回归。记录每步时间、阻塞、文案误导和控制台/网络错误。

至少测试：移动视口、键盘、SSE 断线重连、页面刷新、重复点击、超时、取消、无模型、无 Worker、Sandbox 故障、限流。验证 Frozen Demo 不依赖实时模型且数据确来自真实完成 Run。

## 4. 发布与安全验证

- 运行全量后端/前端/E2E、Docker smoke、最新 Benchmark Gate。
- 从公网尝试访问 Sandbox、内部/管理/隐藏评测端点。
- 检查 CORS、Host、错误堆栈、Source Map、前端环境变量和日志。
- 核对 Railway 服务可见性、数据库角色、内部令牌和预算配置。
- 抽样从 UI Finding 反查 Receipt/Event/Oracle/版本元组。

未经用户授权，不创建新云资源、不改变线上数据、不进行高流量压力测试；采用小规模安全探测。

## 5. 严重级别

- P0：Ground Truth/密钥泄漏；Sandbox 公网可写；可绕过成本预算；漏洞证据伪造；正常 Case confirmed 误报；数据跨 Run；核心 E2E 不成立。
- P1：门禁未达却宣称完成；Frozen 数据非真实 Run；刷新/恢复/幂等失败；关键失败状态误导；部署不可复现。
- P2：视觉、文案、轻微无障碍、性能和非阻断开发体验问题。

## 6. 最终验收矩阵

逐项给出 `PASS`、`FAIL`、`NOT VERIFIED` 与证据：

1. 三类 RuleSpec 与歧义确认。
2. 三策略隔离及受控 Runtime。
3. Simulator/Sandbox/Oracle 独立性。
4. confirmed 黑盒重放与 1-minimal。
5. 24 Case 和四 Baseline。
6. 0 normal confirmed 误报。
7. confirmed 3/3 重放。
8. hidden ≥75%。
9. 历史 P0 回归 100%。
10. Ground Truth 泄漏 0。
11. 在线黄金闭环。
12. 公共部署安全与成本边界。

## 7. 输出模板

```text
最终结论：RELEASE | RELEASE WITH ACCEPTED RISKS | BLOCK
审查版本、环境与 URL
黄金旅程记录
实际执行的测试、安全探测和结果
Findings：P0/P1/P2，含复现和证据
12 项最终验收矩阵
HR 30 秒理解结论
技术深挖完整性结论
已接受风险与上线后动作
可公开/不可公开的事实性项目表述
```

任一 P0 必须 `BLOCK`。P1 只有经用户逐项明确接受才可 `RELEASE WITH ACCEPTED RISKS`；`NOT VERIFIED` 不得自动视为通过。
