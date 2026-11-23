# C10 · 长期智能体（第二大脑）

五层架构、**每次使用都在进化**的本地智能体：工具可插拔、记忆跨会话落盘、重复任务自动从历史轨迹蒸馏成可调用技能，全程不依赖云端模型。

- 快速上手看 [`AGENT.md`](AGENT.md)
- 契约与数据流看 [`docs/architecture.md`](docs/architecture.md)
- 复盘与踩坑看 [`AAR.md`](AAR.md)，协作记录看 [`AI日志.md`](AI日志.md)

## 30 秒验证

```bash
cd C10
python -m brain.cli stats
python demo/run_demo.py          # 10 幕演示，写 demo/RESULT.json
python tests/test_brain.py       # 9 项回归断言
```

`demo/run_demo.py` 的关键输出（本机实测）：

```
ACT05 evolve promoted=['auto_fs_list_mem_write'] skills=['auto_fs_list_mem_write','doc_digest','workspace_overview']
ACT06 intent=skill:auto_fs_list_mem_write steps=1
STEP_COMPARE before=[2, 2] after=[1, 1]
STATS episodes=9 facts=7 skills=3 fts=True avg_steps=1.22
```

也就是：同类任务在进化前要 2 步（先清点、再记录，计数由第 1 步带出），蒸馏出技能后只要 1 步，且调用方不再需要提供计数。

## 目录

```
brain/     cli.py(接口) loop.py(循环) skills.py(技能+蒸馏) tools.py(工具) memory.py(记忆)
demo/      run_demo.py  10 幕演示，输出 RESULT.json
tests/     test_brain.py 工具/记忆/蒸馏/回放 回归
docs/      architecture.md 架构说明
skills/    手写技能清单（声明式 manifest.json + SKILL.md）
data/      运行期数据库与轨迹（已 gitignore，不随仓库发布）
```

## 环境

Python 3.12（仅标准库：`sqlite3`、`json`、`re`、`hashlib`、`argparse`）。已在 Windows + Python 3.12.13 实测；其他平台未验证，不作声明。

## 说明

- `data/` 下所有内容都是运行产物，仓库不包含；首次运行自动创建。
- 演示会先清空 `data/demo_run` 再跑，保证结果可复现，不受历史状态影响。
- 技能晋升需通过回放验证：执行不成功不会写入注册表。
