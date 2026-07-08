# AGENTS.md

本文件只保留 coding agent 难以稳定从代码中推断、但会影响修改正确性的项目级操作规则。不要把它写成 README 副本、阶段周报或项目百科。

## 适用范围

这些规则适用于整个仓库，除非子目录中存在更具体的 `AGENTS.md`。子目录级文件只写该区域的额外规则，不重复根文件内容。

## 项目结构与边界

- `src/e2e_agent/`：LangGraph + LiteLLM 回归测试 Agent 的 Python 包。
- `src/e2e_agent/agents/agent1_tc_merge/` 到 `agent4_exec/`：四个流水线节点，按阶段职责分离。
- `src/e2e_agent/graph/`：`E2EAgentState`、Gate 路由和图组装。测试/冒烟使用 `build_graph(":memory:")`；持久化运行使用 `build_persistent_graph()`。
- `src/e2e_agent/llm/`：唯一允许的 LLM 集成边界。
- `src/e2e_agent/skills/<skill>/MANIFEST.yaml`：每个 Skill Package 的唯一入口。发现和执行 Skill 必须通过 `SkillPackageLoader`。
- `src/e2e_agent/browser/`：Playwright Python 会话，以及 generated/legacy Playwright TypeScript 脚本的 subprocess 执行兼容层。
- `src/e2e_agent/core/`：被多个 Agent 复用的确定性业务逻辑。
- `config/`：模型路由、state-deps 治理、Gate 运营、Playwright、断言模板等配置。
- `schemas/`：17 个 JSON Schema Draft-07 契约文件。
- `tools/`：schema 校验、铁律 CI 检查和 observability 工具。
- `products/`：产品样例、静态包 fixture 和生成产物。除非任务明确要求更新 fixture，否则不要把 run reports、trace、video、screenshot、visible-runs 输出当源码编辑。

## 环境与安装

- 推荐安装依赖：`uv sync --all-extras`。
- 无 `uv` 时使用：`python -m pip install -e ".[dev]"`。
- 安装 Python Playwright 浏览器：`make install-playwright` 或 `python -m playwright install chromium`。
- 只有需要执行 TypeScript spec 时才安装 Node Playwright 依赖：在仓库根目录执行 `npm ci`。
- 不要手工编辑 `uv.lock` 或 `package-lock.json`。

## 常用检查

完成代码修改前按影响范围运行检查：

- `make smoke`：图编译、Skill Loader、LLM Wrapper 冒烟检查。
- `make validate-schemas` 或 `python tools/validate_schemas.py`。
- `make ci-check` 或 `python tools/ci_rule_check.py`。
- `make test`：全量 pytest + coverage。

常用定向命令：

- `python -m pytest tests/test_graph_smoke.py -v`
- `python -m pytest tests/ -k "test_graph"`
- `python -m pytest tests/test_llm_wrapper.py -v`
- `python -m pytest tests/test_skill_loader.py -v`

Windows 环境如果没有 `make`，直接执行 `Makefile` 中对应的 Python 命令。

## 测试规则

- 修改行为时添加或更新测试，尤其是 `src/e2e_agent/core/`、图路由、schema 和 Skill Loader 相关改动。
- 修 bug 时尽量补回归测试。
- 不要为了通过测试而削弱断言、Gate、schema 校验或 CI 铁律。
- LLM 测试必须 mock `litellm.acompletion`，不能调用真实模型 API。
- 浏览器相关单测优先使用确定性 fallback 和本地 fixture，不依赖外部保险站点、真实账号、凭据或 live API key。
- 新增、删除或重命名图节点时，同步更新 `tests/test_graph_smoke.py::test_graph_has_nodes`。

## 架构规则

- 流水线顺序固定为：`tc_merge -> r1_gate -> path_extract -> r2_gate -> explore -> r3_gate -> exec_healing -> r4_gate -> END`。
- Gate 状态只能是 `pending`、`approved`、`rejected`。pending Gate 会持久化 checkpoint 供人工审核；`R4` 实际是汇报型 Gate，pending 会路由到 `END`。
- 测试或本地运行需要隔离 Gate checkpoint 时，使用 `E2E_AGENT_GATE_CHECKPOINT_DIR` 覆盖默认目录。
- 安装后 Gate CLI 为：`e2e-agent gate approve|reject <run_id> --gate r1|r2|r3|r4`，以及 `e2e-agent gate resume <run_id>`。
- 新增持久化 state 字段时，要同步 `E2EAgentState`、相关 Agent 输出、图测试和 JSON Schema。

