"""FastAPI entrypoint.

Stateless backend (ADR-0005). Routes live in app.api; this module only wires
middleware and mounts the routers.

Two pipelines are mounted:

  • V3 (default, ``/api/v3/...``) — color-driven deterministic pipeline,
    SOLUTION-DESIGN-V3. This is the active product surface as of the V3
    pivot. The frontend calls these routes for end-to-end detection.

  • V1/V2 agent pipeline (``/api/agent/...``) — the original VLM-driven
    pipeline. Parked behind the ``agent`` prefix because we may revive it
    when a sufficiently-capable on-prem VLM lands. Code lives at the
    same paths; only the URL prefix changed.
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router as agent_router
from app.api.v3_routes import router as v3_router

app = FastAPI(title="HVAC Duct Detection", version="0.3.0")

# CORS open in dev — frontend runs on a different port via Vite.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Vite dev proxy strips ``/api`` before forwarding, so backend prefixes
# are bare (``/v3``, ``/agent``). Frontend calls e.g. ``/api/v3/render``
# → proxy strips ``/api`` → backend serves ``/v3/render``.
app.include_router(v3_router, prefix="/v3", tags=["v3"])
app.include_router(agent_router, prefix="/agent", tags=["agent (parked)"])


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
