"""
MarkItDown Web — a small FastAPI front-end around the MarkItDown library.

Serves a single-page dark-mode UI at "/" and a JSON conversion endpoint at
"/api/convert" that accepts an uploaded file and returns the resulting Markdown.
"""

from __future__ import annotations

import ipaddress
import io
import os
from contextvars import ContextVar
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from fastapi import FastAPI, File, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
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

# CORS allowlist for cross-origin browser access. Empty keeps production at
# same-origin only, while localhost/127.0.0.1 origins are reflected in local
# development for front-end dev servers.
_CORS_ALLOWED_ORIGINS = tuple(
    origin.strip()
    for origin in os.environ.get("CORS_ALLOWED_ORIGINS", "").split(",")
    if origin.strip()
)
_CORS_ALLOWED_ORIGIN_SET = set()
for _origin in _CORS_ALLOWED_ORIGINS:
    try:
        parsed = urlsplit(_origin)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError
        _port = f":{parsed.port}" if parsed.port else ""
        _CORS_ALLOWED_ORIGIN_SET.add(f"{parsed.scheme}://{parsed.hostname}{_port}")
    except ValueError:
        print(
            "[markitdown-web] Ignoring invalid CORS_ALLOWED_ORIGINS entry: "
            f"{_origin!r}"
        )

_CORS_ALLOW_METHODS = "GET, POST, OPTIONS"
_CORS_MAX_AGE_SECONDS = "600"

# --- API docs exposure -----------------------------------------------------
# The interactive API docs (Swagger) are DISABLED by default for security —
# they reveal the API surface publicly. Enable only when needed with
# ENABLE_API_DOCS=1 (and ideally behind the IP allowlist below).
_DOCS_ON = os.environ.get("ENABLE_API_DOCS", "0") == "1"

app = FastAPI(
    title="MarkItDown Web",
    docs_url="/api/docs" if _DOCS_ON else None,
    redoc_url=None,
    openapi_url="/api/openapi.json" if _DOCS_ON else None,
)

# --- IP allowlist ----------------------------------------------------------
# Restrict who can reach the site. Set ALLOWED_IPS to a comma-separated list of
# IPs or CIDR ranges (e.g. "181.58.39.244,10.0.0.0/24"). Empty = open to all.
# The IP is read from the X-Forwarded-For header set by the reverse proxy
# (Traefik/Dokploy); see XFF_TRUSTED_HOPS below.
_ALLOWED_NETS: list = []
for _part in os.environ.get("ALLOWED_IPS", "").split(","):
    _part = _part.strip()
    if not _part:
        continue
    try:
        _ALLOWED_NETS.append(ipaddress.ip_network(_part, strict=False))
    except ValueError:
        print(f"[markitdown-web] Ignoring invalid ALLOWED_IPS entry: {_part!r}")

IP_FILTER_ENABLED = len(_ALLOWED_NETS) > 0

# Number of trusted reverse-proxy hops in front of the app. With a single
# Traefik (Dokploy default) leave this at 1. If you also sit behind Cloudflare
# or another CDN, set it to 2 so the real client IP is read correctly.
_XFF_HOPS = max(1, int(os.environ.get("XFF_TRUSTED_HOPS", "1")))

# Paths exempt from the IP filter (health check must stay reachable so Docker /
# Dokploy can probe the container from inside).
_IP_FILTER_EXEMPT = {"/api/health"}


def _client_ip(request: Request) -> str:
    """Resolve the real client IP from the trusted reverse-proxy chain.

    Traefik appends the connecting client's IP to the RIGHT of X-Forwarded-For,
    so the trustworthy value is the Nth-from-last entry, where N is the number
    of trusted proxy hops. Client-supplied (spoofed) values sit to the left and
    are ignored.
    """
    xff = request.headers.get("x-forwarded-for", "")
    if xff:
        parts = [p.strip() for p in xff.split(",") if p.strip()]
        if parts:
            idx = max(0, len(parts) - _XFF_HOPS)
            return parts[idx]
    xreal = request.headers.get("x-real-ip", "").strip()
    if xreal:
        return xreal
    return request.client.host if request.client else ""


def _is_loopback_host(hostname: str | None) -> bool:
    if not hostname:
        return False
    if hostname.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return False


def _normalize_origin(origin: str) -> str | None:
    try:
        parsed = urlsplit(origin)
    except ValueError:
        return None
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or not parsed.hostname:
        return None
    port = f":{parsed.port}" if parsed.port else ""
    return f"{parsed.scheme}://{parsed.hostname}{port}"


