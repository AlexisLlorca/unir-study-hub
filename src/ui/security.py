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
import os
import time
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from fastapi import HTTPException, Request, Response
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
    if real.stat().st_size == 0:
        from src.core.cloud_sync import asegurar_archivo_local
        asegurar_archivo_local(real)
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
    import os
    hosts = {"localhost", "127.0.0.1", "::1", "[::1]", ip_local}
    try:
        nombre = socket.gethostname().lower()
        hosts.update({nombre, f"{nombre}.local", f"{nombre}.lan"})
    except Exception:
        pass
    extra = os.getenv("HUB_ALLOWED_HOSTS", "")
    for h in extra.split(","):
        if h.strip():
            hosts.add(h.strip().lower())
    return hosts


_SUFIJOS_NUBE_PERMITIDOS = (
    ".hf.space",
    ".huggingface.co",
    ".onrender.com",
    ".up.railway.app",
    ".fly.dev",
    ".trycloudflare.com",
    ".ts.net",
)

# --------------------------------------------------------------------------------------
# Anti-Fuerza Bruta & Cabeceras de Seguridad (OWASP A07 & A05)
# --------------------------------------------------------------------------------------

_INTENTOS_FALLIDOS: Dict[str, List[float]] = {}
_BLOQUEOS_HASTA: Dict[str, float] = {}
MAX_INTENTOS_FALLIDOS = 5
VENTANA_INTENTOS_SEG = 60
DURACION_BLOQUEO_SEG = 900  # 15 minutos


def _aplicar_cabeceras_seguridad(response: Response) -> Response:
    """Inyecta cabeceras defensivas para prevenir Clickjacking, MIME-sniffing e inyecciones."""
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    return response


def _obtener_ip_cliente(request: Request) -> str:
    """Extrae la IP real del cliente teniendo en cuenta proxies/Render (X-Forwarded-For)."""
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "desconocida"


def _esta_ip_bloqueada(ip: str) -> Tuple[bool, int]:
    ahora = time.time()
    bloqueado_hasta = _BLOQUEOS_HASTA.get(ip, 0)
    if ahora < bloqueado_hasta:
        return True, int(bloqueado_hasta - ahora)
    if ip in _BLOQUEOS_HASTA:
        del _BLOQUEOS_HASTA[ip]
    return False, 0


def _registrar_intento_fallido(ip: str) -> Tuple[bool, int]:
    ahora = time.time()
    intentos = _INTENTOS_FALLIDOS.get(ip, [])
    intentos = [t for t in intentos if ahora - t < VENTANA_INTENTOS_SEG]
    intentos.append(ahora)
    _INTENTOS_FALLIDOS[ip] = intentos

    if len(intentos) >= MAX_INTENTOS_FALLIDOS:
        _BLOQUEOS_HASTA[ip] = ahora + DURACION_BLOQUEO_SEG
        _INTENTOS_FALLIDOS.pop(ip, None)
        return True, DURACION_BLOQUEO_SEG
    return False, 0


def _limpiar_intentos_exitosos(ip: str) -> None:
    _INTENTOS_FALLIDOS.pop(ip, None)
    _BLOQUEOS_HASTA.pop(ip, None)


_PAGINA_TOKEN = """<!DOCTYPE html><html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>UNIR Study Hub · Acceso</title>
<style>body{background:#0b1120;color:#e5e7eb;font-family:system-ui,sans-serif;display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0}
.c{background:#111827;border:1px solid #1f2937;border-radius:20px;padding:28px;max-width:360px;width:90%}
h1{font-size:18px;margin:0 0 8px}p{font-size:13px;color:#9ca3af}input{width:100%;box-sizing:border-box;padding:12px;border-radius:12px;border:1px solid #374151;background:#030712;color:#fff;margin:12px 0}
button{width:100%;padding:12px;border:0;border-radius:12px;background:linear-gradient(90deg,#06b6d4,#4f46e5);color:#fff;font-weight:600}</style></head>
<body><form class="c" method="get" action="/"><h1>🔒 Acceso a UNIR Study Hub</h1>
<p>Introduce tu código de acceso privado para entrar a la plataforma en la nube o red local.</p>
<input name="token" type="password" placeholder="Código de acceso" autocomplete="off" required><button>Entrar</button></form></body></html>"""


