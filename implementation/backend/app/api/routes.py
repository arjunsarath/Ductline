"""API routes — POST /detect, GET /samples (SOLUTION-DESIGN §3.1, §5.2).

Thin HTTP layer over `DetectionPipeline`. Exception types map directly to
HTTP status codes so the frontend can surface specific errors verbatim.

`/samples` exposes the bundled benchmark drawings under `/drawings/` (mounted
read-only by docker-compose) so the "Try a sample" UI affordance can list and
download them without needing a separate static-file server.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse

from app.api.deps import get_pipeline
from app.pipeline.base import PipelineError
from app.pipeline.runner import DetectionPipeline
from app.schemas import DrawingResult, SampleDrawing

router = APIRouter()

_SAMPLES_DIR = Path("/drawings")
_SAMPLE_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg"}


@router.post("/detect", response_model=DrawingResult)
async def detect(
    file: UploadFile = File(...),
    pipeline: DetectionPipeline = Depends(get_pipeline),
) -> DrawingResult:
    file_bytes = await file.read()
    try:
        return pipeline.run(file_bytes, original_filename=file.filename or "uploaded")
    except PipelineError as exc:
        raise HTTPException(status_code=exc.http_status, detail=str(exc)) from exc


@router.get("/samples", response_model=list[SampleDrawing])
def list_samples() -> list[SampleDrawing]:
    if not _SAMPLES_DIR.exists():
        return []
    return sorted(
        (
            SampleDrawing(name=p.name, size_bytes=p.stat().st_size)
            for p in _SAMPLES_DIR.iterdir()
            if p.is_file() and p.suffix.lower() in _SAMPLE_EXTENSIONS
        ),
        key=lambda s: s.name,
    )


@router.get("/samples/{name}")
def get_sample(name: str) -> FileResponse:
    # Resolve under SAMPLES_DIR and reject any path-traversal attempt.
    candidate = (_SAMPLES_DIR / name).resolve()
    if _SAMPLES_DIR.resolve() not in candidate.parents and candidate != _SAMPLES_DIR.resolve():
        raise HTTPException(status_code=400, detail="invalid sample name")
    if not candidate.exists() or candidate.suffix.lower() not in _SAMPLE_EXTENSIONS:
        raise HTTPException(status_code=404, detail="sample not found")
    return FileResponse(candidate, filename=candidate.name)
