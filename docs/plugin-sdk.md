# v2 Plugin SDK

生产插件位于：

```text
plugins/<plugin-id>/
  plugin.yaml
  plugin.py | plugin.js
```

教学和验证示例位于：

```text
examples/plugins/<plugin-id>/
```

框架只会自动发现生产目录 `plugins/`。示例目录必须通过 CLI 或 Runtime 显式传入，避免教学插件进入生产节点注册表。

## Manifest

```yaml
id: echo
version: "1.0.0"
kind: node
runtime:
  type: python
  entry: plugin.py
  timeout_seconds: 30
contracts:
  input: []
  output:
    - plugin-echo@v2
```

Manifest 必须满足 `schemas/v2/plugin-manifest.schema.json`。Workflow 中的实现 ID 为 `plugin.<id>`：

```yaml
- id: echo
  type: plugin
  implementation: plugin.echo
  outputs: [plugin_echo]
```

## stdin 协议

```json
{
  "run_context": {},
  "inputs": {},
  "artifacts": {},
  "domain": {},
  "node": {},
  "plugin_config": {}
}
```

## stdout 协议

```json
{
  "status": "success",
  "outputs": {},
  "artifacts": [],
  "metrics": {},
  "warnings": []
}
```

非零退出码、超时、无效 JSON 或 `status=failed` 都会转换为 Workflow 节点失败。

## Contract 校验

`contracts.input` 在插件执行前校验已有 Artifact；`contracts.output` 在插件执行后校验输出。引用格式为：

```text
<schema-name>@v<version>
```

## 命令

默认只查看生产插件：

```bash
e2e-agent plugins --json
```

显式查看示例插件：

```bash
e2e-agent plugins --path examples/plugins --json
```

执行 Echo 示例：

```bash
e2e-agent run \
  --app apps/demo-generic-form/app.yaml \
  --workflow examples/workflows/plugin-smoke.yaml \
  --plugin-dir examples/plugins \
  --inputs-json inputs.json
```

代码中可显式注入：

```python
runtime = WorkflowRuntime(
    repo_root=repo_root,
    plugin_roots=[repo_root / "examples" / "plugins"],
)
```
