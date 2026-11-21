"""像素级 screenshot 差分：固定浏览器/视口/DPR 下比较参考与 Python 的渲染结果。

依赖：Node + npx（Playwright CLI 与 Chromium 已在本地缓存时零下载）+ Pillow。
两端 HTML 逐字节一致时，此项只用于兜住「渲染环境漂移」（字体缺失、Chromium 版本变化）。

运行：cd apps/api && pytest tests/test_layout_pixel_parity.py -q
参考项目缺失、没有 node/npx/Playwright 或 Pillow 时自动跳过。
"""
from __future__ import annotations

import functools
import http.server
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

import pytest

HERE = Path(__file__).parent
PARITY = HERE / 'parity'
CORPUS = PARITY / 'corpus'
REF_DIR = Path(os.environ.get('LAYOUT_REF_DIR', r'C:\Users\11301\Downloads\dsh-wechat-studio-main\dsh-wechat-studio-main'))

# 像素对比只需要覆盖各风格的结构差异，不追求穷举（HTML 差分已做穷举）
PIXEL_CASES = [
    ('03-quotes', 'std'),
    ('03-quotes', 'dark'),
    ('09-kitchen', 'neo'),
    ('09-kitchen', 'mag'),
    ('05-tables-lists', 'bento'),
    ('01-inline', 'min'),
]

VIEWPORT = (390, 844)
SHELL = (
    '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
    '<style>html,body{{margin:0;padding:0;background:#EDEDED}}.sheet{{width:{w}px;background:#fff;'
    'padding:18px 14px;box-sizing:border-box;margin:0 auto}}</style></head>'
    '<body><div class="sheet">{body}</div></body></html>'
)


def _npx(args: list[str]) -> list[str]:
    """Windows 上 npx 是 .CMD，CreateProcess 不能直接执行，需经 cmd /c。"""
    return ['cmd', '/c', 'npx', *args] if os.name == 'nt' else ['npx', *args]


def _resolve_cli() -> Path | None:
    """优先用本地已缓存的 playwright cli.js（免去 npx 每次解析，且能校验浏览器已安装）。

    顺序：PLAYWRIGHT_CLI 环境变量 → npm-cache/_npx 下与已装 chromium-headless-shell 版本匹配的 cli.js。
    """
    env_cli = os.environ.get('PLAYWRIGHT_CLI')
    if env_cli and Path(env_cli).is_file():
        return Path(env_cli)
    local = os.environ.get('LOCALAPPDATA')
    if not local:
        return None
    browsers = Path(local) / 'ms-playwright'
    for cli in sorted((Path(local) / 'npm-cache' / '_npx').glob('*/node_modules/playwright/cli.js')):
        # npx 缓存里 playwright-core 与 playwright 平级；pip/独立安装时 browsers.json 就在 cli 同级
        manifests = [cli.parent / 'browsers.json', cli.parent.parent / 'playwright-core' / 'browsers.json']
        data = None
        for manifest in manifests:
            if manifest.is_file():
                try:
                    data = json.loads(manifest.read_text(encoding='utf-8'))
                    break
                except (OSError, ValueError):
                    data = None
        if not data:
            continue
        rev = next((b.get('revision') for b in data.get('browsers', [])
                    if b.get('name') == 'chromium-headless-shell'), None)
        if rev and (browsers / f'chromium_headless_shell-{rev}').is_dir():
            return cli
    return None


def _playwright_cmd() -> list[str] | None:
    node = shutil.which('node')
    cli = _resolve_cli()
    if cli and node:
        return [node, str(cli)]
    if shutil.which('npx'):
        return _npx(['--yes', 'playwright'])
    return None


def _playwright_ready(cmd: list[str] | None) -> bool:
    if not cmd:
        return False
    try:
        out = subprocess.run([*cmd, '--version'], capture_output=True, text=True, timeout=120)
        return out.returncode == 0 and 'Version' in out.stdout
    except (OSError, subprocess.SubprocessError):
        return False


def _ref_html(cases: list[dict], workdir: Path) -> dict[str, str]:
    cases_path = workdir / 'pixel_cases.jsonl'
    out_path = workdir / 'pixel_ref.jsonl'
    cases_path.write_text('\n'.join(json.dumps(c, ensure_ascii=False) for c in cases), encoding='utf-8')
    subprocess.run(
        ['node', str(PARITY / 'ref_dump.mjs'), '--cases', str(cases_path), '--out', str(out_path)],
        check=True, cwd=str(PARITY), env={**os.environ, 'LAYOUT_REF_DIR': str(REF_DIR)},
    )
    return {json.loads(line)['id']: json.loads(line)['html']
            for line in out_path.read_text(encoding='utf-8').splitlines() if line}


