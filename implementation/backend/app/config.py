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
    # Default model is qwen3-vl:235b-cloud (Ollama Cloud) — purpose-built for
    # vision tasks at ~20× the parameter count of llama3.2-vision (11B local).
    # llama3.2-vision remains a valid override via env var; the swap is a
    # config change only, per ADR-0002's pluggable VLMClient seam.
    vlm_provider: str = "ollama"
    ollama_host_url: str = "http://host.docker.internal:11434"
    ollama_model: str = "qwen3-vl:235b-cloud"
    anthropic_api_key: str | None = None  # Provisioned only; not wired in v1.

    # Ingest — 200 DPI is the starting point per SOLUTION-DESIGN §11.
    raster_dpi: int = 200
    # ADR-0007 — DPI for the vector-PDF raster_probe (full-sheet image used by
    # stages that still need a raster to reason over). Lower than raster_dpi
    # because vector tiles re-render losslessly on demand.
    probe_dpi: int = 150
    # SOLUTION-DESIGN-V2 §5.2 — safety cap on smart-DPI re-renders. 600 DPI on
    # the largest benchmark tile is the upper bound before Ollama's request
    # payload limit becomes a risk (open question §9.3).
    smart_dpi_ceiling: int = 600
    # SOLUTION-DESIGN-V2 §5.2 — target pixel height for the smallest text in a
    # tile. ~22 px is the readability floor for llama3.2-vision's tokenizer.
    probe_text_target_px: int = 22

    # Upload limits — SOLUTION-DESIGN §9.
    max_upload_bytes: int = 50 * 1024 * 1024
    max_image_dimension_px: int = 8000


settings = Settings()
