# 配置所有权

框架配置按责任域维护，禁止在多个目录复制同一份业务规则。

## 全局框架配置

`config/` 只保存与行业无关的全局配置：

```text
config/
  gate-operator.yaml
  model-routing.yaml
```

- `model-routing.yaml` 管理模型供应商、fallback、超时和预算。
- `gate-operator.yaml` 管理旧 R1-R4 流程的通用运营名称、阻塞属性、SLA 和当前 CLI 命令；不包含行业状态或断言规则。

## Domain Pack 配置

行业语义必须保存在对应 Domain Pack：

```text
domains/<domain>/
  ontology.yaml
  state-machine.yaml
  state-deps.yaml
  assertion-pack.yaml
  data-pack.yaml
```

保险兼容流程的默认配置来自：

```text
domains/insurance/state-deps.yaml
domains/insurance/assertion-pack.yaml
```

不得重新创建以下全局副本：

```text
config/state-deps.yaml
config/assertion-templates.yaml
```

## Skill Package

Skill 通过自身目录中的 `MANIFEST.yaml` 自动发现：

```text
src/e2e_agent/skills/<skill>/MANIFEST.yaml
```

不存在集中式 `config/skill-manifest.yaml`。新增、删除或升级 Skill 时只维护包内 Manifest。

## Runner 与 Gate

Runner 能力通过 `runners/*.yaml`、App Pack execution 配置和运行时 overrides 组合解析，不维护独立的旧 Playwright YAML。

Gate 的 operator、note、决策和审计结果由 CLI 与 checkpoint 持久化；`gate-operator.yaml` 只描述框架级运营策略，不保存每次决策或人员账号。

## 配置优先级

v2 Runtime 使用以下覆盖顺序：

```text
defaults < domain < app < environment < runtime overrides
```

业务规则仍必须属于 Domain Pack；运行时 override 只用于本次执行参数，不应成为新的配置源。

## 校验

```bash
python tools/validate_domains.py
python tools/check_domain_boundaries.py
python -m pytest tests/test_config_single_source.py -q
```
