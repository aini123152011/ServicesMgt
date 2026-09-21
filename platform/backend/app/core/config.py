import warnings
from pathlib import Path
from typing import Literal, Self

from pydantic import (
    EmailStr,
    HttpUrl,
    PostgresDsn,
    computed_field,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        # Use the top-level .env at the repo root; the relative form "../.env" fails to resolve
        # after backend was moved under platform/, so anchor it to the file location instead of CWD
        env_file=Path(__file__).resolve().parents[4] / ".env",
        env_ignore_empty=True,
        extra="ignore",
    )
    API_V1_STR: str = "/api/v1"
    SECRET_KEY: str
    # 60 minutes * 24 hours * 8 days = 8 days
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 8
    FRONTEND_HOST: str = "http://localhost:5173"
    FASTAPI_ENV: Literal["development"] | None = None

    PROJECT_NAME: str
    SENTRY_DSN: HttpUrl | None = None
    DATABASE_URL: PostgresDsn

    @field_validator("DATABASE_URL", mode="before")
    @classmethod
    def _use_psycopg_driver(cls, value: str | PostgresDsn) -> str:
        database_url = str(value)
        for scheme in ("postgres://", "postgresql://"):
            if database_url.startswith(scheme):
                return database_url.replace(scheme, "postgresql+psycopg://", 1)
        return database_url

    # 平台版本与构建号：镜像构建时经 Dockerfile 的 ARG/ENV 注入；缺省 dev 便于本地运行
    PLATFORM_VERSION: str = "dev"
    PLATFORM_BUILD: str = ""
    # 可选的镜像仓库（如 registry.local:5000）：配置后「检查更新」会先尝试 docker pull，
    # 未配置则只认离线包导入的镜像
    UPDATE_REGISTRY: str = ""
    # 平台自身容器名：自更新需要用它定位自己（部署名可不同，故进配置而非硬编码）
    PLATFORM_CONTAINER_NAME: str = "bmc-platform-backend"
    # 服务插件目录（含 manifest.yaml 的子目录）；相对路径按后端项目目录解析
    SERVICES_DIR: str = "../../services"
    # 配置卷在宿主机上的根路径，每个服务的卷按 {VOLUMES_MOUNT_ROOT}/{name}-config 挂载
    VOLUMES_MOUNT_ROOT: str = "/var/lib/platform"

    @field_validator("SERVICES_DIR", mode="after")
    @classmethod
    def _resolve_services_dir(cls, value: str) -> str:
        # 相对路径以后端项目目录（pyproject.toml 所在处）展开为绝对路径，不依赖进程工作目录
        services_dir = Path(value)
        if not services_dir.is_absolute():
            services_dir = Path(__file__).resolve().parents[2] / services_dir
        return str(services_dir)

    SMTP_TLS: bool = True
    SMTP_SSL: bool = False
    SMTP_PORT: int = 587
    SMTP_HOST: str | None = None
    SMTP_USER: str | None = None
    SMTP_PASSWORD: str | None = None
    EMAILS_FROM_EMAIL: EmailStr | None = None
    EMAILS_FROM_NAME: str | None = None

    @model_validator(mode="after")
    def _set_default_emails_from(self) -> Self:
        if not self.EMAILS_FROM_NAME:
            self.EMAILS_FROM_NAME = self.PROJECT_NAME
        return self

    EMAIL_RESET_TOKEN_EXPIRE_HOURS: int = 48

    @computed_field  # type: ignore[prop-decorator]
    @property
    def emails_enabled(self) -> bool:
        return bool(self.SMTP_HOST and self.EMAILS_FROM_EMAIL)

    EMAIL_TEST_USER: EmailStr = "test@example.com"
    FIRST_SUPERUSER: EmailStr
    FIRST_SUPERUSER_PASSWORD: str

    def _check_default_secret(self, var_name: str, value: str | None) -> None:
        if value == "changethis":
            message = (
                f'The value of {var_name} is "changethis", '
                "for security, please change it, at least for deployments."
            )
            if self.FASTAPI_ENV == "development":
                warnings.warn(message, stacklevel=1)
            else:
                raise ValueError(message)

    @model_validator(mode="after")
    def _enforce_non_default_secrets(self) -> Self:
        self._check_default_secret("SECRET_KEY", self.SECRET_KEY)
        for host in self.DATABASE_URL.hosts():
            self._check_default_secret("DATABASE_URL password", host["password"])
        self._check_default_secret(
            "FIRST_SUPERUSER_PASSWORD", self.FIRST_SUPERUSER_PASSWORD
        )

        return self


settings = Settings()  # type: ignore # ty: ignore[unused-ignore-comment]
