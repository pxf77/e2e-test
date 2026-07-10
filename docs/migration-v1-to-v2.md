# v1 到 v2 迁移指南

## 目标

v2 将当前保险 P0 回归框架演进为通用 E2E 回归框架，同时保留 v1 `product-input.json` 入口。

## 映射关系

| v1 | v2 |
|---|---|
| `product-input.json` | `apps/<app>/app.yaml` |
| `config/state-deps.yaml` | `domains/insurance/state-deps.yaml` |
| `config/assertion-templates.yaml` | `domains/insurance/assertion-pack.yaml` |
| 固定 `graph.py` | `workflows/*.yaml` + `WorkflowRuntime` |
| `PlaywrightTSRunner` | `runners/playwright/runner.py` 适配器 |
| 固定 `E2EAgentState` | `WorkflowRuntimeState` + artifact map |
| Agent 分散产物 | `.assets/runs/<run-id>/artifact-manifest.json` |

## 当前兼容策略

- `e2e-agent run --product-input <path>` 保持旧行为。
- `e2e-agent run --app <path> --workflow <workflow-id>` 已执行真实 v2 Workflow Runtime。
- `p0-insurance-regression` 继续复用现有四个 Agent；Agent2 通过 legacy adapter 读取 `domains/insurance/ontology.yaml` 与 `state-deps.yaml`。
- `p0-web-regression` 和 `smoke-static-web` 使用通用内置节点。
- 每个节点输出都会写入 `by-node/<node-id>/` 并登记到 Artifact Manifest。

## v2 运行示例

```bash
e2e-agent run \
  --app apps/demo-generic-form/app.yaml \
  --workflow smoke-static-web \
  --run-id local-smoke
```

执行结果摘要包含：

- `run_id`
- `status`
- `pending_gates`
- `artifact_names`
- `artifact_manifest`
- `artifacts_dir`

运行目录示例：

```text
apps/demo-generic-form/.assets/runs/local-smoke/
  artifact-manifest.json
  run-context.json
  by-node/
    explore/page_registry.json
    execute/execution_result.json
    report/test_report.json
```

## 推荐迁移步骤

1. 为现有产品创建 `apps/<app>/app.yaml`。
2. 将页面模型和测试数据移入 App Pack。
3. 将页面类型、业务意图、流程链、state deps 和断言模板放入对应 Domain Pack。
4. 使用 `e2e-agent validate app <path>` 校验配置。
5. 使用 `smoke-static-web` 验证基础接入。
6. 使用 `p0-web-regression` 验证通用 Gate 流程，或使用 `p0-insurance-regression` 兼容现有保险 Agent。
7. 检查 `artifact-manifest.json`，确认所有节点产物均有 producer、path 和 sha256。
