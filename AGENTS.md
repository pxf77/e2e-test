# AGENTS.md

本文件只保留 coding agent 难以稳定从代码中推断、但会影响修改正确性的项目级操作规则。不要把它写成 README 副本、阶段周报或项目百科。

## 适用范围

这些规则适用于整个仓库，除非子目录中存在更具体的 `AGENTS.md`。

## 项目结构与边界

- `src/e2e_agent/workflow/`：当前 Workflow DSL 编译、运行时、Gate 和 Node Registry。
- `src/e2e_agent/domains/` 与根目录 `domains/`：Domain Pack 加载器和行业知识。Core/Workflow 不得硬编码保险、电商或 SaaS 术语。
- `src/e2e_agent/legacy/agents/`：保险四 Agent，仅用于 Legacy 产品流程和 `p0-insurance-regression` 适配。
- `src/e2e_agent/legacy/graph/`：Legacy `E2EAgentState`、R1-R4 Gate 和固定 Graph。
- `src/e2e_agent/legacy/skills/`：Legacy Skill Packages；发现和执行必须通过 `SkillPackageLoader`。
- `src/e2e_agent/legacy/browser/`：Legacy Playwright Python 会话和 TypeScript subprocess 兼容层。
- `src/e2e_agent/llm/`：唯一允许的 LLM 集成边界。
- `schemas/v1/`：Legacy Agent/Skill 契约；`schemas/v2/`：当前框架契约。禁止未版本化根级 Schema。
- `config/`：只允许框架级模型路由和 Legacy Gate 运维策略。领域规则属于 `domains/<domain>/`。
- `tests/`：`unit/`、`integration/`、`compatibility/`、`acceptance/`；禁止根级 `test_*.py`。
- `tools/`：`validate/`、`diagnostics/`、`legacy/` 和 `acceptance.py`。根级脚本仅在明确保留兼容入口时存在。

## 环境与安装

- 推荐：`uv sync --all-extras`；无 `uv` 时使用 `python -m pip install -e ".[dev]"`。
- 安装 Python Playwright 浏览器：`python -m playwright install chromium`。
- 只有执行 TypeScript spec 时才运行 `npm ci`。
- 不要手工编辑 `uv.lock` 或 `package-lock.json`；修改依赖后用对应锁工具生成。

## 必需检查

按影响范围运行，发布前必须全量通过：

```bash
uv lock --check
npm ci --ignore-scripts
python -m tools.validate.repository
python -m tools.validate.docs
python -m tools.validate.dependencies
python -m tools.validate.tests
python -m tools.validate.schemas
python -m tools.validate.legacy
python -m tools.validate.domains
python -m tools.validate.workflows
python -m tools.validate.runners
python -m tools.validate.plugins
python -m tools.validate.rules
python -m tools.validate.boundaries
python -m pytest tests/ -q --tb=short
python -m tools.acceptance
make package-smoke
```

常用定向命令：

```bash
python -m pytest tests/unit -q
python -m pytest tests/integration -q
python -m pytest tests/compatibility -q
python -m pytest tests/acceptance -q
```

## 测试规则

- 修改行为时添加或更新测试；修 bug 必须尽量补回归测试。
- 不要为了通过测试而削弱断言、Gate、Schema、Secret 隔离或 CI 规则。
- LLM 测试必须 mock `litellm.acompletion`，不能调用真实模型 API。
- Browser/API 单测使用确定性本地 fixture 或 mock，不依赖真实账号、外部保险站点和 live key。
- Legacy 行为测试放在 `tests/compatibility/`；当前框架组件测试放在 unit/integration。

## Workflow 与 Gate

- 当前流程由 `workflows/*.yaml` 定义；新增 Workflow 不得修改 Legacy Graph。
- Legacy 固定顺序为：`tc_merge -> r1_gate -> path_extract -> r2_gate -> explore -> r3_gate -> exec_healing -> r4_gate -> END`。
- Gate 状态只能是 `pending`、`approved`、`rejected`。
- 统一命令：`e2e-agent gate status|summary|approve|reject|resume <run-id>`。
- v2 checkpoint 目录可通过 `--checkpoint-dir` 指定；Legacy 默认目录通过 `E2E_AGENT_GATE_CHECKPOINT_DIR` 覆盖。

## LLM 与模型路由

- RULE-REG-9：`src/` 内禁止直接 import 模型供应商 SDK。所有调用必须通过 `LLMWrapper`。
- 模型选择和 fallback 只维护在 `config/model-routing.yaml`。
- 新增 LLM 路由时同步配置和 mock 测试。

## Domain、Skill 与 Contract

- 行业页面类型、流程链、状态机、state deps、断言和数据画像必须进入 Domain Pack。
- 每个 Legacy Skill 从 `src/e2e_agent/legacy/skills/<name>/MANIFEST.yaml` 发现。
- RULE-REG-10：`SKILL.md` 中禁止硬编码模型名。
- JSON 输出必须匹配显式 `name@v1` 或 `name@v2` Contract。
- 下游事实来源是 JSON；Markdown 只用于人工摘要。
- 新持久化 Artifact 必须登记 producer、contract、path 和 SHA-256。

## Browser 与执行

- `BrowserSession` 默认 H5 viewport 为 `390x844`；PC 流程必须显式传入 viewport。
- `PlaywrightTSRunner` 通过本地 Playwright CLI 执行 spec；路径必须从仓库根或产物根可解析。
- 测试不得依赖保活浏览器、真实 profile 或人工操作。

## 依赖管理

- Python 依赖写入 `pyproject.toml`；Node 依赖只服务 Playwright TypeScript 兼容层。
- 新增生产依赖前优先复用标准库和现有依赖，并补依赖所有权验证。
