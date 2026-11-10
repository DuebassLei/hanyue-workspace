from functools import lru_cache
import os
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parents[2]
ENV_FILE = BASE_DIR / ".env"

_DEV_SECRETS = {"dev-only-change-me", "please-change-me", "change-me", "test-secret"}


def _load_env_file() -> None:
    """把 .env 同步进 os.environ，让所有模块读到同一份配置。

    pydantic-settings 的 env_file 只填充 Settings 对象，不会写入 os.environ；
    而 image_provider / queue / connectors 等模块直接用 os.getenv 读
    IMAGE_PROVIDER_*、REDIS_URL、PLATFORM_*。若不注入，`.env` 里这些键会被静默忽略。
    外部注入的真实环境变量优先：只补 os.environ 里尚不存在的键。
    """
    if not ENV_FILE.is_file():
        return
    for raw_line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        key, _, value = line.partition("=")
        key = key.strip()
        if not key or key in os.environ:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        os.environ[key] = value


_load_env_file()


class Settings(BaseSettings):
    """运行时配置。默认使用本地 SQLite，生产通过环境变量切换 PostgreSQL。"""

    model_config = SettingsConfigDict(env_file=str(ENV_FILE), extra="ignore")

    app_name: str = "hanyue-workspace-api"
    api_prefix: str = "/api/v1"
    environment: str = "development"
    secret_key: str = "dev-only-change-me"
    token_ttl_seconds: int = 60 * 60 * 24 * 7
    allow_dev_login: bool = True
    database_url: str = f"sqlite:///{(BASE_DIR / 'hanyue.db').as_posix()}"
    storage_dir: Path = BASE_DIR / "storage"
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    # 任务租约：running 超过该秒数仍未推进即视为进程崩溃，启动时回收
    job_lease_seconds: int = 300
    # 单次上传硬上限（即使不带签名上传会话也生效）
    max_upload_bytes: int = 20 * 1024 * 1024
    # 运行日志：级别、落盘目录（留空则 storage/logs）、单文件上限与保留份数
    log_level: str = "INFO"
    log_dir: Path | None = None
    log_max_bytes: int = 10 * 1024 * 1024
    log_backup_count: int = 5

    @property
    def cors_origin_list(self) -> list[str]:
        return [item.strip() for item in self.cors_origins.split(",") if item.strip()]

    @property
    def is_production(self) -> bool:
        return self.environment.strip().lower() in {"production", "prod"}


def assert_safe_configuration(settings: Settings) -> None:
    """生产启动门禁：宁可拒绝启动，也不带着可预测密钥或任意登录入口上线。"""
    if not settings.is_production:
        return
    problems: list[str] = []
    if len(settings.secret_key) < 32 or settings.secret_key in _DEV_SECRETS:
        problems.append("SECRET_KEY 必须是至少 32 字符的随机值")
    if settings.allow_dev_login:
        problems.append("ALLOW_DEV_LOGIN 必须为 false（禁止任意 subject 换 token）")
    if settings.database_url.startswith("sqlite"):
        problems.append("生产环境必须使用 PostgreSQL，不能沿用 SQLite")
    if problems:
        raise RuntimeError("生产配置不安全，已拒绝启动：" + "；".join(problems))


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    assert_safe_configuration(settings)
    settings.storage_dir.mkdir(parents=True, exist_ok=True)
    return settings
