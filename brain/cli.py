"""接口层 —— 命令行入口（最小可用接口，把能力暴露给人）。

用法（在 C10 根目录下执行）：
    python -m brain.cli run "清点 sandbox/notes 并记录"
    python -m brain.cli plan "统计 README.md 的行数"
    python -m brain.cli evolve 2
    python -m brain.cli skills
    python -m brain.cli tools
    python -m brain.cli facts
    python -m brain.cli episodes 5
    python -m brain.cli stats

设计取舍：接口层不实现任何业务能力，只做「解析参数 → 调用循环层 → 打印结果」，
保证能力只有一条实现路径（换 GUI / HTTP 接口时循环层无需改动）。
"""

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def _root():
    return os.path.dirname(HERE)


def _agent():
    from .loop import Agent
    return Agent(_root())


def _dump(obj):
    text = json.dumps(obj, ensure_ascii=False, indent=2)
    out = getattr(sys.stdout, "buffer", None)
    if out is None:
        sys.stdout.write(text + "\n")
        return
    out.write((text + "\n").encode("utf-8"))
    out.flush()


USAGE = u"""C10 agent CLI

  run <任务>        执行一轮完整循环
  plan <任务>       只规划不执行
  evolve [min]      从情景记忆蒸馏技能（默认 min=2）
  skills            列出技能 + 程序记忆统计
  tools             列出工具 manifest
  facts             列出语义记忆
  episodes [n]      列出最近 n 条情景记忆
  stats             记忆库总览
"""


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(USAGE.encode("utf-8").decode("utf-8"))
        return 0
    cmd, rest = argv[0], argv[1:]
    ag = _agent()
    try:
        from . import planner
        if cmd == "run":
            _dump(ag.run(" ".join(rest)))
        elif cmd == "plan":
            p = planner.plan(" ".join(rest), ag)
            _dump(p.as_dict())
        elif cmd == "evolve":
            n = int(rest[0]) if rest else 2
            _dump(ag.evolve(n))
        elif cmd == "skills":
            _dump(ag.tools.call("skill_list")["data"])
        elif cmd == "tools":
            _dump({"count": len(ag.tools.manifests()), "tools": ag.tools.manifests()})
        elif cmd == "facts":
            _dump({"count": len(ag.memory.facts()), "facts": ag.memory.facts()})
        elif cmd == "episodes":
            n = int(rest[0]) if rest else 5
            eps = ag.memory.episodes(n)
            for e in eps:
                e["action"] = [a.get("tool") for a in e.get("action") or []]
            _dump({"count": len(eps), "episodes": eps})
        elif cmd == "stats":
            _dump(ag.memory.stats())
        else:
            sys.stderr.write("unknown command: %s\n" % cmd)
            return 2
        return 0
    finally:
        ag.close()


if __name__ == "__main__":
    sys.exit(main())
