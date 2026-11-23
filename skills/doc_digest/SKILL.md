# doc_digest

读一个文本文件，返回行数 / 字符数 / 中文字数 / 预览，并把行数写成一条语义事实
（`<path>.line_count = <lines>`）。

## 参数

- `path`（string）：文件相对路径，必须在沙箱（C10 根目录）内

## 实现形态

代码式技能（`impl.py`）。技能内部只通过 `ctx.tools.call` 调用工具，
不直接读写文件系统，因此沙箱与错误语义和主循环完全一致。

## 返回

```json
{"path": "...", "lines": 12, "chars": 340, "cjk": 210, "preview": "...", "factId": 7}
```

## 失败语义

任一步工具返回 `ok=false` 时抛出 `RuntimeError`，由技能注册表捕获并转成
`{"ok": false, "error": ...}`，程序记忆会记一次失败调用。
