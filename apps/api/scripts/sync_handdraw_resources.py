"""把本地 handdraw-style-prompter Skill 的资源快照同步进仓库内置目录。

运行时机：维护者升级 Skill 后手工执行一次，产出物随仓库提交。
应用运行时不读取本机 Skill 目录，只读 `app/resources/handdraw/`。

用法：
    python scripts/sync_handdraw_resources.py            # 默认源路径
    python scripts/sync_handdraw_resources.py --source <skill-root>
"""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

DEFAULT_SOURCE = Path.home() / ".trae-cn" / "skills" / "handraw-style"
TARGET = Path(__file__).resolve().parents[1] / "app" / "resources" / "handdraw"

REFERENCE_JSON = (
    "styles.json",
    "colors.json",
    "model_capabilities.json",
    "style_alias_map.json",
    "version.json",
)


def _copy_json(source_root: Path, target: Path) -> None:
    references = source_root / "skills" / "handdraw-style-prompter" / "references"
    for name in REFERENCE_JSON:
        src = references / name
        if not src.exists():
            raise SystemExit(f"缺少源文件：{src}")
        shutil.copy2(src, target / name)
    # 根目录 version.json 与 attribution.json 优先，缺失则跳过
    for name in ("version.json", "attribution.json"):
        src = source_root / name
        if src.exists():
            shutil.copy2(src, target / name)
    license_file = source_root / "LICENSE"
    if license_file.exists():
        shutil.copy2(license_file, target / "LICENSE")


def _copy_images(source_root: Path, target: Path) -> tuple[int, int]:
    refs_count = 0
    individual = source_root / "images" / "individual"
    for group_dir in sorted(individual.iterdir()):
        if not group_dir.is_dir():
            continue
        out_dir = target / "refs" / group_dir.name
        out_dir.mkdir(parents=True, exist_ok=True)
        for image in sorted(group_dir.glob("*.webp")):
            shutil.copy2(image, out_dir / image.name)
            refs_count += 1
    swatch_count = 0
    colors = source_root / "images" / "colors"
    out_swatch = target / "swatches"
    out_swatch.mkdir(parents=True, exist_ok=True)
    for image in sorted(colors.glob("*.webp")):
        shutil.copy2(image, out_swatch / image.name)
        swatch_count += 1
    return refs_count, swatch_count


def main() -> None:
    parser = argparse.ArgumentParser(description="同步手绘风格资源快照")
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    args = parser.parse_args()
    source_root: Path = args.source
    if not source_root.exists():
        raise SystemExit(f"Skill 目录不存在：{source_root}")
    TARGET.mkdir(parents=True, exist_ok=True)
    _copy_json(source_root, TARGET)
    refs, swatches = _copy_images(source_root, TARGET)
    print(f"已同步 {refs} 张参考图、{swatches} 张色卡到 {TARGET}")


if __name__ == "__main__":
    main()
