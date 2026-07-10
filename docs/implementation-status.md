# 通用 E2E 实施计划完成矩阵

| 实施项 | 状态 | 主要实现 |
|---|---|---|
| Core/Domain 分层 | 完成 | `contracts/`, `workflow/`, `domains/` |
| Contract First | 完成 | `schemas/v2`, `ContractRegistry` |
| Workflow DSL | 完成 | YAML DSL → LangGraph、动态 Gate |
| Domain Pack | 完成 | generic-web、insurance、ecommerce、saas；支持继承 |
| Ontology/Page/State | 完成 | flow chains、page types、state deps、state-machine schema |
| Assertion Pack | 完成 | Assertion Engine、领域模板、assertion-report |
| Data Pack | 完成 | 7 类 Provider、脱敏、runtime_data 隔离 |
| Playwright Runner | 完成 | TS 兼容、证据收集、统一结果 |
| API Runner | 完成 | HTTP、status/JSON/body 校验、响应证据 |
| Mobile Runner | 适配层完成 | 外部 Appium command、结果解析、证据收集；不内置 Appium client |
| LLM Gateway | 完成 | LLMWrapper、集中路由、fallback、预算规则 |
| Plugin SDK | 完成 | Manifest、Python/Node 协议、Contract 校验、脚手架 |
| Artifact Manifest | 完成 | 节点、Runner、报告、Run Context、hash |
| 配置体系 | 完成 | defaults/domain/app/env/runtime 五层合并 |
| Gate Runtime | 完成 | status/approve/reject/resume、审计历史 |
| 统一 Reporting | 完成 | JSON、HTML、JUnit、failure taxonomy |
| CLI | 完成 | run、gate-v2、plugins、runners、data-providers、acceptance |
| v1 兼容 | 完成 | product-input、旧 Graph、Legacy Agent adapters |
| Demo | 完成 | generic、ecommerce、insurance、saas、api |
| CI/质量门禁 | 完成 | schema/domain/workflow/runner/plugin/boundary/test/acceptance |
| Golden 基线 | 完成 | `tests/golden/v2` |
| 文档与发布 | 完成 | architecture、SDK、migration、reporting、release |

## 明确边界

- Mobile 能力是标准化适配器，不包含设备农场、Appium Server 或供应商 SDK。
- API Runner 是 HTTP 场景执行器，不替代专业性能测试工具。
- LLM 生成能力必须继续通过 LLMWrapper，禁止插件绕过安全和契约边界。
- v1 保险 Graph 保留用于兼容；新接入优先采用 App Pack + Domain Pack + Workflow DSL。
