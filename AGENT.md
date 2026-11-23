# C10 · 长期智能体（第二大脑）

一句话：一个五层结构、**每次使用都会自我进化**的本地智能体 —— 工具可插拔、记忆跨会话、重复任务自动从历史轨迹里蒸馏成技能，全程不调用云端模型。

- 入口：`python -m brain.cli <command>`
- 演示：`python demo/run_demo.py`（10 幕，可复跑，纯本地 SQLite）
- 架构细节：[`docs/architecture.md`](docs/architecture.md)
- 复盘：[`AAR.md`](AAR.md)　协作记录：[`AI日志.md`](AI日志.md)

---

## 1. 五层架构

| 层 | 文件 | 职责 | 对外接口 |
| --- | --- | --- | --- |
| 接口层 | `brain/cli.py` | 参数解析 → 装配 → 打印 UTF-8 JSON，零业务逻辑 | `run` `plan` `evolve` `skills` `tools` `facts` `episodes` `stats` |
| 循环层 | `brain/loop.py` | 一轮「感知 → 计划 → 执行 → 落盘」；注册 `skill_run`/`skill_list` 工具；稳态指标 | `Agent.run()` `Agent.evolve()` `Agent.situation_brief()` |
| 技能层 | `brain/skills.py` | 技能注册/触发匹配/序列执行；声明式技能与代码式技能统一入口；**蒸馏器** | `SkillRegistry.match()/run()` `distill()` |
| 工具层 | `brain/tools.py` | 原子能力 + 沙箱路径校验 + 异常归一 | `ToolRegistry.call(name, args)` |
| 记忆层 | `brain/memory.py` | 四类记忆落 SQLite，FTS5 全文检索，技能统计与进化日志 | `remember/recall/episodes/skill_hit/log_evolution` |

设计约束：上层只依赖下层的**接口**，不依赖其实现。接口层不做业务判断；循环层不直接碰 SQL；工具层不关心谁调用；记忆层不知道"技能"怎么执行。

## 2. 四类记忆（全部落盘，跨会话可用）

| 记忆类型 | 承载 | 表 | 作用 |
| --- | --- | --- | --- |
| 工作记忆 | `work_put/work_get`，容量 `working_budget=32` | — | 轮次间保留 `last_task`/`last_result` 等短时状态 |
| 情景记忆 | 每轮完整轨迹（含每步参数与输出快照） | `episodes` + FTS5 `episodes_fts` | "以前怎么做过这件事"，蒸馏的原料 |
| 语义记忆 | 主体-谓词-值 事实，带置信度与来源 | `facts` | "我知道什么"，`recall()` 模糊召回 |
| 程序记忆 | 技能清单、调用次数、成功数、平均步数 | `skills` / `evolution` | "我会做什么"，进化留痕 |

关键实现：`episodes` 的 `sig` 字段存**工具名序列**，同签名轨迹即"同类任务"，是自动进化的分组依据；每条 action 里额外存 `data`（该步输出快照），使 `$prev` 这类跨步引用可以被静态归纳出来。

## 3. 进化机制：轨迹 → 技能

`python -m brain.cli evolve 2`（`2` = 同一签名最少几条成功样本）：

1. **分组**：取全部成功且步数 ≥ 2 的情景，按工具名序列 `sig` 聚类；
2. **配对**：组内挑**差异最大**的两条轨迹（差异最大才能暴露哪些位置随任务变化，避免把"碰巧相等的值"固化成常量）；
3. **归纳**：逐位对齐两步 —— 相同取值保留字面量；变化的取值升级为参数；两处出现的同一取值**绑定同一个参数**；等于上一步输出整数字段的取值写成 `$prev.<field>`；
4. **判重**：用**结构签名**（工具 + 参数键 + 取值种类）比对已有技能，只比工具名会把"同工具不同接线"误判为重复；
5. **回放验证**：用真实样本参数执行一遍，成功才写 `manifest.json` + `SKILL.md`、记 `evolution` 日志、刷新注册表。

演示里的真实产物（`data/demo_run/skills/auto_fs_list_mem_write/manifest.json`）：

```json
{"params": {"path": {"type": "string"}},
 "triggers": ["清点", "并记录"],
 "sequence": [
   {"tool": "fs_list", "args": {"path": {"$param": "path"}}},
   {"tool": "mem_write",
    "args": {"predicate": "file_count",
             "subject": {"$param": "path"},
             "value": {"$prev": "count"}}}]}
```

调用方只给一个目录，计数自动取自上一步 `fs_list` 的 `count` —— 这才是"把重复任务抽象成技能"。

## 4. 快速开始

```bash
cd C10
python -m brain.cli tools                     # 列出原子工具
python -m brain.cli stats                     # 记忆与技能总览
python -m brain.cli run "清点 sandbox/notes 并记录"
python -m brain.cli evolve 2                  # 蒸馏并晋升技能
python -m brain.cli skills                    # 技能清单（含 calls/success/avg_steps）
python -m brain.cli episodes 10               # 最近 10 条情景轨迹
python demo/run_demo.py                       # 10 幕完整演示，写 demo/RESULT.json
python tests/test_brain.py                    # 回归测试
```

所有命令输出单个 UTF-8 JSON 对象；未知子命令 → stderr + 退出码 2。

## 5. 演示证据（`demo/RESULT.json` 实测）

| 指标 | 值 |
| --- | --- |
| 沙箱种子文件 | 11 |
| 幕数 | 10（含 1 幕故意失败：读取不存在的文件） |
| 进化前晋升 | 无（`ACT05` 前只有 2 个手写技能） |
| 晋升结果 | `auto_fs_list_mem_write`，样本 2 条 |
| 同类任务步数 | 进化前 `[2, 2]` → 进化后 `[1, 1]` |
| 记忆总量 | 情景 9 条 / 事实 7 条 / 技能 3 个，FTS5 可用 |
| 平均步数 | 1.22 |

失败幕不是装饰：`读 sandbox/notes/ghost.md` 返回 `outcome=failed`，错误信息原样落进情景记忆，保证"失败也被记住"。

## 6. 已知限制

1. **规划器是规则式**（正则 + 技能优先匹配），没有 LLM 规划；好处是完全确定、可复跑，坏处是对措辞变化不鲁棒 —— 换说法可能落到更弱的兜底路径。
2. **蒸馏只归纳线性序列**，不支持分支、循环、带条件的技能。
3. **`$prev` 只在"上一步输出含同名字段"时生效**，跨多步引用（第 3 步引用第 1 步）不识别。
4. **参数类型只区分 int / string**，不校验语义（例如 `path` 是否真存在，由执行时的沙箱校验兜底）。
5. 只在 Windows + Python 3.12 实测；SQLite/FTS5 为标准库，理论跨平台，但未在 Linux/macOS 跑过，**不作跨平台声明**。
