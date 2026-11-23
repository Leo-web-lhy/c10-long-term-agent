# AI 日志 · C10 长期智能体

记录本次"构建长期智能体"任务的协作事实：做了什么、AI 在哪一步帮上/帮倒忙、人做了哪些决定。所有数值均来自本机实跑的 `demo/RESULT.json` 与 CLI 输出，未做修饰。

## 一、任务

平台挑战 C10「Build Your Own Agent（构建你的长期智能体 / 第二大脑）」要求：

1. 五层架构（接口 / 工具 / 循环 / 技能 / 记忆），且层间契约清晰；
2. 落地长期记忆并能跨会话复用；
3. 把重复任务抽象为**可调用技能**；
4. 让系统**每次使用都在进化**（不是静态脚本）。

交付物：可运行代码 + 演示 + `AGENT.md` + 架构说明 + 本日志 + `AAR.md`。

## 二、与 AI 的协作方式

一句话：**AI 负责全部编码，人负责目标、边界和验收判断**。我的实际做法是"让 AI 写、让演示来判"，而不是"让 AI 说对不对"。

### 1. 分层推进，一次只让它改一层

五层模块是按 `memory → tools → skills → loop → cli` 的顺序逐个让 AI 写的。每个模块写完立刻跑一次最小验证（例如 `mem_write` 后立刻 `recall` 能否命中），不通过就不进入下一层。这样一旦出错，问题域只有一个模块。

**这条纪律救了两次**：`skills.py` 的蒸馏器和 `loop.py` 的规划器都是在"写完即验证"时暴露的，如果等五层拼完再联调，根因会被层层调用栈糊住。

### 2. 用"可复跑的失败"代替"看起来对"

第一版演示 `python demo/run_demo.py` 退出码 0，10 幕全部走完，`ACT01`–`ACT04` 输出正常，表面完全成功。但我要求检查进化那一幕，结果：

```
ACT05 evolve promoted=[] skills=['doc_digest','workspace_overview']
EVOLVED_COPIED []
STEP_COMPARE before=[2, 2] after=[2, 2]
```

**进化没发生**，而"退出码 0"掩盖了它。这一步是本次任务的关键转折：不看这一步，交付的会是一个"声称会进化、实际不会"的系统。

### 3. 定位根因：不猜，直接看库

AI 的第一反应是"可能是样本不够"。我没接受这个解释，直接写了个只读脚本把 `episodes` 表 dump 出来（`.scratch/c10_db_dump.py`，纯 ASCII，输出 1612 字符、9 条情景）。

结果很清楚：EP3–EP6 四条轨迹签名都是 `fs_list|mem_write`，完全够 `min_support=2`。真正原因是 `distill()` 的**判重签名只取工具名**：

```python
sig = "|".join(a.get("tool", "?") for a in action)   # 旧实现
```

而手写的 `workspace_overview` 声明的序列也恰好是 `fs_list|mem_write` —— 于是 4 条真实样本被判定为"技能已存在"而整组跳过。

**这是一个真实的语义 bug：工具名相同 ≠ 技能相同。** 参数接线、谁是常量谁是变量，才是技能的身份。

### 4. 修复：让 AI 改，但由我给出判据

我给 AI 的修复要求不是"修好它"，而是三条可验证的判据：

1. 判重必须能区分「写死 `subject='notes'` 的 `workspace_overview`」与「把 `subject` 绑到 `path` 参数的自动技能」；
2. 归纳出的技能，调用方**不提供计数**，计数必须自动来自上一步输出；
3. 回放验证不通过就不许晋升。

AI 据此重写了 `_sig_struct`（工具 + 参数键 + 取值种类 `L`/`P:名`/`R:字段`），并把判重从"入组前按工具名去重"改为"每个候选构建完成后按结构签名比对"。顺带修掉两个次级缺陷：

- `_build_candidate` 原来取**最近两条**轨迹，改成取**差异最大**的一对（`_pick_pair`）——只有差异最大才能暴露哪些位置随任务变化，否则会把碰巧相等的值固化成常量；
- 新增 `_prev_field`：某步取值等于上一步输出字段时，写成 `{"$prev": field}`，而不是让调用方传一个它根本不知道的值。

### 5. 复跑验证修复

清空 `data/demo_run` 后重跑：

```
ACT05 evolve promoted=['auto_fs_list_mem_write'] skills=['auto_fs_list_mem_write','doc_digest','workspace_overview']
ACT06 intent=skill:auto_fs_list_mem_write steps=1
ACT07 intent=skill:auto_fs_list_mem_write steps=1
STATS episodes=9 facts=7 skills=3 fts=True avg_steps=1.22
STEP_COMPARE before=[2, 2] after=[1, 1]
```

产出的 `manifest.json` 只有一个参数 `path`，`value` 为 `{"$prev":"count"}` —— 符合判据 2。

## 三、AI 帮上忙 / 帮倒忙的地方

**帮上忙**：

- 五层骨架、SQLite 三表 + FTS5、技能占位符解析，一次成型，没有返工；
- 修 bug 时能按我给的结构判据重写，而不是打补丁式的特判；
- 脚本化验证（dump 库、A/B 探测）速度快，把"猜"压缩到最少。

**帮倒忙**：

- **第一版判重按工具名**，这是典型的"看起来合理"的设计，直接把核心需求（会进化）变成了静默失效；
- **`_build_candidate` 取最近两条**，在样本相近时会归纳出无意义的常量技能；
- 早期 `plan[:max_steps-1]` 式的截断（同 C4D 踩过一次）会让最后一步规划被吃掉，属于同一类"边界算错"。

**结论**：AI 写代码的质量取决于我给不给得出**可判定的验收条件**。给"修好它"，它会给你一个自洽但不正确的实现；给"调用方不许传计数"，它就必须去实现 `$prev`。

## 四、产出物

| 路径 | 说明 |
| --- | --- |
| `brain/memory.py` | 四类记忆 + FTS5 + 技能统计 + 进化日志 |
| `brain/tools.py` | 原子工具 + 沙箱 `realpath` 校验 + 错误归一 |
| `brain/skills.py` | 技能注册/触发匹配/序列执行 + **蒸馏器** |
| `brain/loop.py` | 一轮循环、情境摘要、稳态指标、进化入口 |
| `brain/cli.py` | 命令行接口（8 个子命令） |
| `demo/run_demo.py` | 10 幕可复跑演示，输出 `demo/RESULT.json` |
| `tests/test_brain.py` | 回归测试（工具/记忆/蒸馏/回放） |
| `AGENT.md` `docs/architecture.md` `AAR.md` | 交付文档 |

## 五、人工介入

| 环节 | 人做了什么 | 为什么必须是人 |
| --- | --- | --- |
| 目标与边界 | 给定"必须真的会进化、不能是静态脚本"的验收线 | AI 倾向把"跑通"当成"完成" |
| 判定失败 | 坚持检查 `ACT05/promoted`，否决"退出码 0 即成功" | 需要判断什么才算证据 |
| 给出修复判据 | 三条可验证条件（结构判重 / 免传计数 / 回放才晋升） | 判据决定实现质量 |
| 根因取证 | 要求直接 dump 数据库，拒绝"样本不够"的猜测 | AI 会用概率解释掩盖确定 bug |
| 诚实披露 | 要求把"第一版进化没生效"如实写进日志与 AAR | 不允许把失败包装成设计选择 |
