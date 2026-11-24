"""平台兜底模型：隔离、优先级及显式选择。"""
import pytest


@pytest.fixture
def model_rows(client):
    from app import models
    from app.core.db import SessionLocal
    from app.core.security import encrypt_secret

    with SessionLocal() as db:
        owner = models.User(id="platform-gateway-owner", auth_subject="platform-gateway-owner", email="platform-gateway-owner@example.test", display_name="owner")
        user = models.User(id="platform-gateway-user", auth_subject="platform-gateway-user", email="platform-gateway-user@example.test", display_name="user")
        db.merge(owner)
        db.merge(user)
        db.flush()
        conn = models.ModelConnection(id="platform-gateway-conn", owner_user_id=owner.id, display_name="platform", base_url="https://example.test/v1", credential_ciphertext=encrypt_secret("key"))
        personal = models.ModelConnection(id="platform-gateway-personal", owner_user_id=user.id, display_name="personal", base_url="https://example.test/v1", credential_ciphertext=encrypt_secret("key"))
        db.merge(conn)
        db.merge(personal)
        db.flush()
        entries = [
            models.ModelCatalogEntry(id="platform-gateway-text", connection_id=conn.id, model_code="platform-text", capabilities=["text.generate"], enabled=True),
            models.ModelCatalogEntry(id="platform-gateway-image", connection_id=conn.id, model_code="platform-image", capabilities=["image.generate"], enabled=True),
            models.ModelCatalogEntry(id="platform-gateway-private", connection_id=conn.id, model_code="private", capabilities=["text.generate"], enabled=True),
            models.ModelCatalogEntry(id="platform-gateway-mine", connection_id=personal.id, model_code="mine", capabilities=["text.generate"], enabled=True),
        ]
        for entry in entries:
            db.merge(entry)
        db.merge(models.PlatformModelSettings(id="platform", default_text_model_id="platform-gateway-text", default_image_model_id="platform-gateway-image"))
        db.commit()
    yield
    with SessionLocal() as db:
        row = db.get(models.PlatformModelSettings, "platform")
        if row:
            db.delete(row)
            db.commit()


def test_platform_text_authorization_and_priority(model_rows):
    from app.core.db import SessionLocal
    from app.services import ai_gateway
    with SessionLocal() as db:
        selection = ai_gateway.resolve_selection(db, "platform-gateway-user")
        assert selection["entryIds"] == ["platform-gateway-mine", "platform-gateway-text"]
        assert ai_gateway.resolve_selection(db, "platform-gateway-user", "platform-gateway-text")["pinned"]
        with pytest.raises(ai_gateway.InvalidModelSelection):
            ai_gateway.resolve_selection(db, "platform-gateway-user", "platform-gateway-private")


def test_automatic_text_fallback_and_explicit_pinning(model_rows, monkeypatch):
    import asyncio
    from app.core.db import SessionLocal
    from app.services import ai_gateway
    seen = []
    async def fake(db, **kwargs):
        code = kwargs["model_code"]
        seen.append(code)
        return {"text": "ok" if code == "platform-text" else "", "modelCode": code, "errorCode": None if code == "platform-text" else "provider_unavailable"}
    monkeypatch.setattr(ai_gateway, "_collect", fake)
    with SessionLocal() as db:
        result = asyncio.run(ai_gateway.generate_text(db, owner_user_id="platform-gateway-user", task_type="article", prompt="hello"))
        assert seen == ["mine", "platform-text"]
        assert result["fallbackUsed"] and result["modelId"] == "platform-gateway-text"
        seen.clear()
        result = asyncio.run(ai_gateway.generate_text(db, owner_user_id="platform-gateway-user", task_type="article", prompt="hello", model_id="platform-gateway-mine"))
        assert seen == ["mine"] and result["errorCode"]


def test_platform_image_allowed_and_foreign_rejected(model_rows, monkeypatch):
    from app.core.db import SessionLocal
    from app.services import image_provider
    monkeypatch.setattr(image_provider, "_env_provider", lambda: None)
    with SessionLocal() as db:
        assert image_provider.resolve_image_provider(db, "platform-gateway-user")["modelId"] == "platform-gateway-image"
        assert image_provider.resolve_image_provider(db, "platform-gateway-user", "platform-gateway-private") is None
        assert any(row["source"] == "platform" for row in image_provider.list_providers(db, "platform-gateway-user"))
