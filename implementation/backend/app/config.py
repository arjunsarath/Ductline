"""Backend configuration — loaded once at startup.

Knobs only exist when an intent in the spec set requires runtime variation:
  - VLM provider swap (ADR-0002)
  - Working DPI (SOLUTION-DESIGN §11, open until first drawing)
  - Upload size cap (SOLUTION-DESIGN §9)
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # VLM provider — ollama for dev, claude reserved for later (ADR-0002).
    vlm_provider: str = "ollama"
    ollama_host_url: str = "http://host.docker.internal:11434"
    ollama_model: str = "llama3.2-vision"
    anthropic_api_key: str | None = None  # Provisioned only; not wired in v1.

    # Ingest — 200 DPI is the starting point per SOLUTION-DESIGN §11.
    raster_dpi: int = 200

    # Upload limits — SOLUTION-DESIGN §9.
    max_upload_bytes: int = 50 * 1024 * 1024
    max_image_dimension_px: int = 8000


settings = Settings()
