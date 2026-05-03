# VLM setup

V2 ships with **`qwen3-vl:235b-cloud`** as the default VLM (Ollama Cloud).
Three earlier rounds of tuning against `llama3.2-vision` (11B local) hit a
small-model ceiling on "where is X on this page" tasks; qwen3-vl is roughly
20× larger and purpose-built for vision. The swap is a config change only —
ADR-0002's pluggable `VLMClient` seam handles it transparently.

## Setup — qwen3-vl:235b-cloud (default)

1. **Install Ollama.** Follow the install instructions at
   https://ollama.com/download for your platform.

2. **Sign in to Ollama Cloud.** Cloud-tagged models (any model with the
   `:Nb-cloud` suffix) require an authenticated Ollama session. The exact
   command depends on your Ollama version — check the current docs at
   https://ollama.com/cloud and use whichever of `ollama signin` /
   `ollama login` your local CLI exposes. Ollama caches the credential and
   the backend never sees it.

3. **Pull the model.**

   ```sh
   ollama pull qwen3-vl:235b-cloud
   ```

   (Cloud models do not occupy local disk — the pull registers the model
   tag with your local Ollama daemon so requests can route to it.)

4. **Confirm Ollama is reachable from the backend.** The backend talks
   to Ollama at `settings.ollama_host_url`, defaulting to
   `http://host.docker.internal:11434` (the Docker-on-Mac/Windows mapping
   to the host's `localhost:11434`). On Linux the `extra_hosts:` entry
   in `implementation/docker-compose.yml` provides the same mapping.
   If you've moved Ollama to a different port or host, override
   `OLLAMA_HOST_URL` in your environment.

5. **Run a smoke test.** With the backend up, upload any HVAC drawing
   through the UI; the page-categorizer stage will issue the first VLM
   call. Cloud calls take longer than local — the backend's HTTP timeout
   is `_OLLAMA_TIMEOUT_S = 240s` (in `app/vlm/ollama.py`) to absorb the
   higher 99p latency of internet-bound requests.

## Switching back to llama3.2-vision (or any other model)

Override the model via env var. Both `OLLAMA_MODEL` (read by docker-compose
into the backend container) and the underlying `ollama_model` setting
accept any tag your local Ollama daemon serves.

```sh
# Local model — must be pulled first.
ollama pull llama3.2-vision
OLLAMA_MODEL=llama3.2-vision docker compose up backend
```

The codebase carries per-VLM prompt directories at
`implementation/backend/app/vlm/prompts/{model_slug}/...` — the slug is
the model name with any `:tag` stripped (e.g.
`qwen3-vl:235b-cloud` → `qwen3-vl`, `llama3.2-vision:latest` →
`llama3.2-vision`). When a model-specific prompt exists it is preferred
over the default at `prompts/{filename}`; this is how each model carries
its own tuned variants without cross-model regression risk.

To add a new model, place its prompts in a sibling directory matching
its slug; no code change is required.

## Cloud model caveats

- **Quota / rate limits.** Ollama Cloud's free tier has request limits
  that a long pipeline run (~6 VLM calls per drawing in the categorizer
  alone, plus per-tile detect calls) can hit. If you see `429`-class
  errors surface as `VLMError`, check your Ollama Cloud account dashboard
  for current quota.
- **Network dependency.** Unlike local llama3.2-vision, every cloud call
  is an internet round-trip. The 240s timeout is generous but not
  infinite; flaky connectivity will surface as `VLMError` on whichever
  stage was mid-call.
- **Model availability.** Ollama Cloud may rotate or deprecate model
  tags. If `qwen3-vl:235b-cloud` ever disappears, the backend will fail
  fast on first request — swap to the closest available tag via
  `OLLAMA_MODEL` and (ideally) seed a matching `prompts/{slug}/`
  directory copying from `prompts/qwen3-vl/`.
