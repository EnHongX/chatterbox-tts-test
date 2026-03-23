from __future__ import annotations

from contextlib import asynccontextmanager
import subprocess
import threading
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import uvicorn

from tts_service import (
    BASE_DIR,
    DEFAULT_REFERENCE_AUDIO,
    OUTPUT_DIR,
    list_reference_audios,
    tts_service,
)


OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


@asynccontextmanager
async def lifespan(_: FastAPI):
    threading.Thread(target=tts_service.preload, daemon=True).start()
    yield


app = FastAPI(title="Chatterbox Turbo TTS MVP", lifespan=lifespan)
app.mount("/output", StaticFiles(directory=str(OUTPUT_DIR)), name="output")


class GenerateRequest(BaseModel):
    text: str
    reference_audio_path: str | None = None
    mode: str = "fast"


class OpenPathRequest(BaseModel):
    path: str | None = None


@app.get("/", response_class=HTMLResponse)
async def index() -> HTMLResponse:
    html_path = BASE_DIR / "templates" / "index.html"
    return HTMLResponse(html_path.read_text(encoding="utf-8"))


@app.get("/favicon.ico")
async def favicon() -> Response:
    return Response(status_code=204)


@app.get("/api/config")
async def get_config() -> dict:
    reference_audios = list_reference_audios()
    return {
        "default_reference_audio_path": str(DEFAULT_REFERENCE_AUDIO.relative_to(BASE_DIR)),
        "output_dir": str(OUTPUT_DIR.relative_to(BASE_DIR)),
        "reference_audio_options": reference_audios,
        "generation_modes": [
            {"value": "fast", "label": "极速"},
            {"value": "standard", "label": "标准"},
        ],
        "default_generation_mode": "fast",
    }


@app.post("/api/generate")
async def generate_audio(payload: GenerateRequest) -> dict:
    try:
        result = tts_service.generate(
            text=payload.text,
            audio_prompt_path=payload.reference_audio_path or str(DEFAULT_REFERENCE_AUDIO),
            mode=payload.mode,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"TTS generation failed: {exc}") from exc

    relative_output_path = result.output_path.relative_to(BASE_DIR)
    return {
        "status": result.status,
        "elapsed_seconds": round(result.elapsed_seconds, 2),
        "output_file_path": str(relative_output_path),
        "audio_url": f"/{relative_output_path.as_posix()}",
        "reference_audio_path": str(result.reference_audio_path),
        "mode": result.mode,
        "segment_count": result.segment_count,
    }


def _resolve_project_path(path_value: str | None) -> Path:
    if not path_value:
        raise HTTPException(status_code=400, detail="Path is required.")

    candidate = Path(path_value)
    if not candidate.is_absolute():
        candidate = (BASE_DIR / candidate).resolve()
    else:
        candidate = candidate.resolve()

    try:
        candidate.relative_to(BASE_DIR)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Path must be inside the project directory.") from exc

    if not candidate.exists():
        raise HTTPException(status_code=404, detail=f"Path not found: {candidate}")

    return candidate


@app.post("/api/open-output-dir")
async def open_output_dir() -> dict:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    subprocess.run(["open", str(OUTPUT_DIR)], check=True)
    return {"status": "opened", "path": str(OUTPUT_DIR)}


@app.post("/api/reveal-path")
async def reveal_path(payload: OpenPathRequest) -> dict:
    target_path = _resolve_project_path(payload.path)
    subprocess.run(["open", "-R", str(target_path)], check=True)
    return {"status": "opened", "path": str(target_path)}


if __name__ == "__main__":
    uvicorn.run("app:app", host="127.0.0.1", port=8010, reload=False)
