# Assertion Engine

`builtin.assertions` 在 Runner 执行后、统一报告生成前运行 Domain Assertion Pack。

```text
prepare_data -> execute -> assertions -> report
```

断言表达式使用 `${path.to.value}`：

```yaml
checks:
  - operator: number_equals
    actual: "${business.actual_total}"
    expected: "${expected.total}"
```

运行时可通过 `inputs.assertion_context` 提供 `page`、`business` 和 `expected` 数据。

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

缺少实际值时结果为 `skipped`，不会误报失败。无法解析的操作符或业务规则采用显式 `skipped/error`，不进行自由推断。

可通过运行输入强制指定模板：

```json
{"assertion_templates": ["visible_element"]}
```

断言输出为 `assertion-report@v2`，失败检查会合并到统一 `test_report` 状态和失败计数中。
