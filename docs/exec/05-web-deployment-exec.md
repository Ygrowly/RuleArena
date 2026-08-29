# 阶段 5 执行 Spec：在线 Demo、部署与最终交付

状态：待执行  
前置条件：阶段 4 Review 通过  
阶段目标：把可信技术闭环变成 HR 能在 30 秒理解、技术面试官能继续下钻、用户能安全试用的在线 Demo。

## 1. 给 Codex 的任务

完成 MVP Web、Railway/Docker 部署、安全收口、E2E、演示资产和开源 README。开始前阅读全部 Spec、阶段 4 Review 和现有设计；先检查仓库是否已有视觉规范。不得添加账户组织、支付、RAG、通用规则平台或其他 P1/P2 功能。

## 2. 黄金用户旅程

页面必须围绕一条主线，而非管理后台功能墙：

1. **选择规则**：三个业务模板；默认进入黄金优惠/退款/积分 Case。
2. **确认规则**：自然语言与 RuleSpec 双栏，歧义必须显式确认。
3. **Arena 运行**：显示 Random/BFS 与三策略的独立进度、预算和当前动作，不展示思维链。
4. **查看证据**：最小动作序列、每步资产/状态 Diff、Receipt/Event、Oracle invariant 和重放 3/3。
5. **修复回归**：切换 Fixed v2，旧反例不再成立且正常退款仍通过。

首屏文案必须在一句话内说明：AI 搜索电商规则的异常操作组合，真实 API 重放，确定性 Oracle 裁决。避免“智能审查产品方案”等过宽描述。

## 3. Demo 展示要求

- 提供无需模型等待即可查看的预录/冻结黄金运行，明确标记为“已完成真实运行”，数据来自持久化 Run，不是前端伪造。
- 提供限额 Live Run；不可用时显示真实失败状态，并允许继续查看冻结案例。
- Arena 用可辨识策略颜色/图标和资产流动画增强理解，但不能牺牲证据可读性。
- State Diff 优先展示净支付、退款、积分、优惠券、权益与订单状态。
- Trace 默认折叠技术细节，技术模式可展开模型版本、工具调用、Hash、延迟和成本。
- 所有 Outcome 文案严格遵守语义；`NO_VIOLATION_WITHIN_BUDGET` 不写“安全”。
- 桌面端重点优化，移动端至少可完整查看冻结案例。

## 4. 前后端实现

- React/TypeScript/Vite，复用现有 API Schema 生成或类型安全客户端。
- React Flow 仅用于紧凑动作/状态路径，不构建通用图编辑器。
- SSE 支持断线重连，页面刷新后从权威 Run API 恢复。
- 明确展示 loading、empty、ambiguous、unsupported、cancelled、infra failed、unconfirmed 和 budget exhausted。
- 所有写请求带 Idempotency-Key；防止按钮连点重复创建运行。
- 基础无障碍：键盘可操作、焦点、语义标签、对比度和 reduced motion。

## 5. 部署与安全

- 本地 Docker Compose 一条命令启动；Railway 部署 Web/Control、Worker、Sandbox、PostgreSQL、Redis。
- Railway 只公开 Web/Control；Sandbox 仅私网并验证内部令牌。
- 迁移在服务启动前执行且失败阻断发布。
- `/healthz`、`/readyz`、Worker 心跳和依赖故障可观测。
- 公共 Demo 按 IP + Session 限流；单次运行默认 90 秒、3 策略和明确 Token/成本上限。
- CORS、可信 Host、请求大小、日志脱敏、异常响应、依赖密钥全部收口。
- 冻结案例只读；管理/评测内部端点不公开。

## 6. 开源与面试材料

根 README 至少包含：一句话定位、30 秒 GIF/截图、为什么不是 Prompt、架构图、黄金 Case、快速启动、评测表、边界/诚实声明、设计取舍和部署链接。

提供：

- `docs/demo-script.md`：3 分钟演示脚本。
- `docs/architecture-decisions.md`：至少记录 Simulator/Sandbox 分离、确定性 Runtime、多策略隔离、Oracle 裁决、不用 LangGraph 五个 ADR 摘要。
- `docs/security-model.md`：资产、信任边界、Ground Truth、Prompt injection、公共成本攻击。
- 实际 Benchmark 结果与生成命令；没有数据的格子标 N/A，不填估计值。

## 7. E2E 与验收测试

- 从模板编译到歧义确认、运行、confirmed 反例、Fixed 回归的完整 E2E。
- 刷新、SSE 断线、Worker 重启、按钮连点、Live Run 超时/成本上限。
- Sandbox 公网不可达、内部令牌错误、管理端点未暴露。
- Frozen Demo 在 LLM/Worker 临时故障时仍可浏览。
- 桌面主流浏览器、基础移动视口和键盘流程。
- Lighthouse/等价检查记录性能、可访问性和安全问题，不要求追求虚假满分。

建议命令：

```bash
uv run pytest -q
npm --prefix frontend test
npm --prefix frontend run lint
npm --prefix frontend run typecheck
npm --prefix frontend run build
npm --prefix frontend run test:e2e
docker compose config
docker compose up -d --build
uv run rulearena benchmark verify --latest
```

## 8. 最终发布门

| 验收项 | 通过条件 |
| --- | --- |
| 理解速度 | 非技术用户 30 秒能说出“搜索路径 + API 重放 + Oracle” |
| 黄金闭环 | Vulnerable → Confirm → Minimize → Fixed → Regression 可演示 |
| 可信证据 | UI 中每个漏洞可追到真实 Run/Receipt/Event/Oracle |
| 质量门 | 24 Case、0 正常误报、3/3、隐藏 ≥75%、P0 回归 100% |
| 公共安全 | Sandbox 不公开、限流/预算/密钥/错误收口 |
| 可复现 | 本地 Docker 和在线部署均通过 smoke test |
| 诚实表达 | 不宣称形式化证明，不虚构用户与指标 |

## 9. 停止条件

如果最终 Benchmark 未达门禁，不得通过隐藏失败、改 UI 文案或删 Case 上线；可部署“技术预览”，但 README 和 Demo 必须明确当前限制。若 Railway 私网或成本控制无法满足，优先关闭 Live Run，仅发布冻结真实案例，不暴露 Sandbox。

## 10. 完成报告

附：线上 URL、部署版本、全部测试与 smoke 结果、最终门禁矩阵、公开攻击面、已知限制、3 分钟演示路径、简历可用的事实性项目描述。未经用户明确授权，不自动公开仓库、不 push、不创建云资源或产生费用。
