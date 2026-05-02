"""Provider-name → VLMClient factory (ADR-0002).

The Anthropic provider is provisioned but not implemented in v1 — the user
direction on 2026-05-02 was Ollama-only with the seam ready for a later swap.
"""

from app.config import Settings
from app.vlm.base import VLMClient
from app.vlm.ollama import OllamaVisionClient


def build_vlm_client(settings: Settings) -> VLMClient:
    provider = settings.vlm_provider.lower()
    if provider == "ollama":
        return OllamaVisionClient(
            host_url=settings.ollama_host_url,
            model=settings.ollama_model,
        )
    if provider in {"claude", "anthropic"}:
        raise NotImplementedError(
            "Claude provider not implemented in v1; ADR-0002 keeps the seam ready"
        )
    raise ValueError(f"unknown VLM_PROVIDER: {settings.vlm_provider!r}")
