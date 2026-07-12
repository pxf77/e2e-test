# Data Pack Runtime

Data Pack 通过 Workflow 节点 `builtin.prepare_data` 在执行前解析测试数据。

## Provider

| Provider | 用途 |
|---|---|
| `static_json` | JSON fixture |
| `csv` | CSV 指定行 |
| `faker` | 可复现的合成数据 |
| `secret_ref` | 环境变量密钥 |
| `account_pool` | 账号池 |
| `api_seed` | HTTP API 造数 |
| `db_seed` | SQLite 造数与查询 |

```bash
e2e-agent data-providers --json
```

## 选择 Profile

App Pack：

```yaml
data:
  pack: data/data-pack.yaml
  profiles: [random_user]
```

运行时输入：

```json
{"data_profiles": ["random_user", "login_secret"]}
```

## 敏感数据隔离

Provider 原始值只保存在 `WorkflowRuntimeState.runtime_data`。持久化的 `test_data` Artifact 会自动脱敏，`runtime_data` 不进入：

- `run-context.json`
- `artifact-manifest.json`
- v2 Gate checkpoint

密钥字段完全替换为 `***REDACTED***`，邮箱、手机号和证件号采用部分掩码。

## Account Pool

```yaml
profiles:
  buyer:
    provider: account_pool
    file: accounts.json
    index: 0
```

可用 `E2E_ACCOUNT_INDEX` 覆盖账号索引。
