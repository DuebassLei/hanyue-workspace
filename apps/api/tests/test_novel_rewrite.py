import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.db import Base
from app import models
from app.services.job_runner import JobFailure
from app.services.novel_rewrite import (
    accept_chapter,
    apply_extract,
    apply_rewrite_result,
    build_rewrite_prompt,
    parse_extract,
    revert_chapter,
)

FENCED = """说明一下：
```json
{
  "characters": [
    {"originalName": "甲", "relations": "乙的朋友", "speechStyle": "短", "state": "present"},
    {"originalName": "甲", "relations": "重复", "speechStyle": "长", "state": "dead"},
    {"originalName": "  ", "relations": "", "speechStyle": "", "state": "present"},
    {"originalName": "丙", "relations": "", "speechStyle": "", "state": "ghost"}
  ],
  "aliases": [
    {"kind": "person", "originalName": "甲"},
    {"kind": "person", "originalName": "甲"},
    {"kind": "weapon", "originalName": "剑"},
    {"kind": "place", "originalName": "  "},
    {"kind": "place", "originalName": "城南"}
  ]
}
```
后面还有一句。
"""


def test_parse_extract_fenced_json_filters_and_dedupes():
    characters, aliases = parse_extract(FENCED)
    assert [row["originalName"] for row in characters] == ["甲", "丙"]
    assert characters[0]["relations"] == "乙的朋友"
    assert characters[1]["state"] == "present"
    assert aliases == [
        {"kind": "person", "originalName": "甲"},
        {"kind": "place", "originalName": "城南"},
    ]


def test_parse_extract_bad_json():
    with pytest.raises(JobFailure) as caught:
        parse_extract("这不是 json {")
    assert caught.value.code == "extract_parse_failed"


def _session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)()


def _project(db):
    user = models.User(auth_subject="rewrite-user", display_name="rewrite")
    db.add(user)
    db.flush()
    workspace = models.Workspace(owner_user_id=user.id, name="ws")
    db.add(workspace)
    db.flush()
    project = models.NovelProject(
        owner_user_id=user.id,
        workspace_id=workspace.id,
        title="试",
        source_text="甲去了城南。",
        rules={},
    )
    db.add(project)
    db.flush()
    return project


def test_apply_extract_keeps_user_new_names():
    db = _session()
    project = _project(db)
    db.add(models.NovelCharacter(
        project_id=project.id, original_name="甲", new_name="林昭", relations="旧", speech_style="旧", state="present",
    ))
    db.add(models.NovelCharacter(
        project_id=project.id, original_name="遗漏角色", new_name="保留", relations="友人", speech_style="简短", state="dead",
    ))
    db.add(models.NovelAlias(project_id=project.id, kind="person", original_name="甲", new_name="林昭"))
    db.add(models.NovelAlias(project_id=project.id, kind="place", original_name="城南", new_name=""))
    db.add(models.NovelAlias(project_id=project.id, kind="org", original_name="旧门派", new_name="旧名"))
    db.flush()

    apply_extract(
        db,
        project,
        [
            {"originalName": "甲", "relations": "新关系", "speechStyle": "新口气", "state": "present"},
            {"originalName": "乙", "relations": "", "speechStyle": "", "state": "absent"},
        ],
        [
            {"kind": "person", "originalName": "甲"},
            {"kind": "place", "originalName": "城南"},
            {"kind": "org", "originalName": "衙门"},
        ],
    )
    characters = db.query(models.NovelCharacter).filter_by(project_id=project.id).all()
    aliases = db.query(models.NovelAlias).filter_by(project_id=project.id).all()
    by_name = {row.original_name: row for row in characters}
    assert by_name["甲"].new_name == "林昭"
    assert by_name["甲"].relations == "旧"
    assert by_name["甲"].speech_style == "旧"
    assert by_name["遗漏角色"].state == "dead"
    assert by_name["遗漏角色"].new_name == "保留"
    assert by_name["乙"].new_name == ""
    assert by_name["乙"].state == "absent"
    alias_names = {(row.kind, row.original_name): row.new_name for row in aliases}
    assert alias_names[("person", "甲")] == "林昭"
    assert alias_names[("place", "城南")] == ""
    assert alias_names[("org", "衙门")] == ""
    assert alias_names[("org", "旧门派")] == "旧名"


def _chapter(db, project, **overrides):
    values = dict(
        project_id=project.id,
        sort_order=0,
        source="rewrite",
        title="一",
        source_text="甲说了话。",
        draft_text="",
        draft_version=1,
        status="pending",
        facts={"characterChanges": [], "itemHoldings": [], "events": [], "openHooks": []},
        stale=False,
    )
    values.update(overrides)
    chapter = models.NovelChapter(**values)
    db.add(chapter)
    db.flush()
    return chapter


def test_apply_rewrite_result_version_match_and_conflict():
    db = _session()
    project = _project(db)
    chapter = _chapter(db, project, draft_text="旧草稿", draft_version=2)

    assert apply_rewrite_result(db, chapter, "新草稿", 2) == "applied"
    assert chapter.draft_text == "新草稿"
    assert chapter.draft_version == 3
    assert chapter.status == "drafted"
    assert chapter.pending_candidate is None

    assert apply_rewrite_result(db, chapter, "后来的文本", 2) == "conflict"
    assert chapter.draft_text == "新草稿"
    assert chapter.draft_version == 3
    assert chapter.pending_candidate == "后来的文本"


def test_accept_chapter_empty_and_success():
    db = _session()
    project = _project(db)
    empty = _chapter(db, project, draft_text="   ", sort_order=0)
    with pytest.raises(JobFailure) as caught:
        accept_chapter(db, empty, {})
    assert caught.value.code == "empty_draft"

    chapter = _chapter(db, project, draft_text="定稿。", sort_order=1, status="drafted")
    accept_chapter(db, chapter, {"events": ["甲说了话"], "extra": ["丢掉"], "characterChanges": "不是列表"})
    assert chapter.accepted_text == "定稿。"
    assert chapter.status == "accepted"
    assert chapter.facts == {
        "characterChanges": [],
        "itemHoldings": [],
        "events": ["甲说了话"],
        "openHooks": [],
    }


def test_revert_chapter_requires_accepted_and_marks_later_stale():
    db = _session()
    project = _project(db)
    current = _chapter(db, project, status="drafted", accepted_text="上次", sort_order=0)
    later = _chapter(db, project, status="accepted", sort_order=1, stale=False, draft_text="后文")
    with pytest.raises(JobFailure) as caught:
        revert_chapter(db, current, [later])
    assert caught.value.code == "not_accepted"
    assert later.stale is False

    current.status = "accepted"
    current.accepted_text = "已接受正文"
    revert_chapter(db, current, [later])
    assert current.status == "drafted"
    assert current.accepted_text == "已接受正文"
    assert later.stale is True
    assert later.draft_text == "后文"


def test_build_rewrite_prompt_alias_and_empty_rules():
    chapter = type("Chapter", (), {"title": "开篇", "source_text": "甲去了城南。"})()
    aliases = [
        type("Alias", (), {"kind": "person", "original_name": "甲", "new_name": "林昭"})(),
        type("Alias", (), {"kind": "place", "original_name": "城南", "new_name": ""})(),
    ]
    prompt = build_rewrite_prompt(chapter, aliases, [], {})
    assert "林昭" in prompt
    assert "该名称保持原名" in prompt
    assert "人称跟随原文" in prompt
    assert "不额外指定" in prompt
