"""代码式技能示例：读文件 → 统计 → 落语义记忆。

技能内部同样只通过 ctx.tools 调用能力，不直接 import os / sqlite3，
保证「所有能力只有一条入口」这条架构约束在技能层也成立。
"""


def run(ctx, path):
    r = ctx.tools.call("fs_read", {"path": path, "max_chars": 1200})
    if not r["ok"]:
        raise RuntimeError(r["error"])
    s = ctx.tools.call("text_stats", {"path": path})
    if not s["ok"]:
        raise RuntimeError(s["error"])
    f = ctx.tools.call("mem_write", {"subject": path, "predicate": "line_count",
                                     "value": str(s["data"]["lines"])})
    if not f["ok"]:
        raise RuntimeError(f["error"])
    return {"path": path, "lines": s["data"]["lines"], "chars": s["data"]["chars"],
            "cjk": s["data"]["cjk"], "preview": r["data"]["text"][:200],
            "factId": f["data"]["factId"]}
