# Domain Pack 开发指南

Domain Pack 承载行业或产品族知识，避免 Core Engine 硬编码业务语义。

## 最小结构

```text
domains/<domain-id>/
  domain.yaml
  ontology.yaml
  state-machine.yaml
  state-deps.yaml
  assertion-pack.yaml
  data-pack.yaml
```

## Manifest

`domain.yaml` 必须满足 `schemas/v2/domain-pack.schema.json`：

```yaml
id: ecommerce
name: Ecommerce E2E Regression Domain
version: "1.0.0"
extends: [generic-web]
ontology: ontology.yaml
state_machine: state-machine.yaml
state_deps: state-deps.yaml
assertion_pack: assertion-pack.yaml
data_pack: data-pack.yaml
supported_workflows: [p0-web-regression]
```

## Ontology

Ontology 定义页面类型、业务意图和流程链：

```yaml
page_types:
  checkout:
    keywords: [checkout, 结算]
business_intents:
  payment:
    keywords: [payment, 支付]
flow_chains:
  payment: [product_detail, cart, checkout, payment, order_result]
```

Agent 只能通过 `DomainResolver` 使用这些语义，不应在 Core 中判断具体行业词。

## State Machine 与 State Deps

- `state-machine.yaml` 描述业务生命周期和允许转移；子领域显式声明时整体覆盖父领域状态机。
- `state-deps.yaml` 定义页面复用 key 需要感知的状态变量，用于控制状态爆炸和路径复用边界。

## Assertion Pack 与 Data Pack

断言模板和数据画像必须归入领域包。Core 只提供通用 operator、provider 和执行协议。

## 继承规则

- 字典配置深度合并。
- 列表按顺序去重合并。
- 子领域本地业务意图优先于父领域通用意图。
- 状态机不进行生命周期混合；子领域存在状态机时整体覆盖。

## 校验

```bash
python -m tools.validate.domains
e2e-agent domains --json
e2e-agent validate domain ecommerce
```
