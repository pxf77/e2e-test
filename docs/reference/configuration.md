# 配置所有权

框架配置按责任域维护，禁止在多个目录复制同一份业务规则。

## 全局框架配置

```text
config/
  gate-operator.yaml
  model-routing.yaml
```

- `model-routing.yaml` 管理模型供应商、fallback、超时和预算。
- `gate-operator.yaml` 管理 Legacy R1-R4 流程的通用运营名称、阻塞属性、SLA 和当前 CLI 命令；不包含行业状态或断言规则。

## Domain Pack 配置

行业语义必须保存在：

```text
domains/<domain>/
  ontology.yaml
  state-machine.yaml
  state-deps.yaml
  assertion-pack.yaml
  data-pack.yaml
```

保险兼容流程默认读取 `domains/insurance/state-deps.yaml` 和 `domains/insurance/assertion-pack.yaml`。不得重新创建 `config/state-deps.yaml`、`config/assertion-templates.yaml` 或集中式 `config/skill-manifest.yaml`。

## Skill、Runner 与 Gate

- Skill 通过 `src/e2e_agent/legacy/skills/<skill>/MANIFEST.yaml` 自动发现。
- Runner 通过 `runners/*.yaml`、App Pack execution 配置和运行时 overrides 解析。
- Gate 的 operator、note、决策和审计结果由 CLI 与 checkpoint 持久化。

## 配置优先级

```text
defaults < domain < app < environment < runtime overrides
```

业务规则必须属于 Domain Pack；运行时 override 只用于本次执行参数。

## 校验

```bash
python tools/validate_domains.py
python tools/check_domain_boundaries.py
python -m pytest tests/test_config_single_source.py -q
```