## LLM 与模型路由

- RULE-REG-9：`src/` 内禁止直接 import 模型 SDK。所有 LLM 调用必须通过 `LLMWrapper.call(agent_name, messages, ...)`。
- 模型选择和 fallback 链只维护在 `config/model-routing.yaml`，不要写进 Agent 代码或 Skill 指令。
- `tools/ci_rule_check.py` 会扫描 `src/` 中的直接模型 SDK import。
- 新增使用 LLM 的 Agent 时，必须在 `config/model-routing.yaml` 增加对应路由 key，并补测试覆盖。

## Skill Package 规则

- 每个 Skill 从 `src/e2e_agent/skills/<name>/MANIFEST.yaml` 发现。
- 修改 Skill 契约时，同步 `MANIFEST.yaml`、entry script、input schema、output schema 和测试。
- RULE-REG-10：`SKILL.md` 中禁止硬编码模型名；模型路由统一走 `config/model-routing.yaml`。
- 当 Skill entry 不可用时，可以保留 `src/e2e_agent/core/` 的确定性 fallback；但现有 Agent 已经以 Skill Package 路径为主的地方，不要绕开 Loader 直接调用脚本。

## 产物契约

- Agent JSON 输出是机器主契约，必须匹配 `schemas/` 中对应 schema。
- Markdown 摘要可以辅助人工评审，但下游 Agent 的事实来源仍是 JSON。
- 写入产品/Agent 产物时使用 `src/e2e_agent/artifacts/paths.py` 的 helper，保持 `products/{product_id}` 与 `*.assets` 目录约定。
- 新产物如果进入人工评审或下游 state，必须通过 `append_artifact_fingerprint` 记录 fingerprint。
- `products/test-product/automation/` 是 static-first 测试 fixture；更新它时必须有定向测试支撑。

## Browser 与执行

- `BrowserSession` 默认 H5 viewport 为 `390x844`；PC 流程必须显式传入 viewport。
- `PlaywrightTSRunner` 通过本地 Playwright CLI 执行 spec；生成的 spec 路径必须能从仓库根或产品产物根解析。
- 常用本地开关包括 `AGENT3_DISABLE_LIVE=1`、`AGENT3_RUN_DIR`、`AGENT4_RUN_DIR`、`AGENT4_VISIBLE_BROWSER=1`、`AGENT4_DISABLE_ADAPTIVE_FALLBACK=1`。
- 单测不要依赖 `AGENT3_KEEP_BROWSER_OPEN`、`AGENT4_KEEP_BROWSER_OPEN`、真实浏览器 profile 或人工浏览器操作。

## 依赖管理

- Python 依赖写入 `pyproject.toml`；新增依赖前优先复用现有库和标准库。
- Node 依赖只服务 Playwright TypeScript 兼容层，写入 `package.json` / `package-lock.json`。
- 未经明确同意，不要新增生产依赖。

## Git 提交规范

- Commit message 使用 `<type>(<scope>): <中文说明>`，`type` 和 `scope` 保持英文小写，`subject` 必须使用中文。
- 常用 `type` 包括 `fix`、`feat`、`test`、`docs`、`refactor`、`wip`；按实际变更选择，不要使用含糊的 `update` 或 `change`。
- 示例：`fix(agent4): 修复旅游险协议弹窗与支付回放`。
- 修改已推送提交信息时，先确认 `HEAD` 与 `origin/<branch>` 一致，再用 `git push --force-with-lease` 推送改写后的当前提交；不要使用普通 `--force`。

## 安全与隐私

- 不要提交 secrets、token、私钥、`.env` 文件或真实客户 PII。
- 运行时代码可以读取环境变量中的 API key，但不得把 key 写入源码、文档、fixture 或测试。

## 交付要求

- 改动保持小而聚焦，不做无关重构。
- 最终说明要包含改了什么、运行了哪些检查、跳过了哪些检查及原因、已知剩余风险。
- 如果全量测试存在与本次修改无关的失败，用定向测试隔离本次改动，并报告失败命令和范围，不要隐藏失败。
