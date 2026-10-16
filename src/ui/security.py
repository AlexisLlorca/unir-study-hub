"""
Capa de seguridad de la web de UNIR Study Hub.

- Validación de rutas (evita path traversal y la lectura de .env, cookies de Canvas, .git...).
- Validación de nombres de asignatura usados como carpeta.
- Middleware de acceso: el propio ordenador entra sin token; cualquier otro dispositivo
  de la red (móvil, tablet...) necesita el token de acceso una única vez (queda en una cookie).
- Protección CSRF / DNS-rebinding: rechaza peticiones que cambian datos desde otras webs
  y peticiones con una cabecera Host desconocida.
"""

import hmac
import ipaddress
import socket
from pathlib import Path
from typing import Iterable, Optional

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from starlette.middleware.base import BaseHTTPMiddleware

from src.core.config import BASE_DIR, DATA_DIR, RESPALDO_DIR

COOKIE_TOKEN = "hub_token"

# Componentes de ruta que nunca se sirven, aunque estén dentro de una carpeta permitida
_PARTES_PROHIBIDAS = {".env", ".git", "auth", ".venv", "secrets", "__pycache__"}


def raices_lectura() -> list:
    """Carpetas desde las que se permite leer archivos (los materiales pueden ser enlaces al respaldo)."""
    raices = [DATA_DIR.resolve()]
    if RESPALDO_DIR.exists():
        raices.append(RESPALDO_DIR.resolve())
    return raices


def _contiene_parte_prohibida(p: Path) -> bool:
    return any(parte in _PARTES_PROHIBIDAS or parte.startswith(".env") for parte in p.parts)


def ruta_segura_lectura(ruta_relativa: str, extensiones: Optional[Iterable[str]] = None) -> Path:
    """
    Convierte una ruta relativa recibida del navegador (p. ej. 'data/procesado/guias/X/y.md')
    en una ruta absoluta validada. Lanza 403/404 si no es segura o no existe.
    """
    if not ruta_relativa or "\x00" in ruta_relativa:
        raise HTTPException(status_code=400, detail="Ruta no válida")

    rel = Path(ruta_relativa)
    if rel.is_absolute() or ".." in rel.parts or _contiene_parte_prohibida(rel):
        raise HTTPException(status_code=403, detail="Acceso denegado")

    candidata = (BASE_DIR / rel)
    # La ruta (sin seguir enlaces) debe estar dentro de data/
    if not candidata.absolute().is_relative_to(DATA_DIR.absolute()):
        raise HTTPException(status_code=403, detail="Acceso denegado")

    # Y su destino real (siguiendo enlaces simbólicos) dentro de data/ o del respaldo de estudios
    real = candidata.resolve()
    if not any(real.is_relative_to(r) for r in raices_lectura()) or _contiene_parte_prohibida(real):
        raise HTTPException(status_code=403, detail="Acceso denegado")

    if extensiones is not None and real.suffix.lower() not in {e.lower() for e in extensiones}:
        raise HTTPException(status_code=403, detail="Tipo de archivo no permitido")

    if not real.exists() or not real.is_file():
        raise HTTPException(status_code=404, detail="Archivo no encontrado")
    return real


def nombre_seguro(nombre: str, campo: str = "nombre") -> str:
    """
    Valida un nombre que se usará como carpeta o archivo (asignatura, test, guía...).
    No admite separadores de ruta, '..' ni nombres ocultos.
    """
    nombre = (nombre or "").strip()
    if (
        not nombre
        or len(nombre) > 200
        or "/" in nombre
        or "\\" in nombre
        or "\x00" in nombre
        or nombre.startswith(".")
        or ".." in nombre
    ):
        raise HTTPException(status_code=400, detail=f"{campo} no válido")
    return nombre


def ruta_dentro_de(base: Path, *partes: str) -> Path:
    """Construye base/partes y garantiza que el resultado sigue dentro de base."""
    base_abs = base.resolve()
    destino = base_abs.joinpath(*partes).resolve()
    if not destino.is_relative_to(base_abs):
        raise HTTPException(status_code=403, detail="Acceso denegado")
    return destino


# --------------------------------------------------------------------------------------
# Middleware de acceso
# --------------------------------------------------------------------------------------

