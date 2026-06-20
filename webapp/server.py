"""
MarkItDown Web — a small FastAPI front-end around the MarkItDown library.

Serves a single-page dark-mode UI at "/" and a JSON conversion endpoint at
"/api/convert" that accepts an uploaded file and returns the resulting Markdown.
"""

from __future__ import annotations

import io
import os
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from markitdown import MarkItDown, StreamInfo
from markitdown._exceptions import (
    FileConversionException,
    UnsupportedFormatException,
)

# --- Configuration ---------------------------------------------------------

# Max upload size in megabytes (override with MARKITDOWN_MAX_UPLOAD_MB).
MAX_UPLOAD_MB = int(os.environ.get("MARKITDOWN_MAX_UPLOAD_MB", "50"))
MAX_UPLOAD_BYTES = MAX_UPLOAD_MB * 1024 * 1024

STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(title="MarkItDown Web", docs_url="/api/docs", redoc_url=None)

# A single MarkItDown instance is reused across requests. Plugins are disabled
# by default so behaviour stays predictable; flip on with MARKITDOWN_PLUGINS=1.
_enable_plugins = os.environ.get("MARKITDOWN_PLUGINS", "0") == "1"
converter = MarkItDown(enable_plugins=_enable_plugins)


# --- API -------------------------------------------------------------------


@app.get("/api/health")
def health() -> dict:
    """Lightweight health check for Dokploy / load balancers."""
    return {"status": "ok"}


@app.post("/api/convert")
async def convert(file: UploadFile = File(...)) -> JSONResponse:
    """Convert an uploaded file to Markdown."""
    data = await file.read()

    if len(data) == 0:
        raise HTTPException(status_code=400, detail="El archivo está vacío.")
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"El archivo supera el límite de {MAX_UPLOAD_MB} MB.",
        )

    filename = file.filename or "archivo"
    extension = Path(filename).suffix or None

    stream_info = StreamInfo(
        filename=filename,
        extension=extension,
        mimetype=file.content_type,
    )

    try:
        result = converter.convert_stream(io.BytesIO(data), stream_info=stream_info)
    except UnsupportedFormatException:
        raise HTTPException(
            status_code=415,
            detail="Formato no soportado. Prueba con PDF, Word, Excel, PowerPoint, "
            "imágenes, HTML, CSV/JSON/XML, EPUB, ZIP, etc.",
        )
    except FileConversionException as exc:
        raise HTTPException(status_code=422, detail=f"No se pudo convertir: {exc}")
    except Exception as exc:  # noqa: BLE001 - surface a clean error to the client
        raise HTTPException(status_code=500, detail=f"Error inesperado: {exc}")

    return JSONResponse(
        {
            "filename": filename,
            "title": result.title,
            "markdown": result.markdown,
            "characters": len(result.markdown),
        }
    )


# --- Static front-end ------------------------------------------------------


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


# Serve any other static assets (favicon, etc.) from /static.
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
