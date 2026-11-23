"""技能层 —— 把重复任务抽象成可调用技能，并支持自动蒸馏。

技能有两种形态：
  A. 声明式（推荐，蒸馏产物）：目录下 manifest.json 里写 sequence（工具调用序列），
     由本模块的统一执行器解释；参数用 {"$param": "path"}，上下文引用用 {"$prev": "count"}。
  B. 代码式（手写）：目录下 impl.py 暴露 run(ctx, **kwargs)。

蒸馏（evolve）算法：
  1. 从情景记忆里取所有成功回合，按工具签名（工具名序列）分组
  2. 组内样本数 >= min_support，且该签名还没有对应技能 → 成为候选
  3. 逐位对齐两条轨迹：完全一致的步骤保留；字面量不同的参数提升为技能参数；
     已是 $prev 引用的保持引用（说明它是语义引用，不是偶然取值）
  4. 生成 SKILL.md + manifest.json，用真实样本参数回放验证；通过才晋升并记进化日志

这条"轨迹 → 技能"的路径就是"系统每次使用都在进化"的机械保证：不需要人写代码。
"""

import json
import os
import re

SKILL_FILE = "manifest.json"


class Skill(object):
    def __init__(self, name, description, params, sequence=None, triggers=None,
                 impl=None, path=""):
        self.name = name
        self.description = description
        self.params = params or {}
        self.sequence = sequence or []
        self.triggers = triggers or []
        self.impl = impl
        self.path = path

    def manifest(self):
        return {"name": self.name, "description": self.description,
                "params": self.params, "triggers": self.triggers,
                "sequence": self.sequence}


class SkillRegistry(object):
    def __init__(self, root_dir):
        self.root = os.path.abspath(root_dir)
        self.skills = {}
        if not os.path.isdir(self.root):
            os.makedirs(self.root)
        self.reload()

    def reload(self):
        self.skills = {}
        for name in sorted(os.listdir(self.root)):
            d = os.path.join(self.root, name)
            if not os.path.isdir(d) or name.startswith("_"):
                continue
            mf = os.path.join(d, SKILL_FILE)
            if not os.path.isfile(mf):
                continue
            with open(mf, "r", encoding="utf-8") as f:
                m = json.load(f)
            impl = None
            impl_py = os.path.join(d, "impl.py")
            if os.path.isfile(impl_py):
                impl = _import_impl(name, impl_py)
            self.skills[m["name"]] = Skill(
                m["name"], m.get("description", ""), m.get("params", {}),
                m.get("sequence", []), m.get("triggers", []), impl, d)

    def names(self):
        return sorted(self.skills.keys())

    def match(self, task):
        """复合触发：技能的全部触发词都必须出现在任务里，得分=触发词总长度。

        单触发词技能等价于普通包含匹配；蒸馏技能通常有两个触发词
        （参数前的动词短语 + 参数后的动作短语），必须同时命中才复用，
        避免"清点 xxx"这种只带半句的请求被整段技能误吞。
        """
        best = None
        for s in self.skills.values():
            trigs = [t for t in s.triggers if t]
            if not trigs:
                continue
            if not all(t in task for t in trigs):
                continue
            score = sum(len(t) for t in trigs)
            if best is None or score > best[1]:
                best = (s, score)
        return best[0] if best else None

    def run(self, ctx, name, args=None):
        if name not in self.skills:
            raise KeyError("unknown skill: %s" % name)
        s = self.skills[name]
        args = args or {}
        missing = [p for p in s.params if p not in args]
        extra = [k for k in args if k not in s.params]
        if missing:
            raise ValueError("missing params: %s" % ",".join(missing))
        if extra:
            raise ValueError("unexpected params: %s" % ",".join(extra))
        if s.impl is not None:
            try:
                data = s.impl(ctx, **args)
            except Exception as e:
                ctx.memory.skill_hit(s.name, False, 1)
                return {"ok": False, "data": None,
                        "error": "%s: %s" % (type(e).__name__, e), "action": []}
            ctx.memory.skill_hit(s.name, True, 1)
            return {"ok": True, "data": data, "error": "",
                    "action": [{"tool": "impl:%s" % s.name, "args": args, "ok": True}]}
        out = execute_sequence(ctx, s.sequence, args)
        ctx.memory.skill_hit(s.name, out["ok"], len(s.sequence))
        return out


