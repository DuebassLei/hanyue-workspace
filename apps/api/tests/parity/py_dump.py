"""Python 侧差分 harness：对同一份 cases.jsonl 调用 render_studio_document 输出 JSONL。

用法：python -m tests.parity.py_dump --cases cases.jsonl --out py_dump.jsonl
输出格式与 ref_dump.mjs 一致，pytest 逐 id 比较 html 字符串。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def dump_case(case: dict) -> dict:
    from app.services.wechat_layout_studio import render_studio_document

    options = {'outro': 'true'}
    for key in ('style', 'theme', 'brandAuthor', 'brandSlogan'):
        if case.get(key):
            options[key] = case[key]
    result = render_studio_document(case['md'], options, [])
    return {'id': case['id'], 'html': result['html']}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--cases', default='cases.jsonl')
    parser.add_argument('--out', default='py_dump.jsonl')
    args = parser.parse_args()
    lines = [line for line in Path(args.cases).read_text(encoding='utf-8').splitlines() if line]
    out = []
    for line in lines:
        try:
            record = dump_case(json.loads(line))
        except Exception as exc:  # noqa: BLE001 - 差分 harness 需要把异常记录为可比较结果
            record = {'id': json.loads(line).get('id'), 'html': f'__ERROR__ {exc}'}
        out.append(json.dumps(record, ensure_ascii=False))
    Path(args.out).write_text('\n'.join(out) + '\n', encoding='utf-8')
    print(f'py_dump: {len(out)} cases -> {args.out}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
