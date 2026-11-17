"""定时任务脚本包。

每个脚本是一个模块，暴露一个 `run(params: dict, ctx: ScriptContext) -> dict` 函数：
- 参数已按 manifest 的 fields 声明校验、补过默认值，脚本内直接按 key 取值即可；
- 返回值原样落到 background_jobs.result，约定可放 `summary` 键供界面显示摘要；
- 可预期的失败抛 `ScriptFailure(code, message)`；异常由执行器统一兜底，不会静默成功。

新增脚本步骤：
1. 在本包下加一个模块，实现 run(params, ctx)；
2. 在 app/resources/scripts/manifest.yaml 加一条声明（key / runner / fields）；
3. 重启服务。前端表单与类型下拉自动出现，无需改前端代码。

脚本运行在服务端进程内，只应由受信任的部署方维护 manifest——这是一条信任边界，
不是给用户在线写任意代码的地方（用户表没有角色字段，做不到安全的多租户代码隔离）。
"""
