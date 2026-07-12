# Runner SDK

Runner 将领域无关 `ExecutionPlan` 转换为统一 `ExecutionResult`。

```python
class ExecutionRunner(Protocol):
    name: str

    async def execute(self, plan: ExecutionPlan) -> ExecutionResult:
        ...

    def collect_artifacts(self, result: ExecutionResult) -> list[dict]:
        ...
```

`ExecutionResult` 必须满足 `execution-result@v2`，失败项使用统一 taxonomy。

## Runner Manifest

```yaml
id: api
version: "1.0.0"
kind: api
implementation: runner.api
capabilities: [http, json-assertion]
config:
  timeout_seconds: 30
```

Manifest 通过 `tools/validate_runners.py` 校验，并确认 `implementation` 已注册为 Workflow Runner。

## 内置 Runner

### Playwright

- Web UI / Chromium
- TypeScript spec 兼容执行
- Trace、video、screenshot、HTML report 收集

### API

```json
{
  "id": "health",
  "request": {"method": "GET", "path": "/health"},
  "expected": {"status": 200, "json": {"status": "ok"}}
}
```

```bash
e2e-agent run --app apps/demo-api/app.yaml --workflow api-contract-regression --inputs-json inputs.json
```

### Appium Adapter

Mobile Runner 通过 `mobile_fixtures.command` 调用现有原生测试项目。没有 command 时返回显式 `blocked`，不会伪造通过结果。

## 新增 Runner

1. 实现 Runner 类。
2. 新增 Workflow Node adapter。
3. 注册到 `build_default_node_registry`。
4. 新增 `runners/<id>.yaml`。
5. 增加 execution-result 与失败分类测试。
6. 运行 `python tools/validate_runners.py`。
