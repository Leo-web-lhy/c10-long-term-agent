"""KSTAR 四层记忆 —— 智能体的记忆底座。

四层：
  L1 工作记忆 working    : 进程内 scratchpad，按 token 预算淘汰（不入库，跨轮次不保留）
  L2 情景记忆 episodic   : 一次任务 = 一条 KSTAR 回合（Situation / Task / Action / Result），
                           含工具轨迹、结果、成败、证据指针；FTS5 全文检索
  L3 语义记忆 semantic   : 事实与偏好（subject, predicate, value），带置信度、来源、命中次数、时间衰减
  L4 程序记忆 procedural : 技能使用统计（调用次数 / 成功率 / 平均步数），是"进化"的证据面

设计约束：
  - 只用标准库（sqlite3 / json / time），离线可跑
  - FTS5 不可用时自动回退 LIKE（不同 sqlite 构建差异）
  - 所有写入幂等：同 (subject,predicate,value) 事实去重并累加置信度
"""

import json
import os
import sqlite3
import time

SCHEMA = """
CREATE TABLE IF NOT EXISTS episodes (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL,
  task TEXT NOT NULL,
  situation TEXT DEFAULT '',
  action_json TEXT DEFAULT '[]',
  result TEXT DEFAULT '',
  outcome TEXT NOT NULL,
  steps INTEGER DEFAULT 0,
  sig TEXT DEFAULT '',
  evidence TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS facts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  subject TEXT NOT NULL,
  predicate TEXT NOT NULL,
  value TEXT NOT NULL,
  confidence REAL DEFAULT 0.6,
  source TEXT DEFAULT '',
  ts TEXT NOT NULL,
  hits INTEGER DEFAULT 0,
  UNIQUE(subject, predicate, value)
);
CREATE TABLE IF NOT EXISTS skills (
  name TEXT PRIMARY KEY,
  calls INTEGER DEFAULT 0,
  success INTEGER DEFAULT 0,
  total_steps INTEGER DEFAULT 0,
  last_used TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS evolution (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL,
  kind TEXT NOT NULL,
  detail TEXT DEFAULT '',
  metric_before REAL DEFAULT 0,
  metric_after REAL DEFAULT 0
);
"""

FTS = """
CREATE VIRTUAL TABLE IF NOT EXISTS episodes_fts
USING fts5(task, situation, content='episodes', content_rowid='id');
"""


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%S")


