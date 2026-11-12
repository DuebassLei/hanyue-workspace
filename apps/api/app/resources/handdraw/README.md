# 手绘风格库资源（内置快照）

本目录是 [handdraw-style-prompter](https://github.com/yang0/handraw-style) Skill 资源的只读快照，
由 `apps/api/scripts/sync_handdraw_resources.py` 从本机 Skill 目录同步而来。
应用运行时只读取本目录，不依赖本机 Skill 安装路径。

- 作者：yang0
- 仓库：https://github.com/yang0/handraw-style
- 许可证：MIT with Attribution Requirement（见同目录 `LICENSE`）

在分发、二次开发或对外展示本库的提示词、风格名称与参考图时，必须保留上述作者与仓库署名。

## 目录结构

- `styles.json`：331 种手绘风格（编号、分组、参考作者、生成风格名、核心特征）
- `colors.json`：36 种主题色
- `model_capabilities.json`：各图像模型的激活策略（`gpt-image-2` 为标定基线，其余走 unknown 兜底）
- `style_alias_map.json`：新旧风格编号映射（旧三位编号 ↔ `FA-001` 形式）
- `version.json`：资源版本
- `refs/<分组>/<编号>.webp`：风格参考图
- `swatches/<色号>.webp`：主题色卡

## 升级方式

升级 Skill 后执行：

```powershell
cd apps/api
python scripts/sync_handdraw_resources.py --source <skill-root>
```

应用启动时按资源内容哈希判断是否需要重建内置表（`handdraw_styles`、`handdraw_colors`），
并记录到 `handdraw_library_meta`；参考图缺失的风格会写入 `missing_references`，
生图时若命中缺失参考图会以 `reference_missing` 明确失败，不静默降级。
