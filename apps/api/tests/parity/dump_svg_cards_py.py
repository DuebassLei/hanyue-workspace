"""Python 侧 SVG 动画卡差分 harness：对同一份 cases.jsonl 调用 render_svg_card。

用法：python -m tests.parity.dump_svg_cards_py --cases svg_cases.jsonl --out py_svg.jsonl
输出格式与 dump_svg_cards.mjs 一致，pytest 逐 id 比较 html 与 warnings。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from app.services import wechat_layout_engine as engine


def dump_case(case: dict) -> dict:
    kwargs = {}
    if case.get("style"):
        kwargs["style"] = case["style"]
    if case.get("theme"):
        kwargs["theme"] = case["theme"]
    try:
        result = engine.render_svg_card(case["kind"], case.get("title"), case.get("lines") or [], **kwargs)
        html, warnings = result["html"], result["warnings"]
    except Exception as exc:  # noqa: BLE001 - 差分 harness 需要把异常记录为可比较结果
        html = f"__ERROR__ {type(exc).__name__}: {exc}"
        warnings = []
    return {"id": case["id"], "html": html, "warnings": warnings}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", default="svg_cases.jsonl")
    parser.add_argument("--out", default="py_svg.jsonl")
    args = parser.parse_args()
    lines = [line for line in Path(args.cases).read_text(encoding="utf-8").splitlines() if line]
    out = [json.dumps(dump_case(json.loads(line)), ensure_ascii=False) for line in lines]
    Path(args.out).write_text("\n".join(out) + "\n", encoding="utf-8")
    print(f"svg_cards_py_dump: {len(out)} cases -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
