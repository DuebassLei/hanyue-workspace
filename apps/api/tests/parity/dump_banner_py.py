"""Python 侧 banner 差分 harness：对同一份 specs.jsonl 调用移植后的 build/checkSpec。

用法：python -m tests.parity.dump_banner_py --cases banner_specs.jsonl --out py_banner.jsonl
输出格式与 dump_banner.mjs 一致，pytest 逐 id 比较 html 与 issues。
主题映射在 harness 里独立实现（与 Node 侧各写一遍），映射漂移会被差分测试抓到。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from app.services import wechat_layout_engine as engine


def mapped_themes() -> dict:
    themes = dict(engine._BANNER_THEMES)
    for key, t in engine.THEMES.items():
        themes[key] = {"main": t["main"], "light": t["tint"], "line": t["line"],
                       "border": t["tint"], "bg": t["soft"]}
    return themes


def dump_case(case: dict, themes: dict) -> dict:
    spec = case["spec"]
    try:
        html = engine.build_banner(spec, themes if case.get("theme") else None)
        issues = engine.check_banner(spec, [html])
    except Exception as exc:  # noqa: BLE001 - 差分 harness 需要把异常记录为可比较结果
        html = f"__ERROR__ {type(exc).__name__}: {exc}"
        issues = []
    return {"id": case["id"], "html": html, "issues": issues}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", default="banner_specs.jsonl")
    parser.add_argument("--out", default="py_banner.jsonl")
    args = parser.parse_args()
    themes = mapped_themes()
    lines = [line for line in Path(args.cases).read_text(encoding="utf-8").splitlines() if line]
    out = [json.dumps(dump_case(json.loads(line), themes), ensure_ascii=False) for line in lines]
    Path(args.out).write_text("\n".join(out) + "\n", encoding="utf-8")
    print(f"banner_py_dump: {len(out)} cases -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
