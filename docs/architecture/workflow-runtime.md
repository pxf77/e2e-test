# Workflow DSL 与 Runtime

Workflow DSL 将测试生命周期定义为 YAML，由 `WorkflowCompiler` 校验并编译为 LangGraph `StateGraph`，由 `WorkflowRuntime` 装配 App、Domain、环境、节点和 Gate。

## 最小结构

```yaml
id: p0-web-regression
version: "1.0.0"
nodes:
  - id: case_merge
    type: agent
    implementation: builtin.case_merge
edges:
  - from: case_merge
    to: END
```

## Node 类型

| type | 用途 |
|---|---|
| `agent` | AI 或确定性业务节点 |
| `gate` | 人工或策略门禁 |
| `runner` | Playwright、API、Appium 等执行器节点 |
| `plugin` | 外部插件节点 |
| `report` | 报告节点 |
| `utility` | 数据准备等辅助节点 |

## Gate 路由

```yaml
edges:
  - from: r1_case_review
    on: approved
    to: path_extract
  - from: r1_case_review
    on: rejected
    to: case_merge
  - from: r1_case_review
    on: pending
    to: END
```

统一 Gate CLI：

```bash
e2e-agent gate status <run-id>
e2e-agent gate approve <run-id> --operator qa --note reviewed
e2e-agent gate reject <run-id> --operator qa --note revise
e2e-agent gate resume <run-id>
```

Runtime 会根据 checkpoint 自动识别 v1/v2；`gate-v2` 只作为 1.x 弃用别名保留。

## 校验

```bash
python tools/validate_workflows.py
e2e-agent workflows --json
e2e-agent validate workflow workflows/p0-web-regression.yaml
```