def _is_dev_request(request: Request) -> bool:
    host = request.headers.get("host", "").split(":", 1)[0].strip()
    return _is_loopback_host(host)


def _resolve_allowed_cors_origin(request: Request) -> str | None:
    origin = request.headers.get("origin", "").strip()
    if not origin:
        return None

    normalized_origin = _normalize_origin(origin)
    if not normalized_origin:
        return None

    if _CORS_ALLOWED_ORIGIN_SET:
        if normalized_origin in _CORS_ALLOWED_ORIGIN_SET:
            return normalized_origin
        return None

    parsed = urlsplit(normalized_origin)
    if _is_dev_request(request) and _is_loopback_host(parsed.hostname):
        return normalized_origin

    return None


def _append_vary(response: Response, value: str) -> None:
    current = response.headers.get("Vary")
    if not current:
        response.headers["Vary"] = value
        return

    values = {item.strip() for item in current.split(",") if item.strip()}
    if value not in values:
        values.add(value)
        response.headers["Vary"] = ", ".join(sorted(values))


def _apply_cors_headers(
    response: Response, origin: str, request_headers: str | None = None
) -> None:
    response.headers["Access-Control-Allow-Origin"] = origin
    response.headers["Access-Control-Allow-Credentials"] = "true"
    response.headers["Access-Control-Allow-Methods"] = _CORS_ALLOW_METHODS
    response.headers["Access-Control-Max-Age"] = _CORS_MAX_AGE_SECONDS
    if request_headers:
        response.headers["Access-Control-Allow-Headers"] = request_headers
        _append_vary(response, "Access-Control-Request-Headers")
    else:
        response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
    _append_vary(response, "Origin")


@app.middleware("http")
async def ip_allowlist(request: Request, call_next):
    if IP_FILTER_ENABLED and request.url.path not in _IP_FILTER_EXEMPT:
        ip = _client_ip(request)
        allowed = False
        try:
            addr = ipaddress.ip_address(ip)
            allowed = any(addr in net for net in _ALLOWED_NETS)
        except ValueError:
            allowed = False
        if not allowed:
            return PlainTextResponse("403 Forbidden", status_code=403)
    return await call_next(request)


@app.middleware("http")
async def cors_policy(request: Request, call_next):
    cors_origin = _resolve_allowed_cors_origin(request)
    is_preflight = (
        request.method == "OPTIONS"
        and request.headers.get("origin")
        and request.headers.get("access-control-request-method")
    )

    if is_preflight:
        if not cors_origin:
            return PlainTextResponse("CORS origin denied", status_code=400)
        response = Response(status_code=204)
        _apply_cors_headers(
            response,
            cors_origin,
            request.headers.get("access-control-request-headers"),
        )
        _append_vary(response, "Access-Control-Request-Method")
        return response

    response = await call_next(request)
    if cors_origin:
        _apply_cors_headers(response, cors_origin)
    return response


# Per-request counter of LLM vision (OCR) calls. A ContextVar keeps it isolated
# between concurrent requests so we can honestly report whether AI was used.
_ai_calls: ContextVar[int] = ContextVar("ai_calls", default=0)


class _CountingClient:
    """Thin proxy around an OpenAI-compatible client that counts vision calls.

    Only `chat.completions.create` is intercepted (that is what the OCR plugin
    invokes); every other attribute passes straight through.
    """

    def __init__(self, inner: Any) -> None:
        self._inner = inner

        class _Completions:
            def __init__(self, comp: Any) -> None:
                self._comp = comp

            def create(self, *args: Any, **kwargs: Any) -> Any:
                _ai_calls.set(_ai_calls.get() + 1)
                return self._comp.create(*args, **kwargs)

        class _Chat:
            def __init__(self, chat: Any) -> None:
                self.completions = _Completions(chat.completions)

        self.chat = _Chat(inner.chat)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


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

            client = _CountingClient(OpenAI(api_key=api_key, base_url=base_url))
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

    _ai_calls.set(0)  # reset the per-request vision-call counter
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

    ai_calls = _ai_calls.get()
    return JSONResponse(
        {
            "filename": filename,
            "title": result.title,
            "markdown": result.markdown,
            "characters": len(result.markdown),
            # How it was converted — reported honestly from actual LLM calls.
            "ocr_available": OCR_ENABLED,
            "used_ai": ai_calls > 0,
            "ai_calls": ai_calls,
        }
    )


# --- Static front-end ------------------------------------------------------


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


# Serve any other static assets (favicon, etc.) from /static.
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
