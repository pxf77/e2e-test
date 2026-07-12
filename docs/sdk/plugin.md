# Plugin SDK

生产插件位于：

```text
plugins/<plugin-id>/
  plugin.yaml
  plugin.py | plugin.js
```

教学示例位于 `examples/plugins/<plugin-id>/`，必须通过 CLI 或 Runtime 显式传入。

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
  output: [plugin-echo@v2]
```

Manifest 必须满足 `schemas/v2/plugin-manifest.schema.json`。Workflow 实现 ID 为 `plugin.<id>`。

## 协议

stdin：

```json
{"run_context": {}, "inputs": {}, "artifacts": {}, "domain": {}, "node": {}, "plugin_config": {}}
```

stdout：

```json
{"status": "success", "outputs": {}, "artifacts": [], "metrics": {}, "warnings": []}
```

非零退出码、超时、无效 JSON 或 `status=failed` 会转换为 Workflow 节点失败。`contracts.input` 在执行前校验已有 Artifact，`contracts.output` 在执行后校验输出。

## 命令

```bash
e2e-agent plugin create my-plugin --runtime python
e2e-agent plugins --json
e2e-agent plugins --path examples/plugins --json

e2e-agent run \
  --app apps/demo-generic-form/app.yaml \
  --workflow examples/workflows/plugin-smoke.yaml \
  --plugin-dir examples/plugins
```

代码注入：

```python
runtime = WorkflowRuntime(
    repo_root=repo_root,
    plugin_roots=[repo_root / "examples" / "plugins"],
)
```
