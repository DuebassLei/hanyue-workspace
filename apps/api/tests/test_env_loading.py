"""apps/api/.env 必须对所有模块可见，而不只是 Pydantic Settings。

image_provider / queue / connectors 直接用 os.getenv 读取
IMAGE_PROVIDER_*、REDIS_URL、PLATFORM_*；若 config 只依赖 env_file 填充
Settings 对象，这些键会被静默忽略（文档中的启用步骤会失效）。
"""

from __future__ import annotations

import os

from app.core import config

SYNCED_KEYS = (
    "IMAGE_PROVIDER_BASE_URL",
    "IMAGE_PROVIDER_API_KEY",
    "IMAGE_PROVIDER_MODEL",
    "REDIS_URL",
    "PLATFORM_EVIDENCE_FILE",
)


def test_env_file_syncs_os_getenv_keys(monkeypatch, tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "# 注释行\n"
        "\n"
        'IMAGE_PROVIDER_BASE_URL="https://api.example.com/v1"\n'
        "IMAGE_PROVIDER_API_KEY='sk-test'\n"
        "IMAGE_PROVIDER_MODEL=gpt-image-1\n"
        "export REDIS_URL=redis://127.0.0.1:6379/0\n"
        "PLATFORM_EVIDENCE_FILE=../../infra/platform-evidence.json\n"
        "SECRET_KEY=should-not-override\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(config, "ENV_FILE", env_file)
    for key in SYNCED_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("SECRET_KEY", "external-wins")

    try:
        config._load_env_file()

        assert os.environ["IMAGE_PROVIDER_BASE_URL"] == "https://api.example.com/v1"
        assert os.environ["IMAGE_PROVIDER_API_KEY"] == "sk-test"
        assert os.environ["IMAGE_PROVIDER_MODEL"] == "gpt-image-1"
        assert os.environ["REDIS_URL"] == "redis://127.0.0.1:6379/0"
        assert os.environ["PLATFORM_EVIDENCE_FILE"] == "../../infra/platform-evidence.json"
        # 外部注入的环境变量优先，不被 .env 覆盖
        assert os.environ["SECRET_KEY"] == "external-wins"
    finally:
        for key in SYNCED_KEYS:
            os.environ.pop(key, None)


def test_missing_env_file_is_noop(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "ENV_FILE", tmp_path / "not-there.env")
    monkeypatch.delenv("IMAGE_PROVIDER_MODEL", raising=False)
    config._load_env_file()
    assert os.environ.get("IMAGE_PROVIDER_MODEL") is None
