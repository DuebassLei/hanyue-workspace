"""定时任务脚本包（ScriptContext / ScriptFailure 公共定义）。

内置 manifest 脚本已整体移除：定时任务只剩用户脚本一条通道（ScheduledScript，
进程内执行）。本包保留 `base.py`——ScriptContext / ScriptFailure 是脚本运行时
与执行器的公共契约。
"""