class AccesoSeguroMiddleware(BaseHTTPMiddleware):
    """
    1. Rechaza cabeceras Host desconocidas (protección contra DNS rebinding).
    2. Rechaza peticiones que modifican datos (POST/PUT/DELETE...) hechas desde otra web (CSRF).
    3. Exige el token de acceso o token de invitado a clientes externos.
    4. Aplica limitación de tasa (anti-fuerza bruta) bloqueando IPs con intentos fallidos.
    5. Inyecta cabeceras HTTP de seguridad en todas las respuestas.
    """

    def __init__(self, app, token: str, ip_local: str):
        super().__init__(app)
        self.token = token
        self.hosts = _hosts_permitidos(ip_local)

    def _tokens_permitidos(self) -> List[str]:
        tokens = [self.token]
        invitados = os.getenv("HUB_GUEST_TOKENS", "").strip()
        if invitados:
            for t in invitados.split(","):
                if t.strip():
                    tokens.append(t.strip())
        return tokens

    def _token_valido(self, valor: Optional[str]) -> bool:
        if not valor:
            return False
        return any(hmac.compare_digest(valor, t) for t in self._tokens_permitidos())

    def _es_host_valido(self, host_sin_puerto: str) -> bool:
        if host_sin_puerto in self.hosts:
            return True
        return any(host_sin_puerto.endswith(suf) for suf in _SUFIJOS_NUBE_PERMITIDOS)

    async def dispatch(self, request: Request, call_next):
        host_hdr = (request.headers.get("host") or "").lower()
        host_sin_puerto = host_hdr.rsplit(":", 1)[0] if not host_hdr.startswith("[") else host_hdr.split("]")[0] + "]"
        if not self._es_host_valido(host_sin_puerto):
            return _aplicar_cabeceras_seguridad(JSONResponse({"detail": "Host no permitido"}, status_code=421))

        # CSRF: una petición que cambia datos debe venir de la propia web del Hub
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            origin = request.headers.get("origin")
            if origin:
                origin_host = origin.split("://", 1)[-1].lower()
                origin_sin_puerto = origin_host.rsplit(":", 1)[0]
                if origin_host != host_hdr and not self._es_host_valido(origin_sin_puerto):
                    return _aplicar_cabeceras_seguridad(JSONResponse({"detail": "Origen no permitido"}, status_code=403))

        cliente = request.client.host if request.client else None
        ip_cliente = _obtener_ip_cliente(request)
        es_nube_o_proxy = bool(request.headers.get("x-forwarded-for")) or any(
            host_sin_puerto.endswith(suf) for suf in _SUFIJOS_NUBE_PERMITIDOS
        )
        if request.url.path in ("/ping", "/health"):
            return _aplicar_cabeceras_seguridad(JSONResponse({"status": "ok", "app": "unir-study-hub"}, status_code=200))

        if _es_loopback(cliente) and not es_nube_o_proxy:
            resp = await call_next(request)
            return _aplicar_cabeceras_seguridad(resp)

        # 1. Comprobar bloqueo anti-fuerza bruta por IP
        bloqueada, restantes = _esta_ip_bloqueada(ip_cliente)
        if bloqueada:
            minutos = max(1, restantes // 60)
            return _aplicar_cabeceras_seguridad(
                JSONResponse(
                    {
                        "detail": f"Demasiados intentos fallidos. IP bloqueada temporalmente durante {minutos} min por protección anti-fuerza bruta."
                    },
                    status_code=429,
                )
            )

        # 2. Dispositivo de la red o acceso en la nube: token por parámetro (?token=...)
        token_query = request.query_params.get("token")
        if token_query:
            if self._token_valido(token_query):
                _limpiar_intentos_exitosos(ip_cliente)
                destino = request.url.remove_query_params("token")
                resp = RedirectResponse(url=str(destino.path) + (f"?{destino.query}" if destino.query else ""), status_code=303)
                es_https = request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"
                resp.set_cookie(
                    COOKIE_TOKEN,
                    token_query,
                    max_age=60 * 60 * 24 * 365,
                    httponly=True,
                    secure=es_https,
                    samesite="none" if es_https else "lax",
                )
                return _aplicar_cabeceras_seguridad(resp)
            else:
                bloqueada_ahora, duracion = _registrar_intento_fallido(ip_cliente)
                if bloqueada_ahora:
                    return _aplicar_cabeceras_seguridad(
                        JSONResponse(
                            {"detail": "Demasiados intentos fallidos. Acceso bloqueado temporalmente durante 15 minutos."},
                            status_code=429,
                        )
                    )
                return _aplicar_cabeceras_seguridad(
                    HTMLResponse(
                        _PAGINA_TOKEN.replace("</form>", "<p style='color:#f87171;margin-top:12px;font-size:12px'>❌ Código de acceso no válido.</p></form>"),
                        status_code=401,
                    )
                )

        # 3. Acceso con Cookie de sesión existente
        cookie_token = request.cookies.get(COOKIE_TOKEN)
        if self._token_valido(cookie_token):
            resp = await call_next(request)
            return _aplicar_cabeceras_seguridad(resp)

        if cookie_token:
            _registrar_intento_fallido(ip_cliente)

        if request.url.path.startswith("/api/"):
            return _aplicar_cabeceras_seguridad(
                JSONResponse({"detail": "Se necesita el código de acceso"}, status_code=401)
            )
        return _aplicar_cabeceras_seguridad(HTMLResponse(_PAGINA_TOKEN, status_code=401))
