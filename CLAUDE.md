# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

**星火计划 v4** — 基于 LangGraph + LiteLLM 的 AI 驱动 P0 回归测试自动化系统。  
Python 包位于 `src/e2e_agent/`，原仓资产（Playwright TS 脚本）在 `D:\huizecode\e2e-test`。

## 常用命令

```bash
# 安装依赖（推荐 uv）
uv sync --all-extras
# 或
pip install -e ".[dev]"

# 安装 Playwright 浏览器
make install-playwright        # = uv run playwright install chromium

# 运行测试
make test                      # pytest + coverage
python -m pytest tests/test_graph_smoke.py -v   # 单个测试文件
python -m pytest tests/ -k "test_graph"         # 按关键词过滤

# Schema 校验（16 个 JSON Schema 语法检查）
make validate-schemas          # = python tools/validate_schemas.py

# 铁律 CI 检查（RULE-REG-9/10）
make ci-check                  # = python tools/ci_rule_check.py

# 冒烟验证（不调用真实 API）
make smoke
```

## 架构总览

四层职责分离：

```
触发层    Python CLI (click) / Web API / Claude Code slash
编排层    LangGraph StateGraph  →  src/e2e_agent/graph/
模型层    LiteLLM Wrapper       →  src/e2e_agent/llm/
执行层    Skill Packages        →  src/e2e_agent/skills/
          Playwright Python     →  src/e2e_agent/browser/
```

### LangGraph 管道（4 Agent + 4 Gate）

```
tc_merge → [R1 Gate] → path_extract → [R2 Gate] → explore → [R3 Gate] → exec_healing → [R4 Gate] → END
```

- **Gate 状态**：`pending`（图暂停等待人审）/ `approved`（继续）/ `rejected`（回退上一节点）
- **R4 Gate**：仅汇报，始终 `pending → END`（不阻塞）
- 图在 `graph/graph.py` 中通过 `build_graph(":memory:")` 编译（测试用 MemorySaver，生产用 `build_persistent_graph()` + SQLite）
- **W1 阶段**：4 个 Agent 节点均为骨架（返回空列表），W2 填充业务逻辑

### 模型路由（RULE-REG-9 核心约束）

**所有 LLM 调用必须且只能通过 `LLMWrapper.call(agent_name, messages)`**，禁止在 `src/` 内直接 `import anthropic / openai / google.generativeai`。违规由 `tools/ci_rule_check.py` 在 CI 中捕获（AST 扫描）。

路由配置在 `config/model-routing.yaml`：

```yaml
agents:
  tc_merge:    { primary: claude-sonnet-4-6, fallback: [gpt-4o, ...] }
  path_extract:{ primary: claude-haiku-4-5,  fallback: [gpt-4o-mini] }
  explore:     { primary: claude-sonnet-4-6, fallback: [gpt-4o] }
  exec_healing:{ primary: claude-haiku-4-5,  fallback: [gpt-4o-mini, deepseek/...] }
```

### Skill Package 机制

`src/e2e_agent/skills/<name>/MANIFEST.yaml` 是每个 Skill 的唯一入口，由 `SkillPackageLoader` 发现和加载。  
**RULE-REG-10**：SKILL.md 中禁止硬编码模型名（由 `tools/ci_rule_check.py` 正则扫描）。

| Skill | 来源 | 状态 |
|---|---|---|
| mpt-ins-prd-ana | 原仓 copy | 骨架 |
| mpt-ins-tc-gen | 原仓 copy+trim（裁阶段二）| 骨架 |
| mpt-ins-ts-gen | 原仓 copy+patch（铁律重编号 A3-R*）| 骨架 |
| mpt-reg-exec | 原仓 rename+patch | 骨架 |
| mpt-reg-case-merge | 新建 | 骨架 |
| mpt-reg-path-extract | 新建 | 骨架 |

### Playwright Python 集成

两种模式（`src/e2e_agent/browser/`）：
- **subprocess 模式**（`PlaywrightTSRunner`）：`npx playwright test <spec.ts> --reporter=json` 调用原仓 TS 脚本
- **纯 Python 模式**（`BrowserSession`）：`async with BrowserSession() as s:` 异步上下文管理器，默认 H5 viewport 390×844

## 数据契约

所有 Agent 产出文件需通过对应 `schemas/*.schema.json` 校验（JSON Schema Draft-07）。  
`tools/validate_schemas.py` 检查所有 schema 文件本身的语法合法性（不是运行时校验产出）。

Agent 输出路径约定：`products/{product_id}/{agent_name}/*.json`（JSON 机器主）+ `*.md`（Markdown 人类可读）。

## 铁律重编号

旧仓规则 `RULE-TSG-R1~R17` 在 v4 中统一重编号为 `A3-R1~R14`，详见 `docs/iron-rules-remap.md`。  
新增全局规则 `RULE-REG-9`（模型无关性）和 `RULE-REG-10`（Skill 模型无关性）。

## 测试说明

- `test_graph_smoke.py`：图编译 + 节点数量 + 首次 invoke 到 R1 Gate（使用 MemorySaver，无需 SQLite）
- `test_llm_wrapper.py`：用 `unittest.mock.patch("litellm.acompletion")` mock，不调用真实 API
- `test_skill_loader.py`：用 `tmp_path` fixture 创建临时 skill 目录，含生产目录集成测试

添加新 Agent 节点后需同步更新 `test_graph_smoke.py::test_graph_has_nodes` 中的 `expected_nodes` 集合。
