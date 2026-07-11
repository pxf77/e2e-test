# E2E Agent — 通用端到端回归自动化框架

基于 **LangGraph + LiteLLM + Playwright** 的契约优先 E2E 回归框架。框架通过 App Pack、Domain Pack、Workflow DSL、Runner、Assertion/Data Pack 与 Plugin SDK，支持跨行业 Web/API 回归，并保留原保险 P0 四 Agent 流程。

## 核心能力

- **Core/Domain 分层**：保险、电商、SaaS 等业务语义位于 `domains/`，Core 不硬编码行业流程。
- **Workflow First**：`workflows/*.yaml` 编译为 LangGraph，支持普通节点、条件边和人工 Gate。
- **Contract First**：`schemas/v2` 约束 App、Domain、Workflow、Runner、Plugin、Artifact 和报告。
- **Runner 可扩展**：内置 Playwright、HTTP API 和 Appium command adapter。
- **Assertion/Data Pack**：领域断言模板、静态/CSV/合成/API/DB/账号/Secret 数据源。
- **Plugin SDK**：自动发现 Python/Node 插件，执行前后校验输入输出契约。
- **可审计**：所有节点产物、Runner 证据、JSON/HTML/JUnit 报告进入 Artifact Manifest。
- **模型无关**：LLM 调用统一经过 `LLMWrapper` 和 `config/model-routing.yaml`。
- **兼容旧流程**：保留 `product-input.json`、旧 Graph、四 Agent 和 Skill Package。

## 快速开始

要求 Python 3.12+：

```bash
python -m pip install -e ".[dev]"
```

需要 Web UI 执行时安装 Chromium：

```bash
python -m playwright install chromium
npm ci
```

运行最小通用工作流：

```bash
e2e-agent run \
  --app apps/demo-generic-form/app.yaml \
  --workflow smoke-static-web \
  --run-id local-smoke
```

运行电商 P0 流程：

```bash
e2e-agent run \
  --app apps/demo-ecommerce/app.yaml \
  --workflow p0-web-regression \
  --run-id ecommerce-p0
```

该流程会在 required Gate 暂停。审核并恢复：

```bash
e2e-agent gate-v2 status ecommerce-p0
e2e-agent gate-v2 approve ecommerce-p0 --operator qa --note "paths reviewed"
e2e-agent gate-v2 resume ecommerce-p0
```

## API 回归

`inputs.json`：

```json
{
  "api_fixtures": {
    "base_url": "http://127.0.0.1:8089"
  },
  "api_scenarios": [
    {
      "id": "health",
      "request": {"method": "GET", "path": "/health"},
      "expected": {
        "status": 200,
        "json": {"status": "ok"}
      }
    }
  ]
}
```

运行：

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
  adapters/legacy/            # v1 四 Agent 兼容适配
  assertions/                 # Assertion Engine
  artifacts/                  # Artifact Manifest
  config/                     # YAML 与分层配置解析
  contracts/                  # Contract Registry
  data/                       # Data Providers 与脱敏
  domains/                    # Domain Pack Loader/Resolver
  llm/                        # 唯一 LLM 集成边界
  plugins/                    # Plugin SDK
  reporting/                  # JSON / HTML / JUnit
  runners/                    # Playwright / API / Mobile
  workflow/                   # DSL 编译、运行、Gate、节点注册
tests/golden/                 # 稳定输出基线
tools/                        # 校验、运行和验收工具
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

校验：

```bash
e2e-agent validate app apps/demo-ecommerce/app.yaml --workflow p0-web-regression
```

## Domain Pack

领域包可继承 `generic-web`：

```yaml
id: ecommerce
name: Ecommerce Domain
version: "1.0.0"
extends: [generic-web]
ontology: ontology.yaml
state_deps: state-deps.yaml
assertion_pack: assertion-pack.yaml
data_pack: data-pack.yaml
```

Ontology 定义：

```yaml
flow_chains:
  main_flow:
    - product_listing
    - product_detail
    - cart
    - checkout
    - payment
    - order_result
```

## Workflow DSL

```yaml
id: smoke-static-web
version: "1.0.0"
nodes:
  - id: explore
    type: agent
    implementation: builtin.explore_static
  - id: execute
    type: runner
    implementation: runner.playwright
edges:
  - from: explore
    to: execute
  - from: execute
    to: END
```

新增 Workflow 不需要修改 `graph.py`。

## Data 与断言

查看 Provider：

```bash
e2e-agent data-providers --json
```

支持：`static_json`、`csv`、`faker`、`secret_ref`、`account_pool`、`api_seed`、`db_seed`。

Secret 和账号原值仅存在于内存 `runtime_data`；持久化产物自动脱敏，原值不会进入 Run Context 或 Gate checkpoint。

Assertion Engine 支持 `${business.value}` 等路径表达式。断言结果输出为 `assertion-report@v2`，失败会进入统一报告。

## Plugin SDK

创建生产插件：

```bash
e2e-agent plugin create my-plugin --runtime python
```

默认只查看生产插件：

```bash
e2e-agent plugins --json
```

显式查看 Echo 示例：

```bash
e2e-agent plugins --path examples/plugins --json
```

执行示例插件工作流：

```bash
e2e-agent run \
  --app apps/demo-generic-form/app.yaml \
  --workflow examples/workflows/plugin-smoke.yaml \
  --plugin-dir examples/plugins
```

Workflow implementation 使用 `plugin.<id>`。插件通过 JSON stdin/stdout 协议运行，并校验 Manifest 中声明的输入输出 Contract。

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

或者：

```bash
make acceptance
```

## v1 保险流程兼容

旧入口保持可用：

```bash
e2e-agent run --product-input products/test-product/eman/product-input.json
```

旧 Gate 和 healing CLI 也继续保留。新应用优先采用：

```text
App Pack + Domain Pack + Workflow DSL + v2 Gate Runtime
```

## 文档

- [架构设计](docs/architecture-v2.md)
- [Domain Pack 开发](docs/domain-pack-dev.md)
- [Workflow DSL](docs/workflow-dsl.md)
- [Runner SDK](docs/runner-sdk.md)
- [Plugin SDK](docs/plugin-sdk.md)
- [Data Pack Runtime](docs/data-pack-runtime.md)
- [Assertion Engine](docs/assertion-engine-v2.md)
- [Gate Runtime](docs/gate-runtime-v2.md)
- [统一报告](docs/reporting-v2.md)
- [v1 → v2 迁移](docs/migration-v1-to-v2.md)

## 安全规则

- 禁止提交 Secret、token、私钥、真实客户 PII 和运行产物。
- `src/` 内禁止直接导入模型供应商 SDK；LLM 必须通过 `LLMWrapper`。
- 插件不得绕过 Contract、Artifact 和 Secret 边界。
- 自动自愈仍采用建议与证据模式，不应无审核修改生产测试资产。
