"""受信脚本运行时 SDK：脚本只能通过这些函数访问 API。"""

from __future__ import annotations

import json
import os
import sys
from typing import Any

import requests


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _call(method: str, path: str, **kwargs: Any) -> dict:
    base = _env("HANYUE_SCRIPT_SDK_BASE_URL")
    token = _env("HANYUE_SCRIPT_SDK_TOKEN")
    if not base:
        _fail("script_sdk_base_url_missing", "缺少 HANYUE_SCRIPT_SDK_BASE_URL")
    if not token:
        _fail("script_sdk_token_missing", "缺少 HANYUE_SCRIPT_SDK_TOKEN")
    headers = {"Authorization": f"Bearer {token}"}
    headers.update(kwargs.pop("headers", {}))
    url = f"{base.rstrip('/')}{path}"
    try:
        response = requests.request(method, url, headers=headers, timeout=30, **kwargs)
        response.raise_for_status()
        data = response.json()
    except requests.RequestException as exc:
        detail = getattr(exc, "response", None)
        message = detail.text if detail is not None and detail.text else str(exc)
        _fail("script_sdk_http_error", message)
    except (ValueError, json.JSONDecodeError) as exc:
        _fail("script_sdk_response_error", str(exc))
    return data if isinstance(data, dict) else {"data": data}


def _fail(code: str, message: str) -> None:
    payload = json.dumps({"code": code, "message": message}, ensure_ascii=False)
    print(payload, file=sys.stderr)
    raise SystemExit(1)


def get_params() -> dict:
    try:
        value = json.loads(_env("HANYUE_SCRIPT_SDK_PARAMS") or "{}")
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def get_context() -> dict:
    try:
        value = json.loads(_env("HANYUE_SCRIPT_SDK_CONTEXT") or "{}")
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def get_workspaces() -> list[dict]:
    value = _call("GET", "/workspaces")
    items = value.get("items", [])
    return items if isinstance(items, list) else []


def list_workspaces() -> list[dict]:
    return get_workspaces()


def create_draft(title: str, body: str = "") -> dict:
    if not isinstance(title, str):
        raise TypeError("title 必须是字符串")
    workspace_id = _env("HANYUE_SCRIPT_SDK_WORKSPACE_ID")
    if not workspace_id:
        workspaces = get_workspaces()
        if not workspaces:
            _fail("script_sdk_workspace_missing", "没有可用工作区")
        workspace_id = str(workspaces[0].get("id") or "")
    created = _call(
        "POST", "/content", json={"title": title, "type": "article"}
    )
    content_id = created.get("id")
    if not content_id:
        _fail("script_sdk_content_missing", "创建稿件响应缺少 id")
    _call(
        "PATCH", f"/content/{content_id}/draft", json={"title": title, "body": body}
    )
    return {"contentItemId": content_id}


def log(*values: object) -> None:
    print(*values, file=sys.stdout, flush=True)
