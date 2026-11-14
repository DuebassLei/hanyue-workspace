from app.services.novel_split import split_chapters

SAMPLE = """第一章 开始

甲说了话。

第二章 继续
乙走了。
"""


def login(client, subject="novel-user"):
    response = client.post("/api/v1/auth/login", json={"subject": subject, "displayName": subject})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['token']}"}


def create_novel(client, headers, **overrides):
    body = {"title": "试作", "sourceText": SAMPLE, "rightsConfirmed": True, **overrides}
    response = client.post("/api/v1/novels", json=body, headers=headers)
    return response


def test_split_keeps_body_and_drops_heading_lines():
    chapters = split_chapters(SAMPLE)
    assert len(chapters) == 2
    assert chapters[0]["title"] == "第一章 开始"
    assert chapters[0]["source_text"] == "甲说了话。"
    assert chapters[1]["title"] == "第二章 继续"
    assert chapters[1]["source_text"] == "乙走了。"
    assert "".join(row["source_text"] for row in chapters) == "甲说了话。乙走了。"


def test_split_untitled_is_one_chapter():
    raw = "  没有标题的一段。\n\n第二段。  "
    chapters = split_chapters(raw)
    assert len(chapters) == 1
    assert chapters[0]["title"] == "正文"
    assert chapters[0]["source_text"] == raw.strip()


def test_create_requires_rights_and_source(client):
    headers = login(client)
    denied = create_novel(client, headers, rightsConfirmed=False)
    assert denied.status_code == 400
    assert denied.json()["code"] == "rights_required"
    empty = create_novel(client, headers, sourceText="   ")
    assert empty.status_code == 400
    assert empty.json()["code"] == "empty_source"


def test_novel_crud_split_and_chapter_reset(client):
    headers = login(client, "novel-owner")
    created = create_novel(client, headers)
    assert created.status_code == 201, created.text
    project = created.json()
    assert project["title"] == "试作"
    assert project["chapters"] == []
    assert project["rightsConfirmedAt"]
    assert project["rules"] == {"person": "", "style": "", "banned": ""}

    listed = client.get("/api/v1/novels", headers=headers)
    assert listed.status_code == 200
    item = next(row for row in listed.json() if row["id"] == project["id"])
    assert item["chapterCount"] == 0
    assert "sourceText" not in item
    assert item["updatedAt"]

    split = client.post(f"/api/v1/novels/{project['id']}/split", headers=headers)
    assert split.status_code == 200, split.text
    chapters = split.json()
    assert [row["title"] for row in chapters] == ["第一章 开始", "第二章 继续"]
    assert chapters[0]["sourceText"] == "甲说了话。"
    assert chapters[1]["sourceText"] == "乙走了。"
    assert chapters[0]["source"] == "rewrite"
    assert chapters[0]["status"] == "pending"
    assert chapters[0]["sortOrder"] == 0
    assert chapters[0]["facts"] == {
        "characterChanges": [],
        "itemHoldings": [],
        "events": [],
        "openHooks": [],
    }

    patched = client.patch(
        f"/api/v1/novels/{project['id']}",
        json={"title": "改名", "rules": {"person": "第一人称", "style": "冷", "banned": "无", "extra": "丢"}},
        headers=headers,
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["title"] == "改名"
    assert patched.json()["rules"] == {"person": "第一人称", "style": "冷", "banned": "无"}
    assert patched.json()["sourceText"] == SAMPLE.strip()

    chapter_id = chapters[0]["id"]
    edited = client.patch(
        f"/api/v1/novels/{project['id']}/chapters/{chapter_id}",
        json={"title": "开篇", "sourceText": "甲又说了话。", "status": "accepted", "draftVersion": 9},
        headers=headers,
    )
    assert edited.status_code == 200, edited.text
    body = edited.json()
    assert body["title"] == "开篇"
    assert body["sourceText"] == "甲又说了话。"
    assert body["status"] == "pending"
    assert body["rewriteText"] == ""
    assert body["draftText"] == ""
    assert body["acceptedText"] == ""
    assert body["draftVersion"] == 0
    assert body["stale"] is False

    again = client.post(f"/api/v1/novels/{project['id']}/split", headers=headers)
    assert again.status_code == 200
    assert len(again.json()) == 2

    missing = client.patch(
        f"/api/v1/novels/{project['id']}/chapters/missing",
        json={"title": "无"},
        headers=headers,
    )
    assert missing.status_code == 404
    assert missing.json()["code"] == "not_found"

    deleted = client.delete(f"/api/v1/novels/{project['id']}", headers=headers)
    assert deleted.status_code == 204
    gone = client.get(f"/api/v1/novels/{project['id']}", headers=headers)
    assert gone.status_code == 404
    assert gone.json()["code"] == "not_found"


def test_other_user_cannot_read(client):
    owner = login(client, "novel-a")
    other = login(client, "novel-b")
    project = create_novel(client, owner).json()
    response = client.get(f"/api/v1/novels/{project['id']}", headers=other)
    assert response.status_code == 404
    assert "小说项目" in response.json()["message"]
    anonymous = client.get(f"/api/v1/novels/{project['id']}")
    assert anonymous.status_code == 401
    unknown = client.get("/api/v1/novels/does-not-exist", headers=owner)
    assert unknown.status_code == 404
