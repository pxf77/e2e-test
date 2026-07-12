# v1 到 v2 迁移指南

v2 将保险 P0 回归框架演进为通用 E2E 回归框架。Legacy product-input、四 Agent 与 Skill 能力继续存在，但源码和契约均位于显式 Legacy 命名空间。

## 配置与运行映射

| Legacy | 当前框架 |
|---|---|
| `product-input.json` | `apps/<app>/app.yaml` |
| 全局保险 state deps | `domains/insurance/state-deps.yaml` |
| 全局保险 assertion templates | `domains/insurance/assertion-pack.yaml` |
| 固定 `graph.py` | `workflows/*.yaml` + `WorkflowRuntime` |
| `PlaywrightTSRunner` | `runners/playwright/runner.py` 适配器 |
| 固定 `E2EAgentState` | `WorkflowRuntimeState` + artifact map |
| Agent 分散产物 | `.assets/runs/<run-id>/artifact-manifest.json` |
| `schemas/<name>.schema.json` | `schemas/v1/<name>.schema.json` |

## Python import 迁移

Legacy 运行模块全部位于 `e2e_agent.legacy`：

```python
from e2e_agent.legacy.graph.graph import build_graph
from e2e_agent.legacy.graph.state import E2EAgentState
from e2e_agent.legacy.skills.loader import SkillPackageLoader
from e2e_agent.legacy.browser.runner import PlaywrightTSRunner
```

保险 Agent 位于：

```text
e2e_agent.legacy.agents.agent1_tc_merge
e2e_agent.legacy.agents.agent2_path_extract
e2e_agent.legacy.agents.agent3_explore
e2e_agent.legacy.agents.agent4_exec
```

此前未带 `legacy` 段的顶层 import 路径不再提供。

## 当前运行策略

- `e2e-agent run --product-input <path>` 显式进入 Legacy runtime。
- `e2e-agent run --app <path> --workflow <workflow-id>` 执行当前 Workflow Runtime。
- `p0-insurance-regression` 通过 `e2e_agent.adapters.legacy` 复用四个保险 Agent；Agent2 从 Insurance Domain Pack 读取 ontology 与 state deps。
- `p0-web-regression` 和 `smoke-static-web` 使用通用内置节点。
- 每个节点输出写入 `by-node/<node-id>/` 并登记到 Artifact Manifest。

## 当前框架运行示例

```bash
e2e-agent run \
  --app apps/demo-generic-form/app.yaml \
  --workflow smoke-static-web \
  --run-id local-smoke
```

执行结果摘要包含 `run_id`、`status`、`pending_gates`、`artifact_names`、`artifact_manifest` 和 `artifacts_dir`。

## 推荐迁移步骤

1. 创建 `apps/<app>/app.yaml`。
2. 将页面模型和测试数据移入 App Pack。
3. 将页面类型、业务意图、流程链、状态依赖和断言模板放入 Domain Pack。
4. 使用 `e2e-agent validate app <path>` 校验配置。
5. 使用 `smoke-static-web` 验证基础接入。
6. 使用 `p0-web-regression` 验证通用 Gate，或使用 `p0-insurance-regression` 兼容保险 Agent。
7. 将 Python import 和直接 Schema 文件路径迁移到显式 Legacy 位置。
8. 检查 `artifact-manifest.json` 中的 producer、path 和 sha256。
