# v1 到 v2 迁移指南

## 目标

v2 的目标是把当前保险 P0 回归框架改造为通用 E2E 回归框架，同时保留 v1 `product-input.json` 入口。

## 映射关系

| v1 | v2 |
|---|---|
| `product-input.json` | `apps/<app>/app.yaml` |
| `config/state-deps.yaml` | `domains/insurance/state-deps.yaml` |
| `config/assertion-templates.yaml` | `domains/insurance/assertion-pack.yaml` |
| 固定 `graph.py` | `workflows/*.yaml` |
| `PlaywrightTSRunner` | `runners/playwright/runner.py` 适配器 |
| 固定 `E2EAgentState` | `runtime/RunContext` + artifact map |

## 当前兼容策略

- `e2e-agent run --product-input <path>` 保持旧行为。
- `e2e-agent run --app <path>` 当前为 v2 接入包校验型入口，后续接入 Workflow Runtime 后执行完整 v2 流程。
- 保险领域配置已复制到 `domains/insurance`，后续 Agent 应逐步改为从 `DomainPackLoader` 注入。

## 推荐迁移步骤

1. 为现有产品创建 `apps/<app>/app.yaml`。
2. 将产品页面模型和测试数据移入 App Pack。
3. 将行业语义留在 `domains/insurance`，不要写入 Core。
4. 使用 `e2e-agent validate app <path>` 校验配置。
5. 等 Workflow Runtime 完成后，将旧 `product-input.json` 运行迁移到 `run --app`。
