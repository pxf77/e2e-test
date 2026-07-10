# Assertion Engine v2

`builtin.assertions` 在 Runner 执行后、统一报告生成前运行 Domain Assertion Pack。

## Workflow

```text
prepare_data -> execute -> assertions -> report
```

## 运行上下文

断言表达式使用 `${path.to.value}`：

```yaml
checks:
  - operator: number_equals
    actual: "${business.actual_total}"
    expected: "${expected.total}"
```

运行时可通过 `inputs.assertion_context` 提供：

```json
{
  "assertion_context": {
    "page": {},
    "business": {},
    "expected": {}
  }
}
```

## 内置 Operator

- `exists`
- `visible`
- `text_equals`
- `text_contains`
- `url_matches`
- `status_in`
- `number_equals`
- `number_between`
- `transition_allowed`
- `business_rule`

缺少实际值时检查结果为 `skipped`，不会误报失败。无法解析的操作符或业务规则也采用显式 `skipped/error` 状态，不进行自由推断。

## 强制指定模板

```json
{
  "assertion_templates": ["visible_element"]
}
```

断言输出为 `assertion_report@v2`，失败检查会合并到统一 `test_report` 状态和失败计数中。
