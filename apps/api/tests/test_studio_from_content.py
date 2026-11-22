"""文章管理 →「进入成稿台」的导入接口。

重点回归三件事：正文能导进来、同一个稿件反复点不会多出重复稿、别人的稿件进不来。
"""

from __future__ import annotations

from tests.test_wechat_mp import _headers

MARKDOWN = """# 进入成稿台的稿件

第一段正文，写得足够长，能过段长判定，也能被拆成独立段落。

第二段正文，同样要够长一些，用来确认导进来的是完整正文而不是标题。
"""


def _content_with_body(client, headers, title="进入成稿台的稿件"):
    created = client.post(
        "/api/v1/content", json={"title": title, "type": "article"}, headers=headers
    )
    assert created.status_code == 201, created.text
    item = created.json()
    saved = client.patch(
        f"/api/v1/content/{item['id']}/draft",
        json={"title": title, "body": MARKDOWN},
        headers=headers,
    )
    assert saved.status_code == 200, saved.text
    return item


def test_from_content_imports_body_and_is_idempotent(client):
    headers = _headers(client, "from-content-owner")
    item = _content_with_body(client, headers)

    first = client.post(
        f"/api/v1/studio/articles/from-content/{item['id']}", headers=headers
    )
    assert first.status_code == 200, first.text
    article = first.json()
    assert article["title"].startswith("进入成稿台")
    # `# 标题` 被提取成 title，正文两段各自成 block
    assert len(article["blocks"]) == 2
    assert "第一段正文" in article["blocks"][0]["text"]

    # 再点一次还是同一篇：否则每点一次「进入成稿台」就多一篇重复稿
    again = client.post(
        f"/api/v1/studio/articles/from-content/{item['id']}", headers=headers
    )
    assert again.status_code == 200, again.text
    assert again.json()["id"] == article["id"]

    listed = client.get("/api/v1/studio/state", headers=headers)
    assert listed.status_code == 200, listed.text
    ids = [row["id"] for row in listed.json()["articles"]]
    assert ids.count(article["id"]) == 1


def test_from_content_rejects_other_users_content(client):
    owner = _headers(client, "from-content-a")
    intruder = _headers(client, "from-content-b")
    item = _content_with_body(client, owner)

    denied = client.post(
        f"/api/v1/studio/articles/from-content/{item['id']}", headers=intruder
    )
    assert denied.status_code == 404
