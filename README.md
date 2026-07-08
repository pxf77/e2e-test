# 星火计划 — AI 驱动 P0 回归测试自动化

基于 **LangGraph + LiteLLM + Playwright** 的多 Agent 编排框架，用于保险产品 P0 回归测试的用例合并、路径提取、页面探索、脚本生成、执行报告与自愈建议。

本仓库强调三件事：

- **契约优先**：Agent JSON 产物以 `schemas/` 中的 JSON Schema 为机器主契约。
- **模型无关**：业务代码不直接调用模型 SDK，统一通过 `LLMWrapper.call()` 与 `config/model-routing.yaml` 路由。
- **人工可控**：R1-R4 Gate 支持 `pending`、`approved`、`rejected`，可暂停、审核、回退和恢复。

---

## 架构简介

### 四层结构

```text
触发层   Python CLI / workflow 脚本 / Gate CLI / Codex 对话
   ↓
编排层   LangGraph StateGraph（4 Agent + 4 Gate）
   ↓
模型层   LiteLLM + LLMWrapper（primary / fallback / timeout / budget）
   ↓
执行层   SkillPackageLoader + static product package + Playwright Python/TS
   ↓
产物层   products/{product}/*.assets + schemas/*.json + artifact fingerprints
```

### 流水线

```text
tc_merge
  → r1_gate
  → path_extract
  → r2_gate
  → explore
  → r3_gate
  → exec_healing
  → r4_gate
  → END
```

四个 Agent 的职责：

- `agent1_tc_merge`：解析 PRD 与人工 P0 用例，生成候选用例、合并用例、冲突列表和 merge trace。
- `agent2_path_extract`：从合并用例构建业务流程树，枚举回归路径，并执行 page-key / state-deps 治理。
- `agent3_explore`：优先使用 static product package 与 element-set 生成 Agent4 合同，必要时通过 Playwright live/probe fallback 补齐页面信息。
- `agent4_exec`：执行生成的 Playwright 场景，输出报告、失败分类、quarantine 与建议式自愈事件。

### 主要目录

```text
src/e2e_agent/
  agents/        # 四个 Agent 节点及兼容入口
  artifacts/     # 产物路径与 artifact fingerprint
  browser/       # BrowserSession 与 PlaywrightTSRunner
  core/          # 可复用确定性业务逻辑
  graph/         # E2EAgentState、Gate 路由、图组装
  llm/           # LLMWrapper 与模型路由边界
  skills/        # SkillPackageLoader 与 6 个 Skill Package

config/          # 模型路由、Gate、Playwright、断言模板、state-deps 等配置
schemas/         # JSON Schema Draft-07 契约
tools/           # 全链路运行、schema 校验、CI 铁律检查和观测工具
products/        # 产品输入、静态产品包 fixture 与运行产物
```

---

## 本地一键启动

从 Codex 终端或普通 PowerShell 终端进入仓库根目录后执行：

```powershell
.\scripts\bootstrap.ps1
```

脚本会检查本机环境、安装项目依赖、安装 Playwright Chromium、创建本地运行目录，并打印后续命令。常用入口：

```powershell
.\.venv\Scripts\e2e-agent.exe doctor
.\.venv\Scripts\e2e-agent.exe products
.\scripts\e2e-agent-run.ps1 -ProductInput products/travel-product/plan-a/product-input.json
.\.venv\Scripts\e2e-agent.exe reports serve --port 8080
```

完整说明见 [`docs/local-one-click-workflow.md`](docs/local-one-click-workflow.md)。

外部部署、交付清单和运行前检查见 [`docs/external-deployment.md`](docs/external-deployment.md)。仓库默认不应提交 `products/**/*.assets/`、trace、video、截图、报告、IDE 状态或密钥文件。

---

## 环境准备

推荐使用 Python 3.12+。

```powershell
uv sync --all-extras
```

没有 `uv` 时：

```powershell
python -m pip install -e ".[dev]"
```

安装 Python Playwright 浏览器：

```powershell
python -m playwright install chromium
```

只有需要执行生成的 TypeScript Playwright spec 时，才安装 Node 依赖：

```powershell
npm ci
```

---

## 配置说明

### 模型路由

模型主路由和 fallback 链集中维护在：

```text
config/model-routing.yaml
```

运行前按 LiteLLM 约定配置对应供应商 API key，例如 `OPENAI_API_KEY`、`GEMINI_API_KEY`、`DEEPSEEK_API_KEY` 等。不要把 key 写入源码、README、fixture 或提交记录。

### 常用运行开关

```powershell
$env:E2E_AGENT_GATE_CHECKPOINT_DIR="D:\tmp\e2e-agent-gates"
$env:AGENT3_DISABLE_LIVE="1"
$env:AGENT4_VISIBLE_BROWSER="1"
$env:AGENT4_DISABLE_ADAPTIVE_FALLBACK="1"
```

含义：

- `E2E_AGENT_GATE_CHECKPOINT_DIR`：覆盖 Gate checkpoint 目录，适合本地隔离运行。
- `AGENT3_DISABLE_LIVE=1`：禁用 Agent3 live 浏览器探索，优先走静态/确定性路径。
- `AGENT4_VISIBLE_BROWSER=1`：执行 Agent4 时显示浏览器窗口，便于人工观察。
- `AGENT4_DISABLE_ADAPTIVE_FALLBACK=1`：关闭 Agent4 自适应 fallback，便于定位执行问题。

---

## 产品输入

全链路运行以 `product-input.json` 为入口。示例：

