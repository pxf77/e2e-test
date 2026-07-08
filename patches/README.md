# patches/ 框架

## 用途

记录每个 Skill Package 相对于原仓（`D:\huizecode\e2e-test`）的变更情况。

## 文件说明

| 文件 | 说明 |
|------|------|
| `skill-origins.json` | 每个 Skill 的来源、处置动作、补丁列表 |
| `patches/<skill-name>/*.patch` | 具体 diff 补丁（W2 填充） |

## 动作类型

| 动作 | 说明 |
|------|------|
| `copy` | 原样复制，无修改 |
| `copy+trim` | 复制后裁剪（如去掉某阶段） |
| `copy+patch` | 复制后应用补丁（如铁律重编号） |
| `rename+patch` | 重命名 + 应用补丁 |
| `new` | 全新编写，无原仓来源 |

## W2 操作指南

1. 参照 `skill-origins.json` 中各 skill 的 `origin` 路径
2. 从原仓复制文件到 `skill_packages/<name>/`
3. 应用 `patches[]` 中列出的补丁
4. 用 `git diff` 生成补丁文件存入 `patches/<skill-name>/`
