"""FastAPI entrypoint.

Stateless backend (ADR-0005). Routes live in app.api; this module only wires
middleware and mounts the router.
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router

app = FastAPI(title="HVAC Duct Detection", version="0.1.0")

# CORS open in dev — frontend runs on a different port via Vite.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
