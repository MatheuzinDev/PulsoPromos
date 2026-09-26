from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = "postgresql+psycopg://pulso:pulso@localhost:5432/pulso"
    environment: str = "development"

    # Intervalo de coleta por marketplace, em minutos (RF09; defaults dentro do RNF05: 30 a 60).
    coleta_intervalo_shopee_min: int = Field(default=30, ge=1)
    coleta_intervalo_aliexpress_min: int = Field(default=45, ge=1)
    coleta_intervalo_mercado_livre_min: int = Field(default=30, ge=1)
    coleta_intervalo_amazon_min: int = Field(default=60, ge=1)

    def intervalo_coleta_min(self, marketplace: str) -> int:
        return int(getattr(self, f"coleta_intervalo_{marketplace}_min"))


@lru_cache
def get_settings() -> Settings:
    return Settings()