def _py_html(cases: list[dict]) -> dict[str, str]:
    from app.services.wechat_layout_engine import render_studio_document

    out = {}
    for case in cases:
        options = {'style': case['style'], 'theme': case['theme'], 'outro': 'true',
                   'brandAuthor': '测试作者', 'brandSlogan': '测试标语'}
        out[case['id']] = render_studio_document(case['md'], options, [])['html']
    return out


class _Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):  # noqa: D401 - 静默静态服务日志
        pass


def _serve(directory: Path):
    handler = functools.partial(_Quiet, directory=str(directory))
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        port = s.getsockname()[1]
    server = http.server.ThreadingHTTPServer(('127.0.0.1', port), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, port


def _shot(cmd: list[str], url: str, png: Path) -> None:
    subprocess.run(
        [*cmd, 'screenshot', '--viewport-size', f'{VIEWPORT[0]},{VIEWPORT[1]}',
         '--full-page', '--wait-for-timeout', '300', url, str(png)],
        check=True, capture_output=True, text=True, timeout=180,
    )


def _diff_pixels(a: Path, b: Path) -> tuple[int, int, int]:
    from PIL import Image, ImageChops

    img_a, img_b = Image.open(a).convert('RGB'), Image.open(b).convert('RGB')
    if img_a.size != img_b.size:
        return -1, img_a.size[0], img_b.size[0]
    box = ImageChops.difference(img_a, img_b).getbbox()
    if box is None:
        return 0, img_a.size[0], img_b.size[0]
    diff = ImageChops.difference(img_a, img_b).convert('L')
    count = sum(1 for px in diff.getdata() if px)
    return count, img_a.size[0], img_b.size[0]


@pytest.mark.skipif(not REF_DIR.is_dir(), reason=f'参考项目不存在：{REF_DIR}')
def test_pixel_parity():
    if not shutil.which('node'):
        pytest.skip('未安装 node')
    pw = _playwright_cmd()
    if not _playwright_ready(pw):
        pytest.skip('Playwright 不可用（可用 PLAYWRIGHT_CLI 指定 cli.js，或先执行 npx playwright install chromium-headless-shell）')
    pytest.importorskip('PIL', reason='未安装 Pillow')

    cases = []
    for name, style in PIXEL_CASES:
        md = (CORPUS / f'{name}.md').read_text(encoding='utf-8')
        cases.append({'id': f'{name}::{style}', 'md': md, 'style': style, 'theme': 'orange',
                      'brandAuthor': '测试作者', 'brandSlogan': '测试标语'})

    with tempfile.TemporaryDirectory() as tmp:
        workdir = Path(tmp)
        static = workdir / 'static'
        static.mkdir()
        ref, py = _ref_html(cases, workdir), _py_html(cases)
        server, port = _serve(static)
        try:
            failures = []
            for case in cases:
                cid = case['id']
                if ref.get(cid) != py.get(cid):
                    failures.append(f'[{cid}] HTML 不一致，像素对比无意义')
                    continue
                for side, html in (('ref', ref[cid]), ('py', py[cid])):
                    (static / f'{cid.replace("::", "_")}_{side}.html').write_text(
                        SHELL.format(w=VIEWPORT[0], body=html), encoding='utf-8')
                base = f'{cid.replace("::", "_")}'
                _shot(pw, f'http://127.0.0.1:{port}/{base}_ref.html', workdir / f'{base}_ref.png')
                _shot(pw, f'http://127.0.0.1:{port}/{base}_py.html', workdir / f'{base}_py.png')
                count, w, _ = _diff_pixels(workdir / f'{base}_ref.png', workdir / f'{base}_py.png')
                if count == -1:
                    failures.append(f'[{cid}] 截图宽度不一致：{w}px vs 另一侧')
                elif count:
                    failures.append(f'[{cid}] 有 {count} 个像素不同（宽度 {w}px，视口 {VIEWPORT}）')
        finally:
            server.shutdown()

    assert not failures, '像素级不一致：\n' + '\n'.join(failures)
