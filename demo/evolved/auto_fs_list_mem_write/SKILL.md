# auto_fs_list_mem_write

自动蒸馏技能：按顺序执行 fs_list -> mem_write，由 2 条历史成功轨迹归纳

## 参数

- `path`（string，示例 `sandbox/reports`）

## 执行序列

1. `fs_list` {"path": {"$param": "path"}}
2. `mem_write` {"predicate": "file_count", "subject": {"$param": "path"}, "value": {"$prev": "count"}}

> 本文件由 `brain/skills.py:distill()` 自动生成，来源为情景记忆中的重复成功轨迹。
