# Workflow DSL 指南

Workflow DSL 将固定 Graph 拆为可配置 YAML，再由 `WorkflowCompiler` 编译为可执行计划。当前 PR 先提供静态编译骨架，后续可映射到 LangGraph `StateGraph`。

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
| `agent` | AI/确定性业务节点 |
| `gate` | 人工或策略门禁 |
| `runner` | 执行器节点，例如 Playwright/API/Mobile |
| `plugin` | 外部插件节点 |
| `report` | 报告节点 |
| `utility` | 辅助节点 |

## Gate 路由

Gate 节点通常使用：

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

## 校验

```bash
python tools/validate_workflows.py
e2e-agent workflows --json
e2e-agent validate workflow workflows/p0-web-regression.yaml
```