def _import_impl(name, path):
    import importlib.util
    spec = importlib.util.spec_from_file_location("c10_skill_" + name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return getattr(mod, "run")


def resolve(value, ctx, params, prev):
    """解析序列中的三类取值：$param / $prev / 字面量。"""
    if isinstance(value, dict):
        if "$param" in value:
            return params[value["$param"]]
        if "$prev" in value:
            if prev is None:
                raise ValueError("$prev used but no previous step")
            field = value["$prev"]
            if field not in prev:
                raise ValueError("$prev.%s not in previous data: %s" % (field, list(prev)))
            return prev[field]
        return {k: resolve(v, ctx, params, prev) for k, v in value.items()}
    if isinstance(value, list):
        return [resolve(v, ctx, params, prev) for v in value]
    return value


def execute_sequence(ctx, sequence, params):
    action = []
    prev = None
    for step in sequence:
        tool = step["tool"]
        args = resolve(step.get("args", {}), ctx, params, prev)
        r = ctx.tools.call(tool, args)
        action.append({"tool": tool, "args": _plain(args), "ok": r["ok"],
                       "error": r["error"]})
        if not r["ok"]:
            return {"ok": False, "data": None, "error": r["error"], "action": action}
        prev = r["data"] if isinstance(r["data"], dict) else {"value": r["data"]}
    return {"ok": True, "data": prev, "error": "", "action": action}


def _plain(v):
    try:
        json.dumps(v, ensure_ascii=False)
        return v
    except Exception:
        return str(v)


# ------------------------- 蒸馏 -------------------------
def distill(memory, registry, min_support=2, auto_promote=True, ctx=None):
    """把重复的成功轨迹蒸馏为声明式技能。返回 (candidates, promoted)。"""
    eps = [e for e in memory.episodes(500) if e["outcome"] == "success" and e["steps"] >= 2]
    groups = {}
    for e in eps:
        groups.setdefault(e["sig"], []).append(e)

    existing = set(_sig_struct(s.sequence) for s in registry.skills.values() if s.sequence)
    candidates, promoted = [], []
    for sig, items in sorted(groups.items()):
        if len(items) < min_support:
            continue
        a, b = _pick_pair(items)
        cand = _build_candidate(sig, a, b, support=len(items))
        if cand is None:
            continue
        struct = _sig_struct(cand["sequence"])
        if struct in existing:
            continue
        candidates.append(cand)
        if auto_promote and ctx is not None:
            if _validate(ctx, cand, a):
                _write_skill(registry.root, cand)
                registry.reload()
                existing.add(struct)
                memory.log_evolution(
                    "skill_promoted", "sig=%s name=%s support=%d" % (
                        sig, cand["name"], len(items)),
                    before=float(a["steps"]), after=1.0)
                promoted.append(cand)

    if candidates and not promoted:
        memory.log_evolution("distill_candidate", "found=%d none_promoted" % len(candidates))
    return candidates, promoted


def _sig_struct(sequence):
    """结构签名：工具名 + 参数键 + 取值种类（字面量 L / 参数 P:名 / 上一步引用 R:字段）。

    与只比工具名的旧签名不同，同工具、不同参数接线的两个技能不会被判为重复
    （例如 workspace_overview 写死 subject，而蒸馏出的清点技能把 subject 绑到 path 参数）。
    """
    parts = []
    for step in sequence:
        items = []
        args = step.get("args") or {}
        for k in sorted(args):
            v = args[k]
            if isinstance(v, dict) and "$param" in v:
                kind = "P:" + str(v["$param"])
            elif isinstance(v, dict) and "$prev" in v:
                kind = "R:" + str(v["$prev"])
            else:
                kind = "L"
            items.append("%s=%s" % (k, kind))
        parts.append("%s(%s)" % (step.get("tool"), ",".join(items)))
    return "|".join(parts)


def _pick_pair(items):
    """挑差异最大的两条轨迹作为归纳样本。

    取最后两条常常字面量相同（如两个目录都恰好 2 个文件），会把应当参数化的取值
    误判成常量；差异最大的样本对才能暴露"哪些位置随任务变化"。
    """
    best, best_score = None, -1
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            sc = _diff_score(items[i]["action"], items[j]["action"])
            if sc > best_score:
                best, best_score = (items[i], items[j]), sc
    if best is None:
        return items[-1], items[-1]
    return best


def _diff_score(act_a, act_b):
    sc = 0
    for sa, sb in zip(act_a, act_b):
        keys = set((sa.get("args") or {}).keys()) | set((sb.get("args") or {}).keys())
        for k in keys:
            if (sa.get("args") or {}).get(k) != (sb.get("args") or {}).get(k):
                sc += 1
    return sc


def _step_data(ep, idx):
    act = ep.get("action") or []
    if 0 <= idx < len(act):
        return act[idx].get("data")
    return None


def _prev_field(ep_a, ep_b, idx, va, vb):
    """若本步的字面量等于上一步输出的某个整数字段，则它应当写成 $prev 引用。

    这是把"清点后把数量写进记忆"这类两步链路自动接上的关键：
    样本里斯文值 3/2 恰好等于 fs_list 返回的 count，于是归纳出 $prev.count，
    蒸馏出的技能就不需要调用方手填计数。
    """
    if idx <= 0:
        return None
    da = _step_data(ep_a, idx - 1)
    db = _step_data(ep_b, idx - 1)
    if not isinstance(da, dict) or not isinstance(db, dict):
        return None
    for key in sorted(set(da) & set(db)):
        va_k, vb_k = da[key], db[key]
        if isinstance(va_k, bool) or isinstance(vb_k, bool):
            continue
        if isinstance(va_k, int) and isinstance(vb_k, int) and va_k == va and vb_k == vb:
            return key
    return None


def _bound_param(bound, va, vb):
    """同一取值在两处出现 → 绑定到同一个参数，而不是各建一个哑参数。"""
    for name, pa, pb in bound:
        if isinstance(pa, (dict, list)) or isinstance(pb, (dict, list)):
            continue
        if pa == va and pb == vb:
            return name
    return None


def _build_candidate(sig, a, b, support=2):
    tools = [x["tool"] for x in a["action"]]
    if [x["tool"] for x in b["action"]] != tools:
        return None
    params = {}
    sequence = []
    bound = []
    for i, sa in enumerate(a["action"]):
        sb = b["action"][i]
        if not (sa["ok"] and sb["ok"]):
            return None
        args = {}
        keys = set((sa.get("args") or {}).keys()) | set((sb.get("args") or {}).keys())
        for k in sorted(keys):
            va = (sa.get("args") or {}).get(k)
            vb = (sb.get("args") or {}).get(k)
            if _is_ref(va):
                args[k] = va
                continue
            if va == vb:
                args[k] = va
                continue
            pname = _bound_param(bound, va, vb)
            if pname:
                args[k] = {"$param": pname}
                continue
            field = _prev_field(a, b, i, va, vb)
            if field:
                args[k] = {"$prev": field}
                continue
            pname = k if k not in params else "%s_%d" % (k, i)
            params[pname] = {"type": "int" if isinstance(va, int) else "string",
                             "example": va}
            args[k] = {"$param": pname}
            bound.append((pname, va, vb))
        sequence.append({"tool": sa["tool"], "args": args})
    name = "auto_" + _slug("_".join(tools))
    return {
        "name": name,
        "description": "自动蒸馏技能：按顺序执行 %s，由 %d 条历史成功轨迹归纳" % (
            " -> ".join(tools), support),
        "params": params,
        "triggers": _derive_triggers(a.get("task", ""), b.get("task", "")),
        "sequence": sequence,
        "sample": {p: v["example"] for p, v in params.items()},
    }


def _derive_triggers(t1, t2):
    """从两次同类任务的措辞里取"参数之外的公共片段"作为复合触发词。

    做法：最长公共前缀 + 最长公共后缀，再剪掉属于参数值的字符
    （参数值往往含字母数字、斜杠、点划线，剪掉后剩下的就是动词短语）。
    """
    if not t1 or not t2:
        return []
    n = min(len(t1), len(t2))
    i = 0
    while i < n and t1[i] == t2[i]:
        i += 1
    pre = t1[:i]
    while pre and (pre[-1].isalnum() or pre[-1] in u"/\\._-"):
        pre = pre[:-1]
    j = 0
    while j < n and t1[len(t1) - 1 - j] == t2[len(t2) - 1 - j]:
        j += 1
    suf = t1[len(t1) - j:] if j else ""
    while suf and (suf[0].isalnum() or suf[0] in u"/\\._-"):
        suf = suf[1:]
    out = []
    for t in (pre.strip(), suf.strip()):
        if len(t) >= 2 and t not in out:
            out.append(t)
    return out


def _is_ref(v):
    return isinstance(v, dict) and (("$param" in v) or ("$prev" in v))


def _slug(s):
    s = re.sub(r"[^a-z0-9_]+", "_", s.lower())
    return re.sub(r"_+", "_", s).strip("_")


def _validate(ctx, cand, sample_ep):
    args = dict(cand["sample"])
    try:
        out = execute_sequence(ctx, cand["sequence"], args)
    except Exception:
        return False
    return bool(out["ok"])


def _write_skill(root, cand):
    d = os.path.join(root, cand["name"])
    if not os.path.isdir(d):
        os.makedirs(d)
    mf = {"name": cand["name"], "description": cand["description"],
          "params": cand["params"], "triggers": cand["triggers"],
          "sequence": cand["sequence"]}
    with open(os.path.join(d, SKILL_FILE), "w", encoding="utf-8", newline="\n") as f:
        json.dump(mf, f, ensure_ascii=False, indent=2)
        f.write("\n")
    md = "# %s\n\n%s\n\n## 参数\n\n" % (cand["name"], cand["description"])
    for p, spec in cand["params"].items():
        md += "- `%s`（string，示例 `%s`）\n" % (p, spec.get("example", ""))
    md += "\n## 执行序列\n\n"
    for i, s in enumerate(cand["sequence"], 1):
        md += "%d. `%s` %s\n" % (i, s["tool"], json.dumps(s["args"], ensure_ascii=False))
    md += "\n> 本文件由 `brain/skills.py:distill()` 自动生成，来源为情景记忆中的重复成功轨迹。\n"
    with open(os.path.join(d, "SKILL.md"), "w", encoding="utf-8", newline="\n") as f:
        f.write(md)
