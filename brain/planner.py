"""规划器 —— 把自然语言任务翻译成可执行的工具/技能序列。

这是"循环层"的决策前半段。设计取舍：
  - 默认用**规则规划器**：离线、确定性、可回归测试；不用 0.5B 小模型硬凑 JSON
  - 规则覆盖不了时先查技能库（技能是进化的产物，优先级高于兜底）
  - 步骤参数支持两种引用：{"$prev": "field"} 取上一步输出的字段，字面量直接写
  - 规划器不执行任何工具，纯函数，便于单测
"""

import re

PATH_RE = re.compile(r"([A-Za-z0-9_\-./\\]+\.[A-Za-z0-9]{1,8}|[A-Za-z0-9_\-]+(?:[/\\][A-Za-z0-9_\-.]+)+)")
REMEMBER_RE = re.compile(u"记住[：: ]*(.+?)[ ]*(?:是|为|=)[ ]*(.+)")
RECALL_RE = re.compile(u"(?:回忆|还记得|查一下|查找)[：: ]*(.+)")


class Plan(object):
    def __init__(self, intent, steps, situation, why=""):
        self.intent = intent
        self.steps = steps
        self.situation = situation
        self.why = why

    def as_dict(self):
        return {"intent": self.intent, "steps": self.steps,
                "situation": self.situation, "why": self.why}


def _path(task, default="."):
    m = PATH_RE.search(task)
    return m.group(1) if m else default


def plan(task, agent):
    """task: 用户一句话；agent: 提供 skills 注册表的对象。"""
    t = (task or "").strip()
    situation = agent.situation_brief(t)

    # 1) 已沉淀技能优先（进化成果的复用入口）
    sk = agent.skills.match(t)
    if sk is not None:
        args = {}
        for p in sk.params:
            args[p] = _path(t)
        return Plan("skill:" + sk.name,
                    [{"tool": "skill_run", "args": {"name": sk.name, "args": args}}],
                    situation, why="命中已沉淀技能 %s 的触发词" % sk.name)

    # 2) 清点并记录（两步：列目录 → 写事实）
    if re.search(u"清点|盘点|扫描|审计", t) and re.search(u"记录|记下|保存", t):
        p = _path(t)
        return Plan("inventory_record", [
            {"tool": "fs_list", "args": {"path": p}},
            {"tool": "mem_write", "args": {"subject": p, "predicate": "file_count",
                                           "value": {"$prev": "count"}}},
        ], situation, why="清点目录并落语义记忆")

    # 3) 清点
    if re.search(u"清点|盘点|列目录|有哪些文件|目录", t):
        return Plan("inventory",
                    [{"tool": "fs_list", "args": {"path": _path(t)}}],
                    situation, why="列目录")

    # 4) 文本统计
    if re.search(u"行数|字数|字符数|统计", t):
        return Plan("text_stats",
                    [{"tool": "text_stats", "args": {"path": _path(t)}}],
                    situation, why="统计文本")

    # 5) 记事实
    m = REMEMBER_RE.search(t)
    if m:
        return Plan("remember", [{"tool": "mem_write", "args": {
            "subject": m.group(1).strip(), "predicate": "is", "value": m.group(2).strip()}}],
            situation, why="写入语义记忆")

    # 6) 回忆
    m = RECALL_RE.search(t)
    if m:
        return Plan("recall", [{"tool": "mem_search", "args": {"query": m.group(1).strip()}}],
                    situation, why="检索记忆")

    # 7) 读文件
    if re.search(u"读|打开|看看.*内容", t):
        return Plan("read", [{"tool": "fs_read", "args": {"path": _path(t)}}],
                    situation, why="读文件")

    # 8) 兜底：先检索记忆，再列工作区
    return Plan("explore", [
        {"tool": "mem_search", "args": {"query": t}},
        {"tool": "fs_list", "args": {"path": "."}},
    ], situation, why="未识别意图，先查记忆再勘察工作区")


def summarize(intent, data):
    """把工具输出转成人话；缺失字段不编造，如实说明。"""
    if not isinstance(data, dict):
        return "结果：%s" % data
    if intent == "inventory_record":
        return u"清点完成，共 %s 个文件；已记入语义记忆：%s.file_count = %s。" % (
            data.get("value"), data.get("subject"), data.get("value"))
    if intent.startswith("skill:"):
        name = intent[6:]
        if "count" in data:
            return u"技能 %s 执行完成，共 %s 个文件。" % (name, data.get("count"))
        return u"技能 %s 执行完成。" % name
    if intent == "inventory":
        return "目录内共 %s 个文件。" % data.get("count")
    if intent == "text_stats":
        return "共 %s 行、%s 字符，其中中文 %s 字。" % (
            data.get("lines"), data.get("chars"), data.get("cjk"))
    if intent == "remember":
        return "已记住：%s = %s。" % (data.get("subject"), data.get("value"))
    if intent == "recall":
        return "命中 %d 条事实、%d 条情景。" % (len(data.get("facts") or []),
                                          len(data.get("episodes") or []))
    if intent == "read":
        return "读取 %s 成功，%s 字符%s。" % (
            data.get("path"), data.get("chars"),
            "（已截断）" if data.get("truncated") else "")
    if intent == "explore":
        return "检索与勘察完成。"
    return "完成。"
