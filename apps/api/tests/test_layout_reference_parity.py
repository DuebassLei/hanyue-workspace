"""Node ↔ Python 排版引擎逐字节差分回归。

参考实现：LAYOUT_REF_DIR（默认 dsh-wechat-studio-main）vendor/layout/layout.js。
Node 侧通过 ref_dump.mjs 调用参考 renderPage，提取正文 HTML；Python 侧通过
tests.parity.py_dump 调用 render_studio_document；两者对同一 cases.jsonl 逐字节比较。
参考项目缺失或没有 node 时跳过。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from app.services.wechat_layout_engine import STYLES, SVG_CARD_KINDS, THEMES

HERE = Path(__file__).parent
PARITY = HERE / 'parity'
CORPUS = PARITY / 'corpus'
REF_DIR = Path(os.environ.get('LAYOUT_REF_DIR', r'C:\Users\11301\Downloads\dsh-wechat-studio-main\dsh-wechat-studio-main'))

BRAND = {'brandAuthor': '测试作者', 'brandSlogan': '测试标语'}


def build_cases() -> list[dict]:
    files = {p.stem: p.read_text(encoding='utf-8') for p in sorted(CORPUS.glob('*.md'))}
    cases: list[dict] = []
    for name, md in files.items():
        cases.append({'id': f'{name}::std::orange', 'md': md, 'style': 'std', 'theme': 'orange', **BRAND})
    # 全风格扫描：引用/金句与混合文档对风格分支最敏感（std/orange 已在上面覆盖）
    for name in ('03-quotes', '09-kitchen'):
        for style in STYLES:
            if style == 'std':
                continue
            cases.append({'id': f'{name}::{style}::orange', 'md': files[name], 'style': style, 'theme': 'orange', **BRAND})
    # 全配色扫描：行内样式对配色最敏感
    for theme in THEMES:
        cases.append({'id': f'01-inline::std::{theme}', 'md': files['01-inline'], 'style': 'std', 'theme': theme, **BRAND})
    # front matter 驱动（style/theme 不从参数来）
    cases.append({'id': '08-front-matter::meta::meta', 'md': files['08-front-matter'], **BRAND})
    # 默认作者卡（不传 brandAuthor/brandSlogan，暴露默认值差异）
    cases.append({'id': '02-headings::std::orange-default-brand', 'md': files['02-headings'], 'style': 'std', 'theme': 'orange'})
    return cases


def _dump_node(cases_path: Path, out_path: Path) -> None:
    env = {**os.environ, 'LAYOUT_REF_DIR': str(REF_DIR)}
    subprocess.run(
        ['node', str(PARITY / 'ref_dump.mjs'), '--cases', str(cases_path), '--out', str(out_path)],
        check=True, env=env, cwd=str(PARITY),
    )


def _dump_banner_node(cases_path: Path, out_path: Path) -> None:
    env = {**os.environ, 'LAYOUT_REF_DIR': str(REF_DIR)}
    subprocess.run(
        ['node', str(PARITY / 'dump_banner.mjs'), '--cases', str(cases_path), '--out', str(out_path)],
        check=True, env=env, cwd=str(PARITY),
    )


def _dump_banner_python(cases_path: Path, out_path: Path) -> None:
    subprocess.run(
        [sys.executable, '-m', 'tests.parity.dump_banner_py', '--cases', str(cases_path), '--out', str(out_path)],
        check=True, cwd=str(HERE.parent),
    )


def _dump_python(cases_path: Path, out_path: Path) -> None:
    api_root = HERE.parent
    subprocess.run(
        [sys.executable, '-m', 'tests.parity.py_dump', '--cases', str(cases_path), '--out', str(out_path)],
        check=True, cwd=str(api_root),
    )


def _load(path: Path) -> dict[str, str]:
    return {json.loads(line)['id']: json.loads(line)['html']
            for line in path.read_text(encoding='utf-8').splitlines() if line}


def _first_diff(a: str, b: str) -> tuple[int, str]:
    n = min(len(a), len(b))
    idx = next((i for i in range(n) if a[i] != b[i]), n)
    lo = max(0, idx - 50)
    ctx_a = a[lo:idx + 70].replace('\n', '\\n')
    ctx_b = b[lo:idx + 70].replace('\n', '\\n')
    return idx, f'  node: ...{ctx_a}...\n  py  : ...{ctx_b}...（差异位于第 {idx} 字节，node 长度 {len(a)}，py 长度 {len(b)}）'


@pytest.fixture(scope='module')
def parity_result(tmp_path_factory) -> dict[str, tuple[str, str]]:
    if not REF_DIR.is_dir():
        pytest.skip(f'参考项目不存在：{REF_DIR}')
    if not shutil.which('node'):
        pytest.skip('未安装 node，无法运行参考实现')
    cases = build_cases()
    workdir = tmp_path_factory.mktemp('parity')
    cases_path = workdir / 'cases.jsonl'
    cases_path.write_text('\n'.join(json.dumps(c, ensure_ascii=False) for c in cases), encoding='utf-8')
    node_out = workdir / 'node_dump.jsonl'
    py_out = workdir / 'py_dump.jsonl'
    _dump_node(cases_path, node_out)
    _dump_python(cases_path, py_out)
    node_html, py_html = _load(node_out), _load(py_out)
    return {c['id']: (node_html.get(c['id'], ''), py_html.get(c['id'], '')) for c in cases}


def test_reference_parity(parity_result):
    failed = []
    for cid, (node, py) in parity_result.items():
        if node != py:
            _, ctx = _first_diff(node, py)
            failed.append(f'[{cid}]\n{ctx}')
    assert not failed, f'{len(failed)}/{len(parity_result)} 个 case 与参考实现不一致：\n' + '\n'.join(failed[:8])


# ---------------------------------------------------------------------------
# Banner 差分：参考 vendor/layout/banner.js 的 build / checkSpec
# ---------------------------------------------------------------------------

POSTER_SPEC = {
    'eyebrow': 'AI 排版实测', 'title1': '排版这件事', 'title2': '交给流程',
    'right': 'flow', 'ask': '帮我排好这篇', 'result': '排好了',
    'result_sub': '直接贴进公众号', 'tags': ['不开编辑器', '一键复制', '十套风格'], 'big': 'AI',
}
CARD_SPEC = {
    'eyebrow': 'PRODUCT · TOPIC', 'title1': '第一行黑字', 'title2': '第二行主题色',
    'sub': '关键词 · 关键词 · 关键词', 'right': 'steps',
    'steps': [{'label': '第一步', 'icon': 'doc'}, {'label': '第二步', 'icon': 'spark'}],
    'result': '点个关注，下一篇见',
}


def _card(right, theme=None, **extra) -> dict:
    body = {**CARD_SPEC, 'right': right, **extra}
    if theme:
        body['theme'] = theme
    return {'style': 'card', 'card': body}


def _poster(theme=None, **extra) -> dict:
    body = {**POSTER_SPEC, **extra}
    if theme:
        body['theme'] = theme
    return {'style': 'poster', 'poster': body}


def _neo(theme=None, **extra) -> dict:
    body = {**POSTER_SPEC, **extra}
    if theme:
        body['theme'] = theme
    return {'style': 'neo', 'poster': body}


def build_banner_cases() -> list[dict]:
    cases: list[dict] = []

    def add(cid: str, spec: dict, theme: str | None = 'orange') -> None:
        cases.append({'id': cid, 'spec': spec, 'theme': theme})

    # poster flow：15 套正文配色映射
    for theme in THEMES:
        add(f'poster-flow::{theme}', _poster(theme), theme)
    # poster 其他右栏
    add('poster-tags::teal', _poster('teal', right='tags'), 'teal')
    add('poster-tags-one::rose', _poster('rose', right='tags', tags=['只有一条']), 'rose')
    add('poster-tags-empty::amber', _poster('amber', right='tags', tags=[]), 'amber')
    add('poster-stat::ink', _poster('ink', right='stat', value='90%', label='重复劳动', note='AI 全部接走'), 'ink')
    add('poster-stat-number::navy', _poster('navy', right='stat', value=90, label=12, note=None), 'navy')
    add('poster-steps::violet', _poster('violet', right='steps', steps=['收集素材', '写成初稿', '排版发布']), 'violet')
    # brand / avatar 分支
    add('poster-brand::blue', {**_poster('blue'), 'brand': {'author': '测试作者', 'slogan': '测试标语'}}, 'blue')
    add('poster-avatar-false::forest',
        {**_poster('forest', avatar=False), 'brand': {'author': '测试作者', 'slogan': '测试标语'}}, 'forest')
    add('poster-avatar-false-no-brand::mist', _poster('mist', avatar=False), 'mist')
    add('poster-brand-author-only::teal',
        {**_poster('teal', avatar=False), 'brand': {'author': '只有作者'}}, 'teal')
    add('poster-default-theme', _poster(), None)
    # 回退与边界
    add('poster-unknown-right::blue', _poster('blue', right='wat'), 'blue')
    add('poster-unknown-theme', _poster('zzz'), 'blue')
    add('poster-long-title::teal', _poster('teal', title1='这一行标题明显写得太长了吧', title2='第二行也很长很长'),
        'teal')
    add('poster-empty::orange', {'style': 'poster', 'poster': {}}, 'orange')
    add('poster-missing-body::orange', {'style': 'poster'}, 'orange')
    # neo
    add('neo-flow::violet', _neo('violet'), 'violet')
    add('neo-tags-default::matcha', _neo('matcha', right=None, tags=['第一条', '第二条']), 'matcha')
    add('neo-stat::amber', _neo('amber', right='stat', value='3 分钟', label='出稿时间', note='从选题到成稿'), 'amber')
    add('neo-steps::mist', _neo('mist', right='steps', steps=['选题', '成稿']), 'mist')
    add('neo-big::cocoa', _neo('cocoa', big='AI'), 'cocoa')
    add('neo-big-long::petrol', _neo('petrol', big='AI排版'), 'petrol')
    add('neo-brand::forest', {**_neo('forest'), 'brand': {'author': '测试作者', 'slogan': '测试标语'}}, 'forest')
    add('neo-no-brand::blue', _neo('blue'), 'blue')
    add('neo-empty::orange', {'style': 'neo', 'poster': {}}, 'orange')
    add('neo-default-theme', _neo(), None)
    # card：12 种右栏
    add('card-steps::blue', _card('steps'), 'blue')
    add('card-steps-flow::teal', _card('steps', flow=['先', '再', '最后']), 'teal')
    add('card-timeline-no-result::violet', _card('timeline', items=[
        {'title': '第一步', 'desc': '准备素材'}, {'title': '第二步', 'desc': '开始'},
    ]), 'violet')
    add('card-remix::teal', _card('remix', cards=[
        {'title': '原稿', 'art': 'nodes'},
        {'title': '改造', 'sub': '换成流程', 'hl': True},
        {'title': '成稿', 'art': 'image'},
    ], track_label='SHOT', track=[0.36, 0.4, 0.24], caption='三段式'), 'teal')
    add('card-pick::rose', _card('pick', grid_label='六个平均方案', pick='选', pick_label='留下最对的',
                                 card_title1='今天的判断', card_title2='只留一版', caption='取舍'), 'rose')
    add('card-timeline::ink', _card('timeline', items=[
        {'title': '第一步', 'desc': '准备素材'}, {'title': '第二步'}, {'title': '第三步', 'desc': '发布'},
    ], result='点个关注'), 'ink')
    add('card-converge::navy', _card('converge', caption='收敛', cards=[
        {'num': '01', 'icon': 'doc', 'label': '收集'}, {'num': '02', 'icon': 'chat', 'label': '筛选'},
        {'num': '03', 'icon': 'spark', 'label': '成稿'},
    ], box_title='最后只剩一版', box_sub='不用再纠结'), 'navy')
    add('card-tree::violet', _card('tree', head='一句话就能分叉', items=[
        {'icon': 'doc', 'title': '分支一', 'desc': '说明一'}, {'icon': 'chat', 'title': '分支二', 'desc': '说明二'},
        {'icon': 'spark', 'title': '分支三', 'desc': '说明三'}, {'icon': 'web', 'title': '分支四', 'desc': '说明四'},
    ], result='继续看'), 'violet')
    add('card-compare::forest', _card('compare', caption='同一道题', head='四家结果', items=[
        {'icon': 'web', 'label': '甲'}, {'icon': 'chat', 'label': '乙'}, {'icon': 'doc', 'label': '丙'},
    ], result='结论在后'), 'forest')
    add('card-storyboard::amber', _card('storyboard', caption='一个角色', who={'label': '主角', 'avatar': True},
                                         shots=[{'icon': 'image', 'label': '全景'}, {'icon': 'person', 'label': '中景'},
                                                {'icon': 'scene_close', 'label': '特写'}],
                                         flow=['角色', '分镜', '成片']), 'amber')
    add('card-storyboard-icon::mist', _card('storyboard', who={'label': '主角', 'icon': 'person'}, shots=[
        {'icon': 'image', 'label': '全景'}, {'icon': 'image', 'label': '中景'},
    ], flow=['角色', '成片']), 'mist')
    add('card-chat-no-title::navy', _card('chat', messages=[
        {'from': 'ai', 'text': '在的'}, {'from': 'me', 'text': '帮我排一下'},
    ], result_title='好了'), 'navy')
    add('card-chat::cocoa', _card('chat', window_title='和 AI 对话', messages=[
        {'from': 'me', 'text': '帮我排好这篇'}, {'from': 'ai', 'text': '好的，正在处理'},
        {'from': 'me', 'text': '快一点'}, {'from': 'ai', 'text': '好了'},
    ], result_icon='doc', result_title='排好了', result_sub='直接贴进公众号', caption='对话即排版'), 'cocoa')
    add('card-stats::petrol', _card('stats', caption='三个数字', items=[
        {'label': '时间', 'value': '3 分钟', 'note': '从选题到成稿'},
        {'label': '风格', 'value': '10', 'note': '一套流程全包'},
        {'label': '返工', 'value': '0', 'note': '一次成型', 'hl': True},
    ], result='点个关注'), 'petrol')
    add('card-checklist::matcha', _card('checklist', title='发布前检查', items=[
        '标题不超过 20 字', '配图三张以上', '结尾加关注', '标签补齐',
    ], done='4/4 完成', caption='逐项打勾'), 'matcha')
    add('card-terminal::dusty', _card('terminal', title='一条命令搞定', lines=[
        {'type': 'cmd', 'text': 'wenyan layout article.md'}, {'type': 'out', 'text': '解析中…'},
        {'type': 'ok', 'text': '完成'}, {'text': '默认 out 行'},
    ], caption='终端里跑完'), 'dusty')
    # card 其他分支
    add('card-avatar-false::orange', {**_card('steps'), 'brand': {'author': '测试作者', 'slogan': '测试标语'}}, 'orange')
    add('card-no-brand::teal', _card('steps', avatar=False, foot='关注不迷路'), 'teal')
    add('card-default-theme', _card('steps'), None)
    add('card-empty::orange', {'style': 'card', 'card': {}}, 'orange')
    add('card-too-long::blue', _card('steps', eyebrow='眉标写得太长了吧啦啦', title1='标题第一行太长了啦',
                                     title2='标题第二行也太长了', sub='副标题超过二十个字就会挤出卡片外面'), 'blue')
    add('card-unknown-right::blue', _card('nope'), 'blue')
    return cases


@pytest.fixture(scope='module')
def banner_parity_result(tmp_path_factory) -> dict[str, tuple[str, list, str, list]]:
    if not REF_DIR.is_dir():
        pytest.skip(f'参考项目不存在：{REF_DIR}')
    if not (REF_DIR / 'vendor' / 'layout' / 'banner.js').is_file():
        pytest.skip('参考项目缺少 vendor/layout/banner.js')
    if not shutil.which('node'):
        pytest.skip('未安装 node，无法运行参考实现')
    cases = build_banner_cases()
    workdir = tmp_path_factory.mktemp('banner-parity')
    cases_path = workdir / 'banner_specs.jsonl'
    cases_path.write_text('\n'.join(json.dumps(c, ensure_ascii=False) for c in cases), encoding='utf-8')
    node_out = workdir / 'node_banner.jsonl'
    py_out = workdir / 'py_banner.jsonl'
    _dump_banner_node(cases_path, node_out)
    _dump_banner_python(cases_path, py_out)
    node = _load_banner(node_out)
    py = _load_banner(py_out)
    return {c['id']: (*node.get(c['id'], ('', [])), *py.get(c['id'], ('', []))) for c in cases}


def _load_banner(path: Path) -> dict[str, tuple[str, list]]:
    out: dict[str, tuple[str, list]] = {}
    for line in path.read_text(encoding='utf-8').splitlines():
        if line:
            rec = json.loads(line)
            out[rec['id']] = (rec['html'], rec['issues'])
    return out


def test_banner_reference_parity(banner_parity_result):
    failed = []
    for cid, (node_html, node_issues, py_html, py_issues) in banner_parity_result.items():
        if node_html != py_html:
            _, ctx = _first_diff(node_html, py_html)
            failed.append(f'[{cid}] html\n{ctx}')
        elif list(node_issues) != list(py_issues):
            failed.append(f'[{cid}] issues\n  node: {node_issues}\n  py  : {py_issues}')
    assert not failed, f'{len(failed)}/{len(banner_parity_result)} 个 banner case 与参考实现不一致：\n' + '\n'.join(failed[:8])


# ---------------------------------------------------------------------------
# SVG 动画卡差分：参考 layout.js 的 renderSvgCard（8 种卡 × 风格 × 配色 × 边界）
# ---------------------------------------------------------------------------

SVG_SAMPLES = {
    'svg-table': ['指标|数值', '★转化|12.5%', '留存|38%', '> 表里有重点'],
    'svg-bars': ['拉新|45%', '★转化|80%', '留存|30%', '> 转化最高'],
    'svg-steps': ['第一步|打开设置页', '第二步|点击保存并退出', '> 两步搞定'],
    'svg-timeline': ['2020|立项', '2021|★增长', '2022|爆发', '> 三年三级跳'],
    'svg-breakdown': ['模块A|这里是拆开后的说明文字', '★模块B|重点内容', '> 拆开看更清楚'],
    'svg-compare': ['传统做法|新做法', '慢|快', '★贵|省', '> 换一种做法'],
    'svg-concept': ['一句话概念', '旧|旧A|旧B', '现在|★新A|新B|新C', '!一句备注说明', '> 概念金句'],
    'svg-phone': ['示意演示，非真实截图', '开场|我|帮我排好这篇', '确认|对方|收到，马上处理', '> 对话即排版'],
}


def build_svg_card_cases() -> list[dict]:
    cases: list[dict] = []

    def add(cid: str, kind: str, lines: list[str], title: str | None = '示例标题',
            style: str = 'std', theme: str = 'orange') -> None:
        cases.append({'id': cid, 'kind': kind, 'title': title, 'lines': lines,
                      'style': style, 'theme': theme})

    # 8 种卡 × 合法样例（std / orange）
    for kind, lines in SVG_SAMPLES.items():
        add(f'{kind}::std::orange', kind, lines)
    # 全 10 种风格抽样（卡框分支对风格最敏感）
    for style in STYLES:
        for kind in ('svg-table', 'svg-bars', 'svg-phone'):
            add(f'{kind}::{style}::orange', kind, SVG_SAMPLES[kind], style=style)
    # 全 15 套配色（含 orange / ink 等深浅对比）
    for theme in THEMES:
        add(f'svg-table::std::{theme}', 'svg-table', SVG_SAMPLES['svg-table'], theme=theme)
    # 缺省标题：走 SVG_CARD_INFO[kind]["lead"]
    for kind, lines in SVG_SAMPLES.items():
        add(f'{kind}::no-title', kind, lines, title=None)
    # 空 lines：参考实现各自的自然失败/空卡行为
    for kind in SVG_CARD_KINDS:
        add(f'{kind}::empty-lines', kind, [])
    # 数据格式错误触发回退
    add('svg-table::edge-empty', 'svg-table', [])
    add('svg-bars::single-cell', 'svg-bars', ['只有名字'])
    add('svg-bars::non-numeric', 'svg-bars', ['A|abc', 'B|--'])
    add('svg-bars::value-text', 'svg-bars', ['A|45%|45 人', '★B|80%|80 人'])
    add('svg-steps::no-desc', 'svg-steps', ['第一步', '第二步|有说明'])
    add('svg-breakdown::edge-empty', 'svg-breakdown', [])
    add('svg-compare::one-header', 'svg-compare', ['只有一个表头'])
    add('svg-concept::no-track', 'svg-concept', ['只有一句话'])
    add('svg-phone::no-scene', 'svg-phone', ['只有副标题'])
    # 未知 kind：参考回退成普通代码框并报 TypeError
    add('svg-nope::with-title', 'svg-nope', ['随便一行'])
    add('svg-nope::no-title', 'svg-nope', ['随便一行'], title=None)
    return cases


def _dump_svg_node(cases_path: Path, out_path: Path) -> None:
    env = {**os.environ, 'LAYOUT_REF_DIR': str(REF_DIR)}
    subprocess.run(
        ['node', str(PARITY / 'dump_svg_cards.mjs'), '--cases', str(cases_path), '--out', str(out_path)],
        check=True, env=env, cwd=str(PARITY),
    )


def _dump_svg_python(cases_path: Path, out_path: Path) -> None:
    subprocess.run(
        [sys.executable, '-m', 'tests.parity.dump_svg_cards_py', '--cases', str(cases_path), '--out', str(out_path)],
        check=True, cwd=str(HERE.parent),
    )


def _load_svg(path: Path) -> dict[str, tuple[str, list]]:
    out: dict[str, tuple[str, list]] = {}
    for line in path.read_text(encoding='utf-8').splitlines():
        if line:
            rec = json.loads(line)
            out[rec['id']] = (rec['html'], rec['warnings'])
    return out


@pytest.fixture(scope='module')
def svg_card_parity_result(tmp_path_factory) -> dict[str, tuple[str, list, str, list, dict]]:
    if not REF_DIR.is_dir():
        pytest.skip(f'参考项目不存在：{REF_DIR}')
    if not (REF_DIR / 'vendor' / 'layout' / 'layout.js').is_file():
        pytest.skip('参考项目缺少 vendor/layout/layout.js')
    if not shutil.which('node'):
        pytest.skip('未安装 node，无法运行参考实现')
    cases = build_svg_card_cases()
    workdir = tmp_path_factory.mktemp('svg-parity')
    cases_path = workdir / 'svg_cases.jsonl'
    cases_path.write_text('\n'.join(json.dumps(c, ensure_ascii=False) for c in cases), encoding='utf-8')
    node_out = workdir / 'node_svg.jsonl'
    py_out = workdir / 'py_svg.jsonl'
    _dump_svg_node(cases_path, node_out)
    _dump_svg_python(cases_path, py_out)
    node, py = _load_svg(node_out), _load_svg(py_out)
    return {c['id']: (*node.get(c['id'], ('', [])), *py.get(c['id'], ('', [])), c) for c in cases}


# 参考 layout.js 的 sFrame 只有 std/mag/min/geek/bold/neo/dark 七个卡框分支；
# note/zen/bento 是本项目的卡面扩展（见 _sFrame 注释与
# test_wechat_layout_engine.py::test_implemented_styles_produce_distinct_html）。
# 这三套风格只断言能正常出卡，不参与与参考的逐字节比较，差异仅限卡框。
SVG_CARD_PRODUCT_STYLES = frozenset({'note', 'zen', 'bento'})


def test_svg_card_reference_parity(svg_card_parity_result):
    failed, extensions = [], []
    for cid, (node_html, node_warnings, py_html, py_warnings, case) in svg_card_parity_result.items():
        if node_html == py_html and list(node_warnings) == list(py_warnings):
            continue
        if case.get('style') in SVG_CARD_PRODUCT_STYLES:
            extensions.append(cid)
            assert py_warnings == [], f'[{cid}] 产品扩展风格不应触发回退'
            assert '<svg' in py_html, f'[{cid}] 产品扩展风格应正常出卡'
            continue
        if node_html != py_html:
            _, ctx = _first_diff(node_html, py_html)
            failed.append(f'[{cid}] html\n{ctx}')
        else:
            failed.append(f'[{cid}] warnings\n  node: {node_warnings}\n  py  : {py_warnings}')
    assert not failed, f'{len(failed)}/{len(svg_card_parity_result)} 个 SVG 卡 case 与参考实现不一致：\n' + '\n'.join(failed[:8])
    assert len(extensions) == 9, f'产品扩展风格 case 数应为 3 种风格 × 3 种代表卡，实际 {extensions}'

