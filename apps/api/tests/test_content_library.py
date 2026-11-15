"""稿件库后端行为：fields=list 瘦身、筛选参数、归档软隐藏。"""

from tests.conftest import login


def _auth(client, subject="lib-user"):
    return login(client, subject)


def _create(client, headers, title):
    res = client.post("/api/v1/content", json={"title": title}, headers=headers)
    assert res.status_code == 201, res.text
    return res.json()["id"]


def test_list_fields_omits_body_and_adds_counts(client):
    headers = _auth(client, "lib-fields")
    item_id = _create(client, headers, "瘦身列表稿")
    client.post(
        f"/api/v1/content/{item_id}/versions",
        json={"title": "瘦身列表稿", "body": "x" * 5000},
        headers=headers,
    )

    full = client.get("/api/v1/content", headers=headers).json()
    row = next(r for r in full if r["id"] == item_id)
    assert (row.get("draftBody") is not None) or (
        row.get("latestVersion") and len(row["latestVersion"]["body"]) > 0
    )

    slim = client.get("/api/v1/content?fields=list", headers=headers).json()
    row = next(r for r in slim if r["id"] == item_id)
    assert row.get("draftBody") is None
    assert row.get("latestVersion") is None
    assert row["versionNo"] == 2  # 创建 v1 + 手动存一版
    assert row["variantCount"] == 0
    assert row["archived"] is False


def test_list_filters_by_keyword_status_and_platform(client):
    headers = _auth(client, "lib-filter")
    hit = _create(client, headers, "微信公众号运营心得")
    miss = _create(client, headers, "无关标题")

    rows = client.get("/api/v1/content?fields=list&q=公众号", headers=headers).json()
    ids = {r["id"] for r in rows}
    assert hit in ids and miss not in ids

    rows = client.get("/api/v1/content?fields=list&status=published", headers=headers).json()
    assert rows == []

    # 平台筛选走 variants EXISTS；无 variant 时任何平台都筛不出
    rows = client.get("/api/v1/content?fields=list&platform=wechat", headers=headers).json()
    assert rows == []


def test_archive_hides_from_default_list_and_restores(client):
    headers = _auth(client, "lib-archive")
    item_id = _create(client, headers, "待归档稿")

    res = client.patch(f"/api/v1/content/{item_id}/archive", json={"archived": True}, headers=headers)
    assert res.status_code == 200, res.text
    assert res.json()["archived"] is True
    assert res.json()["archivedAt"]

    default_ids = {r["id"] for r in client.get("/api/v1/content?fields=list", headers=headers).json()}
    assert item_id not in default_ids

    with_archived = client.get(
        "/api/v1/content?fields=list&includeArchived=true", headers=headers
    ).json()
    row = next(r for r in with_archived if r["id"] == item_id)
    assert row["archived"] is True

    # 详情接口不受归档影响，历史链接不 404
    detail = client.get(f"/api/v1/content/{item_id}", headers=headers)
    assert detail.status_code == 200

    res = client.patch(f"/api/v1/content/{item_id}/archive", json={"archived": False}, headers=headers)
    assert res.json()["archived"] is False
    assert res.json()["archivedAt"] is None
    default_ids = {r["id"] for r in client.get("/api/v1/content?fields=list", headers=headers).json()}
    assert item_id in default_ids


def test_list_sort_by_title(client):
    headers = _auth(client, "lib-sort")
    _create(client, headers, "beta draft")
    _create(client, headers, "alpha draft")
    rows = client.get(
        "/api/v1/content?fields=list&sort=title", headers=headers
    ).json()
    titles = [r["title"] for r in rows]
    assert titles == sorted(titles)
