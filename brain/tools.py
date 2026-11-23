"""工具层 —— 所有能力的唯一入口。

设计要点：
  1. 每个工具都有显式 manifest（name / description / params），供 planner 与文档共用
  2. 统一返回 {"ok": bool, "data": ..., "error": str}，循环层只认这一种形状
  3. 文件类工具强制沙箱：realpath 必须落在 root 内，越界直接拒绝（不做"尽力而为"）
  4. 工具不直接写记忆库，记忆写入由循环层在 reflect 阶段统一落库，避免工具副作用污染轨迹
"""

import json
import os
import re

CJK = re.compile(u"[\u4e00-\u9fff]")


class ToolError(Exception):
    pass


class Tool(object):
    def __init__(self, name, description, params, func):
        self.name = name
        self.description = description
        self.params = params
        self.func = func

    def manifest(self):
        return {"name": self.name, "description": self.description,
                "params": self.params}


class ToolRegistry(object):
    def __init__(self, root):
        self.root = os.path.abspath(root)
        self._tools = {}
        self._register_builtin()

    # ---------- 沙箱 ----------
    def _safe(self, path):
        if not path:
            raise ToolError("empty path")
        p = path if os.path.isabs(path) else os.path.join(self.root, path)
        rp = os.path.abspath(p)
        if rp != self.root and not rp.startswith(self.root + os.sep):
            raise ToolError("path escapes sandbox: %s" % path)
        return rp

    # ---------- 注册 ----------
    def register(self, name, description, params, func):
        self._tools[name] = Tool(name, description, params, func)

    def get(self, name):
        if name not in self._tools:
            raise ToolError("unknown tool: %s" % name)
        return self._tools[name]

    def names(self):
        return sorted(self._tools.keys())

    def manifests(self):
        return [self._tools[n].manifest() for n in self.names()]

    def call(self, name, args=None):
        args = args or {}
        try:
            tool = self.get(name)
            data = tool.func(**args)
            return {"ok": True, "data": data, "error": ""}
        except ToolError as e:
            return {"ok": False, "data": None, "error": str(e)}
        except TypeError as e:
            return {"ok": False, "data": None, "error": "bad args for %s: %s" % (name, e)}
        except Exception as e:  # 工具内部异常不向上冒泡，交给循环层决定重试/降级
            return {"ok": False, "data": None, "error": "%s: %s" % (type(e).__name__, e)}

    # ---------- 内置工具 ----------
    def _register_builtin(self):
        self.register(
            "fs_list", "列出目录下文件（递归，返回相对路径与字节数，最多 200 条）",
            {"path": "目录相对路径，默认 '.'", "limit": "int，默认 200"},
            self._fs_list)
        self.register(
            "fs_read", "读取文本文件（UTF-8，超长截断并标记）",
            {"path": "文件相对路径", "max_chars": "int，默认 4000"},
            self._fs_read)
        self.register(
            "fs_write", "在沙箱内写入文本文件（自动建父目录，覆盖写）",
            {"path": "相对路径", "content": "文本内容"},
            self._fs_write)
        self.register(
            "text_stats", "统计文本文件的行数 / 字符数 / 中文字符数",
            {"path": "文件相对路径"},
            self._text_stats)
        self.register(
            "mem_write", "写入一条语义事实（subject/predicate/value）",
            {"subject": "主体", "predicate": "关系", "value": "取值"},
            self._mem_write)
        self.register(
            "mem_search", "检索语义记忆与情景记忆",
            {"query": "关键词", "k": "int，默认 5"},
            self._mem_search)

    # 由 Agent 在构造后注入 memory 引用（避免工具层直接持有 DB 生命周期）
    memory = None

    def _fs_list(self, path=".", limit=200):
        base = self._safe(path)
        if not os.path.isdir(base):
            raise ToolError("not a directory: %s" % path)
        out = []
        for dirpath, _dirs, files in os.walk(base):
            for f in files:
                fp = os.path.join(dirpath, f)
                rel = os.path.relpath(fp, self.root).replace("\\", "/")
                out.append({"path": rel, "bytes": os.path.getsize(fp)})
                if len(out) >= int(limit):
                    break
            if len(out) >= int(limit):
                break
        out.sort(key=lambda x: x["path"])
        return {"count": len(out), "files": out}

    def _fs_read(self, path, max_chars=4000):
        p = self._safe(path)
        if not os.path.isfile(p):
            raise ToolError("not a file: %s" % path)
        with open(p, "r", encoding="utf-8", errors="replace") as f:
            text = f.read()
        return {"path": path, "chars": len(text),
                "truncated": len(text) > int(max_chars),
                "text": text[:int(max_chars)]}

    def _fs_write(self, path, content):
        p = self._safe(path)
        d = os.path.dirname(p)
        if d and not os.path.isdir(d):
            os.makedirs(d)
        with open(p, "w", encoding="utf-8", newline="\n") as f:
            f.write(content)
        return {"path": path, "bytes": len(content.encode("utf-8"))}

    def _text_stats(self, path):
        p = self._safe(path)
        if not os.path.isfile(p):
            raise ToolError("not a file: %s" % path)
        with open(p, "r", encoding="utf-8", errors="replace") as f:
            text = f.read()
        return {"path": path, "lines": text.count("\n") + (1 if text else 0),
                "chars": len(text), "cjk": len(CJK.findall(text))}

    def _mem_write(self, subject, predicate, value):
        if self.memory is None:
            raise ToolError("memory not attached")
        fid = self.memory.remember(subject, predicate, value, source="tool")
        return {"factId": fid, "subject": subject, "predicate": predicate, "value": value}

    def _mem_search(self, query, k=5):
        if self.memory is None:
            raise ToolError("memory not attached")
        return {"facts": self.memory.recall(query, k=int(k)),
                "episodes": self.memory.search_episodes(query, k=int(k))}
