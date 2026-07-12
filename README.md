# E2E Agent — 通用端到端回归自动化框架

基于 **LangGraph + LiteLLM + Playwright** 的 Contract-first E2E 回归框架。通过 App Pack、Domain Pack、Workflow DSL、Runner、Assertion/Data Pack 与 Plugin SDK 支持跨行业 Web/API/Mobile 适配，并保留保险四 Agent 兼容流程。

## 核心能力

- **Core/Domain 分层**：保险、电商、SaaS 等语义位于 `domains/`，Core 不硬编码行业流程。
- **Workflow First**：`workflows/*.yaml` 编译为 LangGraph，支持条件边、人工 Gate 和跨进程恢复。
- **Contract First**：`schemas/v2` 约束 App、Domain、Workflow、Runner、Plugin、Artifact 和报告。
- **Runner 可扩展**：内置 Playwright、HTTP API 和 Appium command adapter。
- **Assertion/Data Pack**：领域断言模板与静态、CSV、合成、API、DB、账号和 Secret 数据源。
- **Plugin SDK**：Python/Node 子进程协议，执行前后校验 Contract。
- **可审计**：节点产物、Runner 证据、JSON/HTML/JUnit 报告进入 Artifact Manifest。
- **模型无关**：LLM 调用统一经过 `LLMWrapper` 和 `config/model-routing.yaml`。
- **兼容旧流程**：1.x 保留 `product-input.json`、旧 Graph、四 Agent 和 Skill Package。

## 快速开始

要求 Python 3.12+：

```bash
python -m pip install -e ".[dev]"
```

Web UI 执行需要：

```bash
python -m playwright install chromium
npm ci
```

运行通用 Smoke：

```bash
e2e-agent run \
  --app apps/demo-generic-form/app.yaml \
  --workflow smoke-static-web \
  --run-id local-smoke
```

运行电商 P0：

```bash
e2e-agent run \
  --app apps/demo-ecommerce/app.yaml \
  --workflow p0-web-regression \
  --run-id ecommerce-p0
```

审核并恢复 required Gate：

```bash
e2e-agent gate status ecommerce-p0
e2e-agent gate approve ecommerce-p0 --operator qa --note "paths reviewed"
e2e-agent gate resume ecommerce-p0
```

## API 回归

输入文件：

```json
{
  "api_fixtures": {"base_url": "http://127.0.0.1:8089"},
  "api_scenarios": [
    {
      "id": "health",
      "request": {"method": "GET", "path": "/health"},
      "expected": {"status": 200, "json": {"status": "ok"}}
    }
  ]
}
```

```bash
e2e-agent run \
  --app apps/demo-api/app.yaml \
  --workflow api-contract-regression \
  --inputs-json inputs.json
```

## 目录结构

```text
apps/                         # 应用接入包和 Demo
domains/                      # generic-web / insurance / ecommerce / saas
workflows/                    # 生产 Web、API、Mobile 工作流
runners/                      # Runner Manifest
plugins/                      # 生产 Plugin Package，可为空
examples/plugins/             # 需显式加载的教学插件
examples/workflows/           # 配套示例工作流
schemas/v2/                   # 通用契约
src/e2e_agent/
  adapters/legacy/            # 1.x Legacy 兼容适配
  assertions/                 # Assertion Engine
  artifacts/                  # Artifact Manifest
  commands/                   # 正式 CLI 入口
  config/                     # YAML 与分层配置解析
  contracts/                  # Contract Registry
  data/                       # Data Providers 与脱敏
  domains/                    # Domain Pack Loader/Resolver
  llm/                        # 唯一 LLM 集成边界
  plugins/                    # Plugin SDK
  reporting/                  # JSON / HTML / JUnit
  runners/                    # Playwright / API / Mobile
  workflow/                   # DSL 编译、运行、Gate、节点注册
docs/                         # architecture / guides / reference / sdk / releases
tests/golden/                 # 稳定输出基线
tools/                        # 校验和验收工具
```

## App Pack

```yaml
id: demo-ecommerce
name: Demo Ecommerce
domain: ecommerce
requirements:
  prd: requirements.md
  manual_cases: manual-cases.json
entrypoints:
  web:
    base_url: https://example.com
    start_url: /products/demo
execution:
  default_runner: playwright
```

```bash
e2e-agent validate app apps/demo-ecommerce/app.yaml --workflow p0-web-regression
```

## Domain Pack

```yaml
id: ecommerce
name: Ecommerce Domain
version: "1.0.0"
extends: [generic-web]
ontology: ontology.yaml
state_machine: state-machine.yaml
state_deps: state-deps.yaml
assertion_pack: assertion-pack.yaml
data_pack: data-pack.yaml
```

## Plugin SDK

创建生产插件：

```bash
e2e-agent plugin create my-plugin --runtime python
```

示例插件必须显式加载：

```bash
e2e-agent plugins --path examples/plugins --json

e2e-agent run \
  --app apps/demo-generic-form/app.yaml \
  --workflow examples/workflows/plugin-smoke.yaml \
  --plugin-dir examples/plugins
```

## 运行产物

```text
apps/<app>/.assets/runs/<run-id>/
  artifact-manifest.json
  run-context.json
  by-node/
  api/
  runner/
  reports/
    report.json
    report.html
    junit.xml
```

Artifact Manifest 记录 contract、producer、node、path、SHA-256、类型和大小。

## 质量门禁

```bash
python tools/validate_repository.py
python tools/validate_schemas.py
python tools/validate_domains.py
python tools/validate_workflows.py
python tools/validate_runners.py
python tools/validate_plugins.py
python tools/ci_rule_check.py
python tools/check_domain_boundaries.py
python -m pytest tests/ -q
python tools/acceptance_matrix.py
```

## Legacy 兼容

```bash
e2e-agent run --product-input products/test-product/eman/product-input.json
```

统一 `e2e-agent gate` 会根据 checkpoint 自动识别 v1/v2。`gate-v2` 在 1.x 中仅作为弃用别名。

## 文档

完整导航见 [docs/index.md](docs/index.md)：

- [总体架构](docs/architecture/overview.md)
- [Domain Pack](docs/architecture/domain-packs.md)
- [Workflow DSL 与 Runtime](docs/architecture/workflow-runtime.md)
- [CLI Reference](docs/reference/cli.md)
- [配置所有权](docs/reference/configuration.md)
- [Plugin SDK](docs/sdk/plugin.md)
- [Runner SDK](docs/sdk/runner.md)
- [v1 到 v2 迁移](docs/guides/migration-v1-to-v2.md)

## 安全规则

- 禁止提交 Secret、token、私钥、真实客户 PII 和运行产物。
- `src/` 内禁止直接导入模型供应商 SDK；LLM 必须通过 `LLMWrapper`。
- 插件不得绕过 Contract、Artifact 和 Secret 边界。
- 自动自愈采用建议与证据模式，不应无审核修改生产测试资产。
