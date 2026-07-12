# v1 到 v2 迁移指南

v2 将保险 P0 回归框架演进为通用 E2E 回归框架，同时在 1.x 保留 `product-input.json` 入口。

## 映射关系

| v1 | v2 |
|---|---|
| `product-input.json` | `apps/<app>/app.yaml` |
| 全局保险 state deps | `domains/insurance/state-deps.yaml` |
| 全局保险 assertion templates | `domains/insurance/assertion-pack.yaml` |
| 固定 `graph.py` | `workflows/*.yaml` + `WorkflowRuntime` |
| `PlaywrightTSRunner` | `runners/playwright/runner.py` 适配器 |
| 固定 `E2EAgentState` | `WorkflowRuntimeState` + artifact map |
| Agent 分散产物 | `.assets/runs/<run-id>/artifact-manifest.json` |

## 当前兼容策略

- `e2e-agent run --product-input <path>` 保持旧行为。
- `e2e-agent run --app <path> --workflow <workflow-id>` 执行 v2 Workflow Runtime。
- `p0-insurance-regression` 继续复用四个 Legacy Agent；Agent2 从 Insurance Domain Pack 读取 ontology 与 state deps。
- `p0-web-regression` 和 `smoke-static-web` 使用通用内置节点。
- 每个节点输出写入 `by-node/<node-id>/` 并登记到 Artifact Manifest。

## v2 运行示例

```bash
e2e-agent run \
  --app apps/demo-generic-form/app.yaml \
  --workflow smoke-static-web \
  --run-id local-smoke
```

执行结果摘要包含 `run_id`、`status`、`pending_gates`、`artifact_names`、`artifact_manifest` 和 `artifacts_dir`。

## 推荐步骤

1. 创建 `apps/<app>/app.yaml`。
2. 将页面模型和测试数据移入 App Pack。
3. 将页面类型、业务意图、流程链、状态依赖和断言模板放入 Domain Pack。
4. 使用 `e2e-agent validate app <path>` 校验配置。
5. 使用 `smoke-static-web` 验证基础接入。
6. 使用 `p0-web-regression` 验证通用 Gate，或使用 `p0-insurance-regression` 兼容保险 Agent。
7. 检查 `artifact-manifest.json` 中的 producer、path 和 sha256。
