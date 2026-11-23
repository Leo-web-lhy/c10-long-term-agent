# -*- coding: utf-8 -*-
"""C10 regression tests. ASCII-only source on purpose (Windows console safety).

Run:  python -X utf8 tests/test_brain.py
"""
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from brain.loop import Agent  # noqa: E402

TMP = os.path.join(HERE, "_tmp")
T_NOTES = u"\u6e05\u70b9 sandbox/notes \u5e76\u8bb0\u5f55"
T_REPORTS = u"\u6e05\u70b9 sandbox/reports \u5e76\u8bb0\u5f55"

passed = []
failed = []


def check(cond, name, detail=""):
    if cond:
        passed.append(name)
        print("PASS %s" % name)
    else:
        failed.append((name, detail))
        print("FAIL %s | %s" % (name, detail))


def seed():
    if os.path.isdir(TMP):
        shutil.rmtree(TMP)
    notes = os.path.join(TMP, "sandbox", "notes")
    reports = os.path.join(TMP, "sandbox", "reports")
    os.makedirs(notes)
    os.makedirs(reports)
    os.makedirs(os.path.join(TMP, "skills"))
    with open(os.path.join(notes, "a.md"), "w", encoding="utf-8") as f:
        f.write("alpha\n")
    with open(os.path.join(notes, "b.md"), "w", encoding="utf-8") as f:
        f.write("beta\n")
    for i in range(3):
        with open(os.path.join(reports, "r%d.md" % i), "w", encoding="utf-8") as f:
            f.write("report %d\n" % i)


def main():
    seed()
    agent = Agent(TMP)
    try:
        # 1. tool layer: list + count
        r = agent.tools.call("fs_list", {"path": "sandbox/notes"})
        check(r.get("ok") and r["data"].get("count") == 2, "tool_fs_list_count",
              repr(r)[:200])

        # 2. sandbox escape rejected
        r = agent.tools.call("fs_read", {"path": "../escape.txt"})
        check(r.get("ok") is False and bool(r.get("error")), "tool_sandbox_reject",
              repr(r)[:200])

        # 3. semantic memory roundtrip
        agent.memory.remember("alice", "language", "chinese", confidence=0.9)
        hits = agent.memory.recall("alice")
        check(any("alice" == h.get("subject") for h in hits), "memory_recall",
              repr(hits)[:200])

        # 4. step-1 run: 2 steps, success
        a = agent.run(T_NOTES)
        check(a.get("outcome") == "success" and a.get("agent_steps") == 2,
              "run_inventory_notes", "steps=%s outcome=%s" % (a.get("agent_steps"),
                                                             a.get("outcome")))

        # 5. step-2 run on a different dir -> 2 steps again
        b = agent.run(T_REPORTS)
        check(b.get("outcome") == "success" and b.get("agent_steps") == 2,
              "run_inventory_reports", "steps=%s" % b.get("agent_steps"))

        # 6. distill promotes a skill from the two same-shape trajectories
        ev = agent.evolve(min_support=2)
        check("auto_fs_list_mem_write" in ev.get("promoted", []), "evolve_promote",
              repr(ev)[:300])
        check("auto_fs_list_mem_write" in ev.get("skills_now", []), "evolve_registered",
              repr(ev.get("skills_now"))[:200])

        # 7. distilled skill: same task now needs 1 step (no count from caller)
        c = agent.run(T_NOTES)
        check(c.get("agent_steps") == 1, "evolve_step_reduction",
              "steps=%s intent=%s" % (c.get("agent_steps"), c.get("intent")))

        # 8. distilled skill keeps working on unseen input
        d = agent.run(u"\u6e05\u70b9 sandbox/reports \u5e76\u8bb0\u5f55")
        check(d.get("outcome") == "success" and d.get("agent_steps") == 1,
              "skill_generalizes", "steps=%s" % d.get("agent_steps"))

        # 9. cost recorded, memory grows
        st = agent.memory.stats()
        check(st.get("episodes", 0) >= 4 and st.get("skills", 0) >= 1, "stats",
              repr(st)[:300])
    finally:
        agent.close()
        shutil.rmtree(TMP, ignore_errors=True)

    print("SUMMARY passed=%d failed=%d" % (len(passed), len(failed)))
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