```json
{
  "product_id": "test-product",
  "product_name": "示例保险产品",
  "prd_path": "products/test-product/eman/product.md",
  "manual_cases_path": "products/test-product/eman/manual-cases.json",
  "entry_url": "https://example.com/product/detail",
  "agent3_mode": "live"
}
```

其中 `prd_path`、`manual_cases_path` 和 `entry_url` 需要替换为目标产品的真实输入。

当前仓库中的可用示例入口可通过以下命令查看：

```powershell
Get-ChildItem -Path products -Recurse -Filter product-input.json
```

默认全链路入口会使用：

```text
products/test-product/eman/product-input.json
```

运行产物会写入产品对应的 `.assets` 目录，常见内容包括：

- `agent1/`、`agent2/`、`agent3/`、`agent4/`：各 Agent 的 JSON 产物。
- `runs/{run_id}/`：单次运行输入、Gate 结果、Agent 结果、最终 state、summary 和 HTML 报告。
- `artifact-fingerprints.jsonl`：关键产物 hash、producer、模型路由、fallback、token 与成本信息。

---

## 运行方式

### 运行完整工作流

使用默认产品输入：

```powershell
python tools/run_full_workflow.py
```

指定产品输入：

```powershell
python tools/run_full_workflow.py --product-input products/test-product/eman/product-input.json
```

运行结束后，脚本会打印本次运行摘要。根据摘要中的 `run_id`，可进入对应 `runs/{run_id}` 目录查看详细产物和 HTML 报告。

### Gate 审核

安装包后可使用 `e2e-agent` CLI：

```powershell
e2e-agent gate status <run_id>
e2e-agent gate summary <run_id>
e2e-agent gate approve <run_id> --gate r1 --operator hz26039982 --note "用例合并通过"
e2e-agent gate reject <run_id> --gate r2 --operator hz26039982 --note "路径缺少支付分支"
e2e-agent gate resume <run_id>
```

Gate 名称固定为：

```text
r1 / r2 / r3 / r4
```

### 应用自愈建议

Agent4 会输出 `healing_events`。确认某个建议可以应用后，使用：

```powershell
e2e-agent healing apply --run-id <run_id> --event-id <event_id> --evidence-file <path-to-evidence> --operator hz26039982
```

建议先查看事件内容、证据路径和影响范围，再执行 apply。

---

## 通过 Codex 使用

Codex 不是新的运行时入口，而是通过对话帮你读取仓库规则、执行命令、分析产物和修改文件。建议在 Codex 中打开本仓库根目录后，先让它遵守 `AGENTS.md` 与本 README。

### 常用提示词

运行一次默认产品回归：

```text
请阅读 AGENTS.md 和 README.md，使用默认 product-input 执行一次完整端到端回归。
遇到 Gate pending 时先汇总当前产物和审核建议，不要自动 approve。
```

运行指定产品：

```text
请使用 products/test-product/eman/product-input.json 运行 tools/run_full_workflow.py，
完成后汇总 run_id、HTML 报告路径、R1-R4 Gate 结果、失败分类和 healing_events。
```

只做静态优先探索：

```text
请设置 AGENT3_DISABLE_LIVE=1 后运行指定 product-input，
重点检查 Agent3 产出的 page_registry、scenarios、assertion_results 和 blocked_paths。
```

查看某次运行：

```text
请根据 run_id <run_id> 查找 products/**/runs/<run_id>，
汇总 run-summary.json、agent4 报告、quarantine 和 healing_events，不要修改源码。
```

处理 Gate：

```text
请查看 run_id <run_id> 的 Gate 状态。
如果 R1/R2/R3 有 pending，请先解释审核重点，并给出 approve/reject 命令草案，等我确认后再执行。
```

处理自愈建议：

```text
请分析 run_id <run_id> 的 healing_events，
按风险从低到高列出可应用建议、证据文件和可能改动。未经我确认不要执行 healing apply。
```

### Codex 使用注意

- 让 Codex 先读取 `AGENTS.md`，它包含项目边界、测试规则、LLM 规则、产物规则和 Git 提交规范。
- 涉及真实浏览器、真实账号或外部站点时，先确认环境变量、账号状态和是否允许可视化运行。
- 不要让 Codex 写入 secrets、token、私钥、`.env` 或真实客户 PII。
- 需要提交代码时，要求 Codex 只暂存本次相关文件，并保留无关本地修改。

---

## 常用维护命令

```powershell
python tools/validate_schemas.py
python tools/ci_rule_check.py
python -m pytest tests/test_graph_smoke.py -v
python -m pytest tests/test_skill_loader.py -v
python -m pytest tests/test_llm_wrapper.py -v
python -m pytest tests/ -v --cov=src/e2e_agent --cov-report=term-missing
```

Windows 环境如果没有 `make`，直接执行 `Makefile` 中对应的 Python 命令即可。

---

## 关键约束

- `src/` 内禁止直接 import 模型 SDK，所有 LLM 调用必须通过 `LLMWrapper.call()`。
- 模型选择和 fallback 链只维护在 `config/model-routing.yaml`。
- 每个 Skill Package 必须通过 `src/e2e_agent/skills/<skill>/MANIFEST.yaml` 发现和执行。
- Agent JSON 输出是机器主契约，必须匹配 `schemas/` 中对应 schema。
- 浏览器相关单测优先使用本地 fixture 和确定性 fallback，不依赖外部保险站点、真实账号或 live API key。
- 运行报告、trace、video、screenshot、visible-runs 等输出属于运行产物，通常不作为源码提交。
