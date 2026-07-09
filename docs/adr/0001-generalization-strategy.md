# ADR-0001: 通用 E2E 回归框架改造策略

## 状态

Accepted

## 背景

当前仓库已经具备可复用的 AI 回归测试骨架：LangGraph 编排、Gate 审核、JSON Schema 契约、LLMWrapper 模型边界、Skill Package 与 Playwright 执行能力。限制通用性的主要问题是保险领域语义分散在 Agent、全局配置和技能包中。

## 决策

采用渐进式重构，不推倒重写。改造方向为：

1. **Core/Domain 分层**：Core 只保留契约、运行时、工作流、插件、执行器、报告等行业无关能力；保险、电商、SaaS 等行业语义进入 Domain Pack。
2. **Contract First**：所有跨模块输入输出必须有 JSON Schema，产物必须声明契约版本。
3. **Workflow First**：固定 Graph 演进为 YAML Workflow DSL，由编译器生成 LangGraph 应用。
4. **Plugin First**：Agent、Skill、Runner、Assertion、Data Provider 均通过注册表与 Manifest 接入。
5. **Model Agnostic**：所有模型调用继续走 LLMWrapper 与 `config/model-routing.yaml`，禁止业务代码直接调用模型 SDK。
6. **Backward Compatible**：保留 `product-input.json` 与现有保险 P0 回归入口，新增 v2 `app.yaml` 入口。

## 非目标

- 本阶段不重写全部 Agent 逻辑。
- 本阶段不实现完整 Mobile Runner。
- 本阶段不移除现有 `config/state-deps.yaml` 和 `config/assertion-templates.yaml`，只建立迁移目标。

## 分阶段落地

1. 增加 v2 契约、Domain Pack、Workflow DSL 与基础校验工具。
2. 将保险 state deps 和 assertion templates 迁移到 `domains/insurance`。
3. 将固定 Graph 改造成 DSL 编译结果。
4. 抽象 Runner、Assertion、Data Provider 与 Plugin SDK。
5. 通过 generic-web、ecommerce、insurance 三个 demo 验证通用性。

## 验收原则

- 当前保险能力不回退。
- Core 中不新增行业硬编码。
- 新增 workflow/domain/app 能通过静态校验。
- 所有新增 Schema 通过 `tools/validate_schemas.py`。
