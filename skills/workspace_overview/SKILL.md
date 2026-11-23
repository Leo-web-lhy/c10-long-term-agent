# workspace_overview

清点整个工作区（C10 根目录递归）的文件数，并写成一条语义事实
`workspace.file_count = <count>`。

## 参数

无。

## 实现形态

**声明式技能**：`manifest.json` 里只有 `sequence`，没有任何 Python 代码。
执行器逐条解释：

1. `fs_list {"path": "."}`
2. `mem_write {"subject": "workspace", "predicate": "file_count", "value": {"$prev": "count"}}`

其中 `{"$prev": "count"}` 表示取上一步输出里的 `count` 字段。

## 为什么声明式更重要

自动蒸馏（`brain/skills.py:distill`）产出的就是这个形态：机器归纳出的
工具序列可以直接落成 manifest，不需要生成、审阅、测试一段新代码，
从而让「系统自己长出技能」这件事变成可验证的低风险动作。
