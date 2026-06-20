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

### Opción A — Application (recomendada)

Dokploy gestiona Traefik y la red automáticamente, así que los redeploys no
rompen el enrutado (con Compose, recrear el contenedor puede dejar a Traefik
apuntando al contenedor viejo → 502 Bad Gateway).

1. **Create Service → Application**, conecta el repo (`sdaza31/markitdown_daza`)
   y la rama `main`.
2. **Build Type:** `Dockerfile` · **Dockerfile Path:** `Dockerfile.web`.
3. **Deploy.**
4. En **Domains**, añade tu dominio con **Container Port `8000`** y HTTPS
   (Let's Encrypt) activado.

### Opción B — Compose

1. En Dokploy: **Create Service → Compose**.
2. Conecta el repo, rama `main`. **Compose Path:** `docker-compose.yml`.
3. **Deploy.** El servicio se une a `dokploy-network` y expone el puerto **8000**.
4. En **Domains**, añade tu dominio apuntando al puerto **8000**.

> Nota: si tras un redeploy ves *502 Bad Gateway* pero los logs muestran
> `Uvicorn running on 0.0.0.0:8000`, la app está bien — es Traefik que perdió la
> ruta al recrearse el contenedor. Usa la Opción A o añade labels de Traefik
> explícitas a este compose.

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
