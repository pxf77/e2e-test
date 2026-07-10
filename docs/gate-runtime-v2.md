# v2 Gate Runtime

v2 Workflow 在 `human_required` Gate 进入 `pending` 时，会将完整运行状态写入：

```text
.local/e2e-agent/gate-checkpoints/<run-id>.v2.json
```

## 查看状态

```bash
e2e-agent gate-v2 status <run-id>
```

## 批准并恢复

```bash
e2e-agent gate-v2 approve <run-id> --operator qa --note "reviewed"
e2e-agent gate-v2 resume <run-id>
```

## 驳回并恢复

```bash
e2e-agent gate-v2 reject <run-id> --operator qa --note "revise paths"
e2e-agent gate-v2 resume <run-id>
```

驳回时 Runtime 会从 Workflow DSL 中查找 `on: rejected` 的目标节点，重新执行修订节点。修订完成后 Gate 会重新进入 `pending`，而不是复用上次驳回状态形成死循环。

## 自定义 checkpoint 目录

所有命令都支持：

```bash
--checkpoint-dir /path/to/checkpoints
```

恢复过程会保留 `metadata.gate_history`，用于审计每次人工决策。
