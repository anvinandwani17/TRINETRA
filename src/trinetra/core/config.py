"""Environment settings for the initial API."""

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Settings load from the environment and a root .env when run there."""

    app_name: str = "TRINETRA"
    metadata_cache_dir: str = "data/cache/s1_metadata"
    cdse_username: SecretStr = Field(default=SecretStr(""), validation_alias="CDSE_USERNAME")
    cdse_password: SecretStr = Field(default=SecretStr(""), validation_alias="CDSE_PASSWORD")
    cdse_s3_access_key: SecretStr = Field(default=SecretStr(""), validation_alias="CDSE_S3_ACCESS_KEY")
    cdse_s3_secret_key: SecretStr = Field(default=SecretStr(""), validation_alias="CDSE_S3_SECRET_KEY")

    model_config = SettingsConfigDict(
        env_prefix="TRINETRA_", env_file=".env", env_file_encoding="utf-8",
        extra="ignore",
    )
