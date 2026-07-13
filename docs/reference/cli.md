# CLI Reference

正式 console-script 入口：

```toml
e2e-agent = "e2e_agent.commands.main:main"
```

## Run

```bash
e2e-agent run --app apps/demo-ecommerce/app.yaml --workflow p0-web-regression
e2e-agent run --product-input products/test-product/eman/product-input.json
```

示例插件需要显式目录：

```bash
e2e-agent run \
  --app apps/demo-generic-form/app.yaml \
  --workflow examples/workflows/plugin-smoke.yaml \
  --plugin-dir examples/plugins
```

## Gate

```bash
e2e-agent gate status <run-id>
e2e-agent gate summary <run-id>
e2e-agent gate approve <run-id> [--gate r1] --operator qa --note reviewed
e2e-agent gate reject <run-id> [--gate r1] --operator qa --note revise
e2e-agent gate resume <run-id>
```

CLI 根据 checkpoint 自动识别 v1/v2。`gate-v2` 已在 2.0 删除。

## Inspection

```bash
e2e-agent doctor --json
e2e-agent domains --json
e2e-agent workflows --json
e2e-agent runners --json
e2e-agent plugins --json
e2e-agent data-providers --json
```

## Plugin scaffold

```bash
e2e-agent plugin create my-plugin --runtime python
e2e-agent plugin create my-plugin --root examples/plugins
```

## Validation and acceptance

```bash
e2e-agent validate app apps/demo-ecommerce/app.yaml --workflow p0-web-regression
e2e-agent acceptance
```
