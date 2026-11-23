# 架构说明（C10 长期智能体）

> 平台必交物 `*architecture*` 对应本文。代码事实以 `brain/` 下五个模块为准，本文只描述契约与数据流。

## 1. 分层与依赖方向

```
          ┌───────────────────────────────┐
          │  接口层  brain/cli.py         │  参数解析 / UTF-8 JSON 输出
          └──────────────┬────────────────┘
                         │ 只依赖 Agent.run()/evolve()/stats()
          ┌──────────────▼────────────────┐
          │  循环层  brain/loop.py        │  感知→计划→执行→落盘；跨轮状态
          └──────┬───────────────┬────────┘
                 │               │
    ┌────────────▼──────┐  ┌─────▼──────────────┐
    │ 技能层 brain/skills│  │ 记忆层 brain/memory│
    │ 注册/匹配/执行/蒸馏 │  │ 四类记忆 + FTS5     │
    └────────────┬──────┘  └────────────────────┘
                 │ 技能只是"工具序列"，不含新能力
    ┌────────────▼────────────────┐
    │  工具层  brain/tools.py      │  原子能力 + 沙箱校验 + 异常归一
    └─────────────────────────────┘
```

依赖严格单向向下：`cli → loop → {skills, memory} → tools`。`memory` 不导入 `tools`；`tools` 不导入 `loop`；`skills` 通过注入的 `ToolRegistry` 执行，不直接读写 SQL。

## 2. 一轮对话的数据流

```
task 文本
  │
  ├─► situation_brief()：读工作记忆 last_task / 最近 3 条情景 → 拼"情境摘要"
  │
  ├─► plan(task)：① 技能触发词命中 → steps=[skill_run]
  │                ② 正则命中原子意图 → 展开为工具序列
  │                ③ 兜底 → mem_write 记录未识别任务（不静默丢弃）
  │
  ├─► 逐步执行 registry.call(tool, args)
  │     每步结果 {ok, data, error} 写进 action[]（含 data 快照）
  │     ok=False 立即中止，错误原文保留
  │
  └─► record_episode(task, action, result, outcome, situation, steps, evidence)
         ├─ 成功且步数≥2 → 成为 distill 的候选原料
         └─ 若本轮调用了技能 → skill_hit(name, success, steps) 更新统计
```

`outcome` 三值：`success` / `failed` / `partial`。失败轨迹照样入库——**失败也是记忆**，是后续复盘与技能改良的依据；`distill` 只取 `success`。

## 3. 蒸馏算法（`brain/skills.py: distill`）

输入：`Memory`、`SkillRegistry`、`min_support`（默认 2）。输出：候选与已晋升名单。

1. **取样本**：`memory.episodes()` 中 `outcome=='success'` 且 `len(action)>=2` 的条目。
2. **分组**：`sig = "|".join(step.tool)`，同签名视为同类任务。工具名序列是"任务形状"的粗粒度指纹，成本低、可解释。
3. **配对**：`_pick_pair()` 在组内挑 `_diff_score` 最高的两条（逐位比较参数取值差异计数）。取"最不相似"而非"最近两条"，才能看出哪些位置随输入变化。
4. **归纳**：`_build_candidate()` 逐位对齐两条轨迹：
   - 任一侧 `ok=False` → 放弃该候选（不把失败路径固化成技能）；
   - 参数键取并集；
   - 两侧取值相同 → 保留字面量；不同 → 升级为参数 `{"$param": key}`；
   - 同一取值出现在两个位置 → `_bound_param()` 绑定为**同一个**参数；
   - 取值等于**前一步输出的某个字段** → `_prev_field()` 改写成 `{"$prev": field}`，调用方无需提供。
5. **判重**：`_sig_struct()` 生成结构签名（工具名 + 参数键 + 取值种类 `L`/`P:名`/`R:字段`）。只比工具名会把「写死 subject 的 workspace_overview」与「把 subject 绑到 path 的自动技能」误判为重复——这正是本实现修掉的真实 bug。
6. **回放验证**：用样本真实参数执行一遍候选序列，全部 `ok` 才晋升：写 `manifest.json` + `SKILL.md`，`memory.log_evolution("promote", …)`，刷新注册表；失败则降级为候选并记录原因。

## 4. 技能执行契约

声明式技能的 `sequence` 是一个工具调用模板，两种占位符：

| 占位符 | 语义 | 解析时机 |
| --- | --- | --- |
| `{"$param": "name"}` | 取自调用方参数 | 执行前一次性绑定 |
| `{"$prev": "field"}` | 取自上一步输出的 `data[field]` | 每步执行后动态取值 |

执行器 `SkillRegistry.run(name, params)` 在每步前解析占位符；上一步无该字段 → 该步 `ok=False`，错误信息写明缺失字段，不猜测默认值。

## 5. 失败语义与退出码

| 情况 | 行为 |
| --- | --- |
| 工具名不存在 | `{ok:false, error:"unknown_tool: x"}`，循环层中止本轮 |
| 沙箱越界 | 工具层 `realpath` 校验后拒绝，错误写明越界路径 |
| 上一步输出缺字段 | 该步失败，`$prev` 不兜底 |
| 任务无法识别 | 规划器兜底为 `mem_write`，`outcome=partial`，不静默返回空 |
| CLI 未知子命令 | stderr 提示可用命令，退出码 2 |
| 正常结束 | 退出码 0，stdout 单个 JSON 对象 |

## 6. 可扩展点

- **加工具**：在 `tools.py` 的 `ToolRegistry` 注册 `(name, fn, schema)`，自动获得沙箱校验与错误归一；规划器加一条正则即可被唤起。
- **加代码式技能**：实现 `Skill` 子类（`match/run`），与声明式技能共享同一注册表与统计。
- **换规划器**：循环层只要求 `plan(task) -> [steps]`，可替换为 LLM 规划而不改其他层。
- **换记忆后端**：只需保持 `record_episode/episodes/remember/recall/skill_hit` 五个方法的语义。

## 7. 不变量

1. 任何一轮都必须留下一条情景记忆（含失败轮），否则"长期"无从谈起。
2. 技能执行不引入新能力，只是工具序列的复用——技能坏了可以退回原子工具。
3. 蒸馏产物必须能回放成功才允许晋升，否则注册表会积累不可执行技能。
4. 记忆与技能都落盘在 `data/`，进程重启后 `stats` 数值不变（演示第二幕跨进程验证）。
