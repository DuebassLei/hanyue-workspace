"""段落模型与修复编排的纯函数测试（不碰数据库、不调模型）。"""

from app.services import article_blocks as AB


def test_split_markdown_keeps_fence_blank_lines_and_extracts_title():
    markdown = "---\ntitle: x\n---\n\n# 真标题\n\n第一段。\n\n```py\nprint(1)\n\nprint(2)\n```\n\n## 小标题\n"
    parsed = AB.split_markdown(markdown)
    assert parsed["front"] == "---\ntitle: x\n---"
    assert parsed["title"] == "真标题"
    # 代码块内部的空行不切，整块算一段
    assert len(parsed["blocks"]) == 3
    assert parsed["blocks"][1].startswith("```py")


def test_block_kind_and_checkable():
    assert AB.block_kind("```py\nx\n```") == "code"
    assert AB.block_kind("## 小标题") == "heading"
    assert AB.block_kind("> 引用一段") == "quote"
    assert AB.block_kind("![图](https://a/b.png)") == "media"
    assert AB.block_kind("- 列表") == "list"
    assert AB.block_kind("---") == "rule"
    assert AB.is_checkable("这一段的文字足够长，足够参加 AI 味质检，大概二十个字以上。")
    assert not AB.is_checkable("短句。")
    assert not AB.is_checkable("## 标题不是成段文字，不参加质检")


def test_apply_ops_uses_pre_mutation_numbers():
    article = AB.build_article(markdown="# T\n\n第一段原文。\n\n第二段原文。\n\n第三段原文。\n")
    ids = [block["id"] for block in article["blocks"]]

    receipts, changed = AB.apply_ops(
        article,
        [
            {"op": "rewrite", "block": 1, "text": "第一段改写。"},
            {"op": "insert_after", "block": 0, "text": "开头新段。"},
            {"op": "delete", "block": 3},
        ],
    )

    # 段号按修改前的位置解析：删除的是原来的第三段，不受前面插入影响
    assert [block["text"] for block in article["blocks"]] == ["开头新段。", "第一段改写。", "第二段原文。"]
    assert article["blocks"][1]["id"] == ids[0]
    assert changed == [1]
    assert receipts == ["改写第 1 段", "在开头加了一段", "删掉第 3 段"]


def test_apply_ops_rejects_out_of_range_and_unknown_fields():
    article = AB.build_article(markdown="# T\n\n一段原文。\n")
    receipts, changed = AB.apply_ops(
        article,
        [
            {"op": "rewrite", "block": 99, "text": "越界"},
            {"op": "drop_table"},
            {"op": "title", "text": "新标题", "hook": "Z"},
        ],
    )
    assert receipts == ["标题改成《新标题》"]
    assert changed == []
    assert article["title"] == "新标题"
    assert article["hook"] is None


def test_fix_plan_skips_twice_worse_and_noise():
    blocks = [
        {"id": "1", "text": "a", "check": {"status": "flag", "score": 0.9, "level": "ai", "source": "api"}},
        {"id": "2", "text": "b", "check": {"status": "flag", "score": 0.9, "fixes": 2, "source": "api"}},
        {"id": "3", "text": "c", "check": {"status": "flag", "score": 0.9, "worse": True, "source": "api"}},
        {"id": "4", "text": "d", "check": {"status": "flag", "score": 0.55, "source": "pdf"}},
        {"id": "5", "text": "e", "check": {"status": "pass"}},
    ]
    picked, skipped = AB.fix_plan(blocks)
    assert picked == [0]
    assert skipped == {"twice": 1, "worse": 1, "noise": 1}


def test_fix_groups_breaks_on_gap_and_sorts_by_weight():
    blocks = [
        {"id": "1", "text": "甲" * 100, "check": {"score": 0.6, "level": "suspect"}},
        {"id": "2", "text": "乙" * 100, "check": {"score": 0.7, "level": "suspect"}},
        {"id": "3", "text": "丙" * 100, "check": {"score": 0.95, "level": "ai"}},
    ]
    groups = AB.fix_groups(blocks, [0, 1])
    assert groups == [[0, 1]]

    groups = AB.fix_groups(blocks, [0, 2])
    # 中间隔了一段，断开；含 AI 特征的组排前面
    assert groups == [[2], [0]]


def test_revision_changes_aligns_by_block_id():
    article = AB.build_article(markdown="# T\n\n甲段。\n\n乙段。\n")
    first = AB.snapshot_payload(article)
    article["blocks"][0]["text"] = "甲段改过。"
    second = AB.snapshot_payload(article)
    article["blocks"].append({"id": "new", "text": "新增段。", "kind": "para", "check": None,
                              "prev_text": None, "inserted_diagram_id": None})

    revisions = AB.revision_changes(
        [{"label": "手动改稿", "at": "t1", "payload": first},
         {"label": "加一段", "at": "t2", "payload": second}],
        article,
    )
    assert [row["label"] for row in revisions] == ["加一段", "手动改稿"]
    assert revisions[0]["rows"][0]["kind"] == "insert"
    assert revisions[1]["rows"][0]["kind"] == "rewrite"


def test_undo_restores_previous_snapshot():
    article = AB.build_article(markdown="# T\n\n原文。\n")
    history = [{"label": "手动改稿", "payload": AB.snapshot_payload(article)}]
    article["blocks"][0]["text"] = "改过。"
    label = AB.undo(article, history)
    assert label == "手动改稿"
    assert article["blocks"][0]["text"] == "原文。"
    assert history == []


def test_readiness_counts_pending_quality():
    article = AB.build_article(markdown="# T\n\n一段。\n")
    article["id"] = "x"
    article["title"] = "T"
    article["zhuque"] = {"round": 1}
    article["blocks"][0]["check"] = {"status": "flag"}
    meta = AB.list_meta(article)
    assert meta["review"] == 1
    assert [item["done"] for item in meta["readiness"]["items"]] == [True, True, False, False, False]


def test_editor_insert_syntax_survives_split_and_roundtrip():
    """编辑器「插入」菜单产出的片段：分段后各自成块，回拼不丢字符。

    这条守的是「插入一张卡片 → 保存 → 段落数变多但内容还在」这个链路：
    分段按空行切，围栏里的空行不能把代码块切断；回拼要能还原原文。
    """
    fence = "`" * 3
    markdown = "\n\n".join([
        "# 标题",
        "正文。",
        "> [!TIP] 小技巧",
        "> [!QUOTE] 金句",
        fence + "bash 命令\necho hi\n\necho 空行之后\n" + fence,
        fence + "promptbox 滑动查看\n第一行\n\n第二行\n" + fence,
        '![图注](<https://example.com/a.png> "long")',
        "**加粗** ==色块== ++下划线++",
        "---",
    ])
    article = AB.build_article(markdown=markdown)
    assert [block["kind"] for block in article["blocks"]] == [
        "para", "quote", "quote", "code", "code", "media", "para", "rule",
    ]
    assert "echo 空行之后" in article["blocks"][3]["text"]
    assert "第二行" in article["blocks"][4]["text"]
    assert AB.to_markdown(article).strip() == markdown.strip()
