"""循环层 —— 五层架构的中枢，把工具/技能/记忆串成一个能自进的 agent。

一轮 run() 的六个阶段（KSTAR 闭环）：
  感知 situation  : 用任务关键词探记忆库，得到"我已知道什么"的简报
  规划 plan       : 规则规划器 + 技能库匹配，产出工具序列（不执行）
  执行 act        : 逐步解析 $prev 引用并调用工具，全程记录 action 轨迹
  结果 result     : 由规划器把末步输出转成人话；失败如实带错误信息
  反思 reflect    : 成败判定 + 证据指针，写一条情景记忆（一次任务=一个 KSTAR 回合）
  进化 evolve     : 独立入口，把重复成功轨迹蒸馏成新技能（见 skills.distill）

约定：工具本身不写记忆库，记忆写入只发生在 reflect；这样轨迹与事实不会互相污染。
"""

import json
import os
import time

from . import planner
from .memory import KStarMemory
from .skills import SkillRegistry, resolve, distill
from .tools import ToolError, ToolRegistry


class Agent(object):
    def __init__(self, root, db_path=None, skills_dir=None, trace_dir=None,
                 working_budget=32):
        self.root = os.path.abspath(root)
        self.memory = KStarMemory(db_path or os.path.join(self.root, "data", "memory.sqlite3"),
                                  working_budget=working_budget)
        self.tools = ToolRegistry(self.root)
        self.tools.memory = self.memory
        self.skills = SkillRegistry(skills_dir or os.path.join(self.root, "skills"))
        self.trace_dir = trace_dir or os.path.join(self.root, "data", "traces")
        if not os.path.isdir(self.trace_dir):
            os.makedirs(self.trace_dir)
        self.tools.register(
            "skill_run",
            "调用一个已沉淀技能（技能内部再调工具）",
            {"name": "技能名", "args": "对象，技能参数"},
            self._skill_run)
        self.tools.register(
            "skill_list", "列出当前可调用技能及其程序记忆统计",
            {}, self._skill_list)
        self.run_count = 0

    # ---------------- 工具回调 ----------------
    def _skill_run(self, name, args=None):
        r = self.skills.run(self, name, dict(args or {}))
        if not r["ok"]:
            raise ToolError("skill %s failed: %s" % (name, r["error"]))
        data = dict(r["data"] or {})
        data["_skill"] = name
        data["_inner_calls"] = len(r.get("action") or [])
        return data

    def _skill_list(self):
        stats = {s["name"]: s for s in self.memory.skill_stats()}
        out = []
        for n in self.skills.names():
            sk = self.skills.skills[n]
            st = stats.get(n, {})
            out.append({"name": n, "description": sk.description,
                        "params": sorted(sk.params.keys()),
                        "declarative": sk.impl is None,
                        "calls": st.get("calls", 0),
                        "success": st.get("success", 0),
                        "avg_steps": st.get("avg_steps", 0)})
        return {"count": len(out), "skills": out}

    # ---------------- 感知 ----------------
    def _probe_key(self, task):
        m = planner.PATH_RE.search(task or "")
        if m:
            return m.group(1)
        runs = [x for x in (task or "").split() if x]
        return max(runs, key=len)[:8] if runs else ""

    def situation_brief(self, task):
        key = self._probe_key(task)
        facts = self.memory.recall(key, 3) if key else []
        eps = self.memory.search_episodes(key, 2) if key else []
        return {"probe": key, "facts_hit": len(facts), "episodes_hit": len(eps),
                "db": self.memory.stats(),
                "note": u"已知 %d 条相关事实、%d 条相关经历" % (len(facts), len(eps))}

    # ---------------- 一轮循环 ----------------
    def run(self, task, budget=8, persist_trace=True):
        t0 = time.time()
        plan = planner.plan(task, self)
        steps = plan.steps[:max(1, int(budget))]
        action, prev, ok, err = [], None, True, ""
        for st in steps:
            args = resolve(st.get("args", {}), self, {}, prev)
            r = self.tools.call(st["tool"], args)
            action.append({"tool": st["tool"], "args": _plain(args),
                           "ok": r["ok"], "error": r["error"],
                           "data": _plain(r["data"])})
            if not r["ok"]:
                ok, err = False, r["error"]
                break
            prev = r["data"] if isinstance(r["data"], dict) else {"value": r["data"]}
        data = prev or {}
        summary = planner.summarize(plan.intent, data) if ok else u"执行失败：%s" % err
        evidence = self._evidence(plan.intent, data)
        eid = self.memory.record_episode(
            task, action, summary, "success" if ok else "failed",
            situation=json.dumps(plan.situation, ensure_ascii=False),
            steps=len(action), evidence=evidence)
        self.memory.work_put("last_task", task)
        self.memory.work_put("last_result", summary)
        self.run_count += 1
        rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "task": task,
               "intent": plan.intent, "why": plan.why,
               "situation": plan.situation, "planned": plan.steps,
               "action": action, "result": summary,
               "outcome": "success" if ok else "failed",
               "episode_id": eid, "evidence": evidence,
               "agent_steps": len(action),
               "tool_calls": _count_tools(action),
               "elapsed_ms": int((time.time() - t0) * 1000)}
        if persist_trace:
            self._trace(rec)
        return rec

    def _evidence(self, intent, data):
        if not isinstance(data, dict):
            return ""
        if "factId" in data:
            return "fact#%s=%s.%s" % (data["factId"], data.get("subject"),
                                      data.get("predicate"))
        if "_skill" in data:
            return "skill:%s" % data["_skill"]
        if "facts" in data:
            return "recall:facts=%d,episodes=%d" % (len(data["facts"] or []),
                                                    len(data.get("episodes") or []))
        return ""

    def _trace(self, rec):
        p = os.path.join(self.trace_dir, "trace.jsonl")
        with open(p, "a", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    # ---------------- 进化 ----------------
    def evolve(self, min_support=2, auto_promote=True):
        cands, promoted = distill(self.memory, self.skills, min_support,
                                  auto_promote=auto_promote, ctx=self)
        return {"candidates": [{"name": c["name"], "params": sorted(c["params"]),
                                "triggers": c["triggers"],
                                "sequence": [s["tool"] for s in c["sequence"]]}
                               for c in cands],
                "promoted": [c["name"] for c in promoted],
                "skills_now": self.skills.names()}

    def close(self):
        self.memory.close()


def _count_tools(action):
    n = 0
    for a in action:
        if a["tool"] == "skill_run":
            n += 1
        else:
            n += 1
    return n


def _plain(v):
    try:
        json.dumps(v, ensure_ascii=False)
        return v
    except Exception:
        return str(v)
