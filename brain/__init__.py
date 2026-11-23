"""C10 长期智能体（第二大脑）—— 五层架构实现。

layers:
  interface  接口层  brain/cli.py          命令行入口，只做参数解析与结果打印
  tools      工具层  brain/tools.py        能力的唯一入口，manifest + 沙箱 + 统一返回
  loop       循环层  brain/loop.py         感知-规划-执行-反思-落库-进化
  skills     技能层  brain/skills.py       声明式技能注册表 + 轨迹蒸馏
  memory     记忆层  brain/memory.py       KSTAR 四层（工作/情景/语义/程序）
"""

__all__ = ["memory", "tools", "skills", "planner", "loop", "cli"]
__version__ = "1.0.0"
