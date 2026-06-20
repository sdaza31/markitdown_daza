# MarkItDown Web

Front-end web (modo oscuro) sobre la librería [MarkItDown](../README.md). Sube un
archivo y obtén Markdown listo para LLMs: PDF, Word, Excel, PowerPoint, imágenes
(OCR), audio, HTML, CSV/JSON/XML, EPUB, ZIP y más.

- **Backend:** FastAPI (`webapp/server.py`)
- **Frontend:** una sola página estática (`webapp/static/index.html`)
- **Endpoints:** `GET /` (UI) · `POST /api/convert` · `GET /api/health` · `GET /api/docs`

## Ejecutar en local

### Con Docker (recomendado)

```bash
docker compose up --build
# -> http://localhost:8000
```

### Sin Docker (Python)

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install ./packages/markitdown[all] -r webapp/requirements.txt
uvicorn webapp.server:app --reload --port 8000
```

## Desplegar en Dokploy

Hay dos formas; ambas usan los archivos ya incluidos en el repo.

### Opción A — Compose (recomendada)

1. En Dokploy: **Create Service → Compose**.
2. Conecta este repositorio de GitHub (`sdaza31/markitdown_daza`), rama `main`.
3. **Compose Path:** `docker-compose.yml` (raíz del repo).
4. **Deploy.** El servicio expone el puerto **8000**.
5. En **Domains**, añade tu dominio y apúntalo al puerto **8000**. Dokploy
   (Traefik) gestiona el HTTPS automáticamente.

### Opción B — Application (Dockerfile)

1. **Create Service → Application**, conecta el repo y la rama `main`.
2. **Build Type:** Dockerfile · **Dockerfile Path:** `Dockerfile.web`.
3. **Deploy** y añade tu dominio al puerto **8000** en **Domains**.

## Variables de entorno

| Variable | Por defecto | Descripción |
|----------|-------------|-------------|
| `PORT` | `8000` | Puerto en el que escucha la app. |
| `MARKITDOWN_MAX_UPLOAD_MB` | `50` | Tamaño máximo de subida en MB. |
| `MARKITDOWN_PLUGINS` | `0` | `1` para habilitar plugins de terceros de MarkItDown. |

## Notas

- El contenedor incluye `ffmpeg` y `exiftool` para transcripción de audio y
  metadatos/OCR de imágenes.
- La conversión ocurre **en tu servidor**; los archivos no se envían a terceros.
- Healthcheck disponible en `/api/health` (lo usan Docker y Dokploy).
