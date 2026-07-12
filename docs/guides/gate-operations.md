# Gate 运维指南

v2 Workflow 在 `human_required` Gate 进入 `pending` 时，将完整运行状态写入：

```text
.local/e2e-agent/gate-checkpoints/<run-id>.v2.json
```

统一 CLI 会自动识别 v1/v2 checkpoint：

```bash
e2e-agent gate status <run-id>
e2e-agent gate summary <run-id>
e2e-agent gate approve <run-id> --operator qa --note reviewed
e2e-agent gate reject <run-id> --operator qa --note "revise paths"
e2e-agent gate resume <run-id>
```

`gate-v2` 在 1.x 中仍可用，但只作为弃用别名。

驳回时 Runtime 从 Workflow DSL 中查找 `on: rejected` 的目标节点并重新执行。修订完成后 Gate 会重新进入 `pending`，不会复用上次驳回状态形成死循环。

所有 v2 命令支持自定义目录：

```bash
--checkpoint-dir /path/to/checkpoints
```

恢复过程保留 `metadata.gate_history`，用于审计每次人工决策。v1 Gate 的默认目录继续通过 `E2E_AGENT_GATE_CHECKPOINT_DIR` 配置。