def _es_loopback(host: Optional[str]) -> bool:
    if not host:
        return False
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host == "localhost"


def obtener_ip_red_local() -> str:
    """Obtiene la dirección IP del equipo en la red local (Wi-Fi o LAN)."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def _hosts_permitidos(ip_local: str) -> set:
    hosts = {"localhost", "127.0.0.1", "::1", "[::1]", ip_local}
    try:
        nombre = socket.gethostname().lower()
        hosts.update({nombre, f"{nombre}.local", f"{nombre}.lan"})
    except Exception:
        pass
    return hosts


_PAGINA_TOKEN = """<!DOCTYPE html><html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>UNIR Study Hub · Acceso</title>
<style>body{background:#0b1120;color:#e5e7eb;font-family:system-ui,sans-serif;display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0}
.c{background:#111827;border:1px solid #1f2937;border-radius:20px;padding:28px;max-width:360px;width:90%}
h1{font-size:18px;margin:0 0 8px}p{font-size:13px;color:#9ca3af}input{width:100%;box-sizing:border-box;padding:12px;border-radius:12px;border:1px solid #374151;background:#030712;color:#fff;margin:12px 0}
button{width:100%;padding:12px;border:0;border-radius:12px;background:linear-gradient(90deg,#06b6d4,#4f46e5);color:#fff;font-weight:600}</style></head>
<body><form class="c" method="get" action="/"><h1>🔒 Acceso a UNIR Study Hub</h1>
<p>Este dispositivo necesita el código de acceso. Escanea el QR que aparece en el ordenador (botón «📱 Móvil») o pega aquí el código.</p>
<input name="token" placeholder="Código de acceso" autocomplete="off" required><button>Entrar</button></form></body></html>"""


class AccesoSeguroMiddleware(BaseHTTPMiddleware):
    """
    1. Rechaza cabeceras Host desconocidas (protección contra DNS rebinding).
    2. Rechaza peticiones que modifican datos (POST/PUT/DELETE...) hechas desde otra web (CSRF).
    3. Exige el token de acceso a cualquier cliente que no sea el propio ordenador.
    """

    def __init__(self, app, token: str, ip_local: str):
        super().__init__(app)
        self.token = token
        self.hosts = _hosts_permitidos(ip_local)

    def _token_valido(self, valor: Optional[str]) -> bool:
        return bool(valor) and hmac.compare_digest(valor, self.token)

    async def dispatch(self, request: Request, call_next):
        host_hdr = (request.headers.get("host") or "").lower()
        host_sin_puerto = host_hdr.rsplit(":", 1)[0] if not host_hdr.startswith("[") else host_hdr.split("]")[0] + "]"
        if host_sin_puerto not in self.hosts:
            return JSONResponse({"detail": "Host no permitido"}, status_code=421)

        # CSRF: una petición que cambia datos debe venir de la propia web del Hub
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            origin = request.headers.get("origin")
            sec_site = request.headers.get("sec-fetch-site")
            if origin and origin.split("://", 1)[-1].lower() != host_hdr:
                return JSONResponse({"detail": "Origen no permitido"}, status_code=403)
            if sec_site and sec_site not in ("same-origin", "none"):
                return JSONResponse({"detail": "Origen no permitido"}, status_code=403)

        cliente = request.client.host if request.client else None
        if _es_loopback(cliente):
            return await call_next(request)

        # Dispositivo de la red: token por parámetro (primera vez) o por cookie
        token_query = request.query_params.get("token")
        if self._token_valido(token_query):
            destino = request.url.remove_query_params("token")
            resp = RedirectResponse(url=str(destino.path) + (f"?{destino.query}" if destino.query else ""), status_code=303)
            resp.set_cookie(COOKIE_TOKEN, self.token, max_age=60 * 60 * 24 * 365, httponly=True, samesite="strict")
            return resp

        if self._token_valido(request.cookies.get(COOKIE_TOKEN)):
            return await call_next(request)

        if request.url.path.startswith("/api/"):
            return JSONResponse({"detail": "Se necesita el código de acceso"}, status_code=401)
        return HTMLResponse(_PAGINA_TOKEN, status_code=401)
