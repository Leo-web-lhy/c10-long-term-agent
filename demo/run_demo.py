# -*- coding: ascii -*-
"""Deterministic demo driver for the C10 long-term agent.

All Chinese scenario text lives in demo/tasks.json (UTF-8 data file) so this
driver stays pure ASCII: on this Windows host, non-ASCII source literals in a
script that prints to the console can kill the process with 0xC0000005.

Run from the C10 dir:   python demo/run_demo.py
Writes:                 demo/RESULT.json, demo/runs/demo_<ts>.jsonl, demo/evolved/*
"""

import json
import os
import shutil
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
C10 = os.path.dirname(HERE)
sys.path.insert(0, C10)

from brain.loop import Agent  # noqa: E402


def load_spec():
    with open(os.path.join(HERE, "tasks.json"), "r", encoding="utf-8") as f:
        return json.load(f)


def fresh_run_dirs():
    run = os.path.join(C10, "data", "demo_run")
    if os.path.isdir(run):
        shutil.rmtree(run)
    os.makedirs(run)
    skills = os.path.join(run, "skills")
    shutil.copytree(os.path.join(C10, "skills"), skills)
    return run, skills


def main():
    spec = load_spec()
    run, skills_dir = fresh_run_dirs()
    ag = Agent(C10, db_path=os.path.join(run, "memory.sqlite3"),
               skills_dir=skills_dir, trace_dir=os.path.join(run, "traces"))
    rows, evo = [], []

    for item in spec["sandbox_files"]:
        r = ag.tools.call("fs_write", {"path": item["path"], "content": item["text"]})
        if not r["ok"]:
            print("SEED_FAIL %s %s" % (item["path"], r["error"]))
            return 3
    print("SEED_OK files=%d" % len(spec["sandbox_files"]))

    for act in spec["acts"]:
        if act["kind"] == "task":
            rec = ag.run(act["task"])
            rows.append({"act": act["id"], "label": act["label"], "task": act["task"],
                         "intent": rec["intent"], "agent_steps": rec["agent_steps"],
                         "outcome": rec["outcome"], "result": rec["result"],
                         "evidence": rec["evidence"], "episode_id": rec["episode_id"],
                         "elapsed_ms": rec["elapsed_ms"], "action": rec["action"]})
            print("ACT%02d intent=%-24s steps=%d outcome=%s"
                  % (act["id"], rec["intent"], rec["agent_steps"], rec["outcome"]))
        else:
            res = ag.evolve(act.get("min_support", 2))
            evo.append({"act": act["id"], "label": act["label"], "result": res})
            print("ACT%02d evolve promoted=%s skills=%s"
                  % (act["id"], res["promoted"], res["skills_now"]))

    stats = ag.memory.stats()
    skills_now = ag.tools.call("skill_list")
    evolution = ag.memory.evolution(50)
    print("STATS episodes=%(episodes)s facts=%(facts)s skills=%(skills)s fts=%(fts)s "
          "avg_steps=%(avg_steps)s" % stats)

    evolved_dir = os.path.join(HERE, "evolved")
    if os.path.isdir(evolved_dir):
        shutil.rmtree(evolved_dir)
    os.makedirs(evolved_dir)
    promoted = []
    for name in ag.skills.names():
        if name.startswith("auto_"):
            dst = os.path.join(evolved_dir, name)
            shutil.copytree(os.path.join(skills_dir, name), dst)
            promoted.append(name)
    print("EVOLVED_COPIED %s" % promoted)

    ts = time.strftime("%Y%m%d-%H%M%S")
    runs_dir = os.path.join(HERE, "runs")
    if not os.path.isdir(runs_dir):
        os.makedirs(runs_dir)
    jsonl = os.path.join(runs_dir, "demo_%s.jsonl" % ts)
    with open(jsonl, "w", encoding="utf-8", newline="\n") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    before = [r for r in rows if r["act"] in (3, 4)]
    after = [r for r in rows if r["act"] in (6, 7)]
    out = {
        "generated_at": ts,
        "agent_version": "1.0.0",
        "student": "2023108610260",
        "sandbox_files": len(spec["sandbox_files"]),
        "rows": rows,
        "evolution_runs": evo,
        "evolution_log": evolution,
        "skills": skills_now.get("data"),
        "stats": stats,
        "step_comparison": {
            "before_evolve_steps": [r["agent_steps"] for r in before],
            "after_evolve_steps": [r["agent_steps"] for r in after],
            "average_before": round(sum(r["agent_steps"] for r in before)
                                    / float(len(before)), 2) if before else 0,
            "average_after": round(sum(r["agent_steps"] for r in after)
                                   / float(len(after)), 2) if after else 0,
        },
        "promoted_skills": promoted,
        "trace_file": os.path.join(run, "traces", "trace.jsonl"),
        "runs_jsonl": jsonl,
    }
    out_path = os.path.join(HERE, "RESULT.json")
    with open(out_path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print("RESULT %s" % out_path)
    print("STEP_COMPARE before=%s after=%s"
          % (out["step_comparison"]["before_evolve_steps"],
             out["step_comparison"]["after_evolve_steps"]))
    ag.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
