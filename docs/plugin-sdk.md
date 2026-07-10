# v2 Plugin SDK

插件位于：

```text
plugins/<plugin-id>/
  plugin.yaml
  plugin.py | plugin.js
```

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

```bash
e2e-agent plugins --json
e2e-agent run --app apps/demo-generic-form/app.yaml --workflow plugin-smoke --inputs-json inputs.json
```
