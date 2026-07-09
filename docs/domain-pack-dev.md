# Domain Pack 开发指南

Domain Pack 用于承载行业或产品族知识，避免 Core Engine 硬编码业务语义。

## 最小结构

```text
domains/<domain-id>/
  domain.yaml
  ontology.yaml
  state-deps.yaml
  assertion-pack.yaml
  data-pack.yaml
```

## `domain.yaml`

`domain.yaml` 必须满足 `schemas/v2/domain-pack.schema.json`：

```yaml
id: ecommerce
name: Ecommerce E2E Regression Domain
version: "1.0.0"
extends:
  - generic-web
ontology: ontology.yaml
state_deps: state-deps.yaml
assertion_pack: assertion-pack.yaml
data_pack: data-pack.yaml
supported_workflows:
  - p0-web-regression
```

## Ontology

Ontology 定义页面类型和业务意图：

```yaml
page_types:
  checkout:
    keywords: [checkout, 结算]
business_intents:
  payment:
    keywords: [payment, 支付]
```

Agent 只能通过 `DomainResolver` 使用这些语义，不应在 Core 中判断具体行业词。

## State Deps

`state-deps.yaml` 定义页面复用 key 需要感知的状态变量，用于控制状态爆炸和路径复用边界。

## Assertion Pack

断言模板必须归入领域包。Core 只提供通用 operator 和执行协议。

## 校验

```bash
python tools/validate_domains.py
e2e-agent domains --json
e2e-agent validate domain ecommerce
```