class KStarMemory(object):
    def __init__(self, path, working_budget=32):
        self.path = path
        d = os.path.dirname(os.path.abspath(path))
        if d and not os.path.isdir(d):
            os.makedirs(d)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self.fts = True
        try:
            self.db.executescript(FTS)
        except sqlite3.Error:
            self.fts = False
        self.db.commit()
        self._working = []
        self._budget = working_budget

    # ---------------- L1 工作记忆 ----------------
    def work_put(self, key, value):
        self._working.append((key, value))
        while len(self._working) > self._budget:
            self._working.pop(0)

    def work_get(self, key, default=None):
        for k, v in reversed(self._working):
            if k == key:
                return v
        return default

    def work_dump(self):
        return [{"k": k, "v": v} for k, v in self._working]

    # ---------------- L2 情景记忆 ----------------
    def record_episode(self, task, action, result, outcome, situation="", steps=None, evidence=""):
        """action: [{"tool":..,"args":..,"ok":bool}]"""
        if steps is None:
            steps = len(action)
        sig = "|".join(a.get("tool", "?") for a in action)
        cur = self.db.execute(
            "INSERT INTO episodes(ts,task,situation,action_json,result,outcome,steps,sig,evidence)"
            " VALUES(?,?,?,?,?,?,?,?,?)",
            (_now(), task, situation, json.dumps(action, ensure_ascii=False),
             result, outcome, steps, sig, evidence))
        eid = cur.lastrowid
        if self.fts:
            self.db.execute(
                "INSERT INTO episodes_fts(rowid, task, situation) VALUES(?,?,?)",
                (eid, task, situation))
        self.db.commit()
        return eid

    def search_episodes(self, query, k=5):
        if self.fts:
            try:
                rows = self.db.execute(
                    "SELECT e.* FROM episodes_fts f JOIN episodes e ON e.id=f.rowid"
                    " WHERE episodes_fts MATCH ? ORDER BY rank LIMIT ?",
                    (query, k)).fetchall()
                if rows:
                    return [self._ep(r) for r in rows]
            except sqlite3.Error:
                pass
        rows = self.db.execute(
            "SELECT * FROM episodes WHERE task LIKE ? OR situation LIKE ?"
            " ORDER BY id DESC LIMIT ?",
            ("%" + query + "%", "%" + query + "%", k)).fetchall()
        return [self._ep(r) for r in rows]

    def episodes(self, limit=200):
        rows = self.db.execute("SELECT * FROM episodes ORDER BY id DESC LIMIT ?",
                               (limit,)).fetchall()
        return [self._ep(r) for r in rows]

    def _ep(self, r):
        return {"id": r["id"], "ts": r["ts"], "task": r["task"],
                "situation": r["situation"], "action": json.loads(r["action_json"] or "[]"),
                "result": r["result"], "outcome": r["outcome"], "steps": r["steps"],
                "sig": r["sig"], "evidence": r["evidence"]}

    # ---------------- L3 语义记忆 ----------------
    def remember(self, subject, predicate, value, confidence=0.6, source=""):
        row = self.db.execute(
            "SELECT id, confidence FROM facts WHERE subject=? AND predicate=? AND value=?",
            (subject, predicate, value)).fetchone()
        if row:
            newc = min(0.99, float(row["confidence"]) + 0.1)
            self.db.execute("UPDATE facts SET confidence=?, ts=?, hits=hits+1 WHERE id=?",
                            (newc, _now(), row["id"]))
            self.db.commit()
            return row["id"]
        cur = self.db.execute(
            "INSERT INTO facts(subject,predicate,value,confidence,source,ts) VALUES(?,?,?,?,?,?)",
            (subject, predicate, value, confidence, source, _now()))
        self.db.commit()
        return cur.lastrowid

    def recall(self, query, k=5):
        """按关键词命中 + 置信度 + 命中次数排序（无 embedding，离线可用）。"""
        rows = self.db.execute(
            "SELECT *, hits FROM facts WHERE subject LIKE ? OR predicate LIKE ? OR value LIKE ?",
            ("%" + query + "%", "%" + query + "%", "%" + query + "%")).fetchall()
        out = []
        for r in rows:
            score = float(r["confidence"]) * (1.0 + min(r["hits"], 5) * 0.1)
            out.append({"subject": r["subject"], "predicate": r["predicate"],
                        "value": r["value"], "confidence": float(r["confidence"]),
                        "hits": r["hits"], "score": round(score, 4), "ts": r["ts"]})
        out.sort(key=lambda x: -x["score"])
        return out[:k]

    def facts(self, limit=200):
        rows = self.db.execute("SELECT * FROM facts ORDER BY confidence DESC, hits DESC LIMIT ?",
                               (limit,)).fetchall()
        return [dict(r) for r in rows]

    # ---------------- L4 程序记忆 ----------------
    def skill_hit(self, name, success, steps):
        row = self.db.execute("SELECT name FROM skills WHERE name=?", (name,)).fetchone()
        if row:
            self.db.execute(
                "UPDATE skills SET calls=calls+1, success=success+?, total_steps=total_steps+?,"
                " last_used=? WHERE name=?",
                (1 if success else 0, steps, _now(), name))
        else:
            self.db.execute(
                "INSERT INTO skills(name,calls,success,total_steps,last_used) VALUES(?,?,?,?,?)",
                (name, 1, 1 if success else 0, steps, _now()))
        self.db.commit()

    def skill_stats(self):
        rows = self.db.execute("SELECT * FROM skills ORDER BY calls DESC").fetchall()
        return [{"name": r["name"], "calls": r["calls"], "success": r["success"],
                 "avg_steps": round(r["total_steps"] / r["calls"], 2) if r["calls"] else 0,
                 "last_used": r["last_used"]} for r in rows]

    # ---------------- 进化记录 ----------------
    def log_evolution(self, kind, detail, before=0.0, after=0.0):
        self.db.execute(
            "INSERT INTO evolution(ts,kind,detail,metric_before,metric_after) VALUES(?,?,?,?,?)",
            (_now(), kind, detail, before, after))
        self.db.commit()

    def evolution(self, limit=100):
        rows = self.db.execute("SELECT * FROM evolution ORDER BY id DESC LIMIT ?",
                               (limit,)).fetchall()
        return [dict(r) for r in rows]

    def stats(self):
        q = self.db.execute
        return {
            "episodes": q("SELECT COUNT(*) c FROM episodes").fetchone()["c"],
            "facts": q("SELECT COUNT(*) c FROM facts").fetchone()["c"],
            "skills": q("SELECT COUNT(*) c FROM skills").fetchone()["c"],
            "evolution": q("SELECT COUNT(*) c FROM evolution").fetchone()["c"],
            "fts": self.fts,
            "avg_steps": round(
                (q("SELECT AVG(steps) a FROM episodes").fetchone()["a"] or 0), 2),
        }

    def close(self):
        self.db.close()
