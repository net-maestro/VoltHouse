from typing import List
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import field_validator


class Settings(BaseSettings):
    # Telegram
    BOT_TOKEN: str = "1234567890:AAH_placeholder"
    ADMIN_IDS: str = ""

    # MySQL
    MYSQL_HOST: str = "localhost"
    MYSQL_PORT: int = 3306
    MYSQL_USER: str = "deye_user"
    MYSQL_PASSWORD: str = "deye_secret_password"
    MYSQL_DATABASE: str = "deye_monitor"

    # Redis
    REDIS_HOST: str = "localhost"
    REDIS_PORT: int = 6379
    REDIS_DB: int = 0
    REDIS_PASSWORD: str = ""

    # Deye Cloud OpenAPI
    DEYE_BASE_URL: str = "https://eu1-developer.deyecloud.com"
    DEYE_APP_ID: str = ""
    DEYE_APP_SECRET: str = ""
    DEYE_EMAIL: str = ""
    DEYE_PASSWORD: str = ""
    DEYE_MOCK_MODE: bool = True

    # Monitoring thresholds
    POLL_INTERVAL_SECONDS: int = 300
    ELEVATOR_CRITICAL_SOC: float = 20.0
    VOLTAGE_MIN: float = 207.0
    VOLTAGE_MAX: float = 253.0
    TIMEZONE: str = "Europe/Kyiv"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @property
    def admin_id_list(self) -> List[int]:
        if not self.ADMIN_IDS:
            return []
        result = []
        for item in self.ADMIN_IDS.split(","):
            item = item.strip()
            if item.isdigit():
                result.append(int(item))
        return result

    @property
    def database_url(self) -> str:
        return (
            f"mysql+aiomysql://{self.MYSQL_USER}:{self.MYSQL_PASSWORD}"
            f"@{self.MYSQL_HOST}:{self.MYSQL_PORT}/{self.MYSQL_DATABASE}?charset=utf8mb4"
        )

    @property
    def redis_url(self) -> str:
        if self.REDIS_PASSWORD:
            return f"redis://:{self.REDIS_PASSWORD}@{self.REDIS_HOST}:{self.REDIS_PORT}/{self.REDIS_DB}"
        return f"redis://{self.REDIS_HOST}:{self.REDIS_PORT}/{self.REDIS_DB}"


settings = Settings()
