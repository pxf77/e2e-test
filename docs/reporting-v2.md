# Unified Reporting v2

每个完成执行节点的 Workflow 都可通过 `builtin.report` 生成：

```text
<run-dir>/reports/
  report.json
  report.html
  junit.xml
```

三种格式均登记到 `artifact-manifest.json`，包含路径、producer、kind、SHA-256 和大小。

## JSON

机器可读事实来源，包含：

- run/app/domain/workflow
- status
- passed/failed/skipped/duration
- 标准失败分类
- Assertion Report
- Runner evidence

## HTML

用于人工快速审核，不作为下游事实来源。

## JUnit

适用于 GitHub Actions、Jenkins、GitLab CI 等测试报告集成。失败类型使用统一 taxonomy：

- `locator_broken`
- `assertion_failed`
- `business_rule_failed`
- `test_data_invalid`
- `environment_unavailable`
- `auth_failed`
- `network_error`
- `third_party_unavailable`
- `runner_error`
- `llm_generation_error`
- `contract_validation_error`
- `unknown`

## 证据关系

```text
ExecutionResult
  ├─ failures
  ├─ runner artifacts
  └─ metrics
AssertionReport
  ├─ checks
  └─ summary
        ↓
TestReport
        ↓
JSON / HTML / JUnit
        ↓
Artifact Manifest
```
