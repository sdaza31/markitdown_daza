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


def _build_converter() -> tuple[MarkItDown, bool]:
    """Build the shared MarkItDown instance.

    If an LLM API key is configured (LLM_API_KEY), wire up an OpenAI-compatible
    client and enable the markitdown-ocr plugin so images inside PDFs/DOCX/PPTX/
    XLSX — and fully scanned PDFs — get transcribed via the LLM. Works with any
    OpenAI-compatible endpoint (OpenAI, Anthropic compat, OpenRouter, Groq,
    Together, local models…) via LLM_BASE_URL.

    Without a key it falls back to the standard, fully-local converters.

    Returns (converter, ocr_enabled).
    """
    api_key = os.environ.get("LLM_API_KEY", "").strip()
    base_url = os.environ.get("LLM_BASE_URL", "").strip() or None
    model = os.environ.get("LLM_MODEL", "gpt-4o").strip()
    prompt = os.environ.get("LLM_PROMPT", "").strip() or None

    if api_key:
        try:
            from openai import OpenAI

            client = OpenAI(api_key=api_key, base_url=base_url)
            md = MarkItDown(
                enable_plugins=True,
                llm_client=client,
                llm_model=model,
                llm_prompt=prompt,
            )
            return md, True
        except Exception as exc:  # noqa: BLE001 - degrade gracefully, never crash boot
            print(f"[markitdown-web] OCR disabled: could not init LLM client: {exc}")

    # No key (or init failed): standard local conversion. Honour the legacy
    # MARKITDOWN_PLUGINS flag in case other plugins are installed.
    enable_plugins = os.environ.get("MARKITDOWN_PLUGINS", "0") == "1"
    return MarkItDown(enable_plugins=enable_plugins), False


# A single MarkItDown instance is reused across requests.
converter, OCR_ENABLED = _build_converter()
print(f"[markitdown-web] LLM OCR {'ENABLED' if OCR_ENABLED else 'disabled'}")


# --- API -------------------------------------------------------------------


@app.get("/api/health")
def health() -> dict:
    """Lightweight health check for Dokploy / load balancers."""
    return {"status": "ok", "ocr": OCR_ENABLED}


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
