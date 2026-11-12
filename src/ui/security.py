"""
Capa de seguridad de la web de NOVA Study Hub.

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

    if not real.exists() or (real.is_file() and real.stat().st_size == 0):
        from src.core.cloud_sync import asegurar_archivo_local
        asegurar_archivo_local(real)

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
MAX_INTENTOS_FALLIDOS = 10
VENTANA_INTENTOS_SEG = 60
DURACION_BLOQUEO_SEG = 180  # 3 minutos de bloqueo temporal


def _aplicar_cabeceras_seguridad(response: Response) -> Response:
    """Inyecta cabeceras defensivas para prevenir Clickjacking, MIME-sniffing e inyecciones."""
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
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


_PAGINA_TOKEN = """<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>NOVA Study Hub · Acceso y Registro</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.1/css/all.min.css">
  <style>
    body { background-color: #0b1120; font-family: system-ui, -apple-system, sans-serif; }
    .glass { background: rgba(17, 24, 39, 0.85); backdrop-filter: blur(16px); border: 1px solid rgba(255, 255, 255, 0.08); }
  </style>
</head>
<body class="min-h-screen flex items-center justify-center p-4 text-gray-200">
  <div class="glass w-full max-w-md rounded-3xl p-6 sm:p-8 shadow-2xl relative overflow-hidden">
    <!-- Luz ambiental de fondo -->
    <div class="absolute -top-24 -left-24 w-48 h-48 bg-cyan-500/20 rounded-full blur-3xl pointer-events-none"></div>
    <div class="absolute -bottom-24 -right-24 w-48 h-48 bg-indigo-500/20 rounded-full blur-3xl pointer-events-none"></div>

    <!-- Encabezado / Logo -->
    <div class="text-center mb-6 relative">
      <div class="inline-flex items-center justify-center w-14 h-14 rounded-2xl bg-gradient-to-tr from-cyan-500 to-indigo-600 text-white shadow-lg shadow-cyan-500/25 mb-3 text-2xl">
        <i class="fa-solid fa-graduation-cap"></i>
      </div>
      <h1 class="text-2xl font-black text-white tracking-tight">NOVA Study Hub</h1>
      <p class="text-xs text-gray-400 mt-1">Plataforma académica inteligente y tutor NOVA</p>
    </div>

    <!-- Pestaña de Acceso Privado -->
    <div class="flex border-b border-gray-800 mb-6 text-sm font-semibold">
      <button id="btnTabLogin" onclick="mostrarTab('login')" class="flex-1 pb-3 text-cyan-400 border-b-2 border-cyan-400 transition flex items-center justify-center gap-2">
        <i class="fa-solid fa-shield-halved text-xs"></i>
        <span>Acceso Privado Personal</span>
      </button>
    </div>

    <!-- Mensaje de estado dinámico -->
    <div id="mensajeAlerta" class="hidden mb-4 p-3.5 rounded-xl text-xs font-medium border flex items-start gap-2.5">
      <i id="alertaIcono" class="fa-solid text-sm mt-0.5 shrink-0"></i>
      <span id="alertaTexto" class="flex-1"></span>
    </div>

    <!-- 1. FORMULARIO DE INICIO DE SESIÓN -->
    <form id="formLogin" onsubmit="enviarLogin(event)" class="space-y-4">
      <div>
        <label class="block text-xs font-medium text-gray-300 mb-1.5">Usuario</label>
        <div class="relative">
          <i class="fa-solid fa-user absolute left-3.5 top-3.5 text-gray-500 text-xs"></i>
          <input id="loginUsuario" type="text" required placeholder="Tu nombre de usuario" autocomplete="username"
                 class="w-full bg-gray-900/80 border border-gray-800 rounded-xl pl-9 pr-3.5 py-2.5 text-sm text-white placeholder-gray-500 focus:outline-none focus:border-cyan-500 transition">
        </div>
      </div>

      <div>
        <label class="block text-xs font-medium text-gray-300 mb-1.5">Contraseña</label>
        <div class="relative">
          <i class="fa-solid fa-lock absolute left-3.5 top-3.5 text-gray-500 text-xs"></i>
          <input id="loginPassword" type="password" required placeholder="••••••••" autocomplete="current-password"
                 class="w-full bg-gray-900/80 border border-gray-800 rounded-xl pl-9 pr-3.5 py-2.5 text-sm text-white placeholder-gray-500 focus:outline-none focus:border-cyan-500 transition">
        </div>
      </div>

      <button id="btnLoginSubmit" type="submit"
              class="w-full py-3 rounded-xl bg-gradient-to-r from-cyan-500 to-indigo-600 hover:from-cyan-400 hover:to-indigo-500 text-white font-bold text-sm shadow-lg shadow-cyan-500/20 transition active:scale-[0.98]">
        Entrar a la Plataforma
      </button>

      <div class="pt-2 text-center">
        <button type="button" onclick="mostrarModalToken()" class="text-xs text-gray-400 hover:text-cyan-400 transition">
          ¿Tienes un código de acceso directo? <span class="underline">Pulsa aquí</span>
        </button>
      </div>
    </form>

    <!-- 2. FORMULARIO DE REGISTRO DE CUENTA -->
    <form id="formRegistro" onsubmit="enviarRegistro(event)" class="space-y-3.5 hidden">
      <div>
        <label class="block text-xs font-medium text-gray-300 mb-1">Nombre y Apellidos</label>
        <div class="relative">
          <i class="fa-solid fa-id-card absolute left-3.5 top-3.5 text-gray-500 text-xs"></i>
          <input id="regNombre" type="text" required placeholder="Ej: Carlos Gómez"
                 class="w-full bg-gray-900/80 border border-gray-800 rounded-xl pl-9 pr-3.5 py-2.5 text-sm text-white placeholder-gray-500 focus:outline-none focus:border-cyan-500 transition">
        </div>
      </div>

      <div>
        <label class="block text-xs font-medium text-gray-300 mb-1">Nombre de usuario (para login)</label>
        <div class="relative">
          <i class="fa-solid fa-at absolute left-3.5 top-3.5 text-gray-500 text-xs"></i>
          <input id="regUsuario" type="text" required placeholder="ej: carlosg" autocomplete="username"
                 class="w-full bg-gray-900/80 border border-gray-800 rounded-xl pl-9 pr-3.5 py-2.5 text-sm text-white placeholder-gray-500 focus:outline-none focus:border-cyan-500 transition">
        </div>
      </div>

      <div>
        <label class="block text-xs font-medium text-gray-300 mb-1">Contraseña</label>
        <div class="relative">
          <i class="fa-solid fa-lock absolute left-3.5 top-3.5 text-gray-500 text-xs"></i>
          <input id="regPassword" type="password" required minlength="6" placeholder="Mínimo 6 caracteres" autocomplete="new-password"
                 class="w-full bg-gray-900/80 border border-gray-800 rounded-xl pl-9 pr-3.5 py-2.5 text-sm text-white placeholder-gray-500 focus:outline-none focus:border-cyan-500 transition">
        </div>
      </div>

      <div>
        <div class="flex items-center justify-between mb-1">
          <label class="block text-xs font-medium text-gray-300">Clave API de Gemini (Google AI Studio) <span class="text-rose-400">*</span></label>
          <a href="https://aistudio.google.com/app/apikey" target="_blank" rel="noopener" class="text-[11px] text-cyan-400 hover:text-cyan-300 underline inline-flex items-center gap-1">
            <span>Obtener gratis</span>
            <i class="fa-solid fa-arrow-up-right-from-square text-[9px]"></i>
          </a>
        </div>
        <div class="relative">
          <i class="fa-solid fa-key absolute left-3.5 top-3.5 text-gray-500 text-xs"></i>
          <input id="regApiKey" type="password" required placeholder="AIzaSy..." autocomplete="off"
                 class="w-full bg-gray-900/80 border border-gray-800 rounded-xl pl-9 pr-3.5 py-2.5 text-sm text-white placeholder-gray-500 focus:outline-none focus:border-cyan-500 transition font-mono">
        </div>
        <p class="text-[10px] text-gray-400 mt-1">Obligatoria para estudiantes (BYOK). Tu cuota personal de Google es gratuita y te da generaciones ilimitadas.</p>
      </div>

      <div class="p-3 bg-amber-500/10 border border-amber-500/20 rounded-xl text-[11px] text-amber-300/90 leading-relaxed">
        <i class="fa-solid fa-shield-halved mr-1 text-amber-400"></i>
        <strong>Aprobación manual:</strong> Tu cuenta quedará registrada y el administrador la activará para permitirte el acceso.
      </div>

      <button id="btnRegSubmit" type="submit"
              class="w-full py-3 rounded-xl bg-gradient-to-r from-emerald-500 to-teal-600 hover:from-emerald-400 hover:to-teal-500 text-white font-bold text-sm shadow-lg shadow-emerald-500/20 transition active:scale-[0.98]">
        Solicitar Cuenta
      </button>
    </form>
  </div>

  <script>
    function mostrarTab(tab) {
      ocultarAlerta();
      const loginTab = document.getElementById('btnTabLogin');
      const regTab = document.getElementById('btnTabRegistro');
      const formLogin = document.getElementById('formLogin');
      const formReg = document.getElementById('formRegistro');

      if (tab === 'login') {
        loginTab.className = "flex-1 pb-3 text-cyan-400 border-b-2 border-cyan-400 transition flex items-center justify-center gap-2";
        regTab.className = "flex-1 pb-3 text-gray-400 hover:text-gray-200 border-b-2 border-transparent transition flex items-center justify-center gap-2";
        formLogin.classList.remove('hidden');
        formReg.classList.add('hidden');
      } else {
        regTab.className = "flex-1 pb-3 text-emerald-400 border-b-2 border-emerald-400 transition flex items-center justify-center gap-2";
        loginTab.className = "flex-1 pb-3 text-gray-400 hover:text-gray-200 border-b-2 border-transparent transition flex items-center justify-center gap-2";
        formReg.classList.remove('hidden');
        formLogin.classList.add('hidden');
      }
    }

    function mostrarAlerta(tipo, mensaje) {
      const box = document.getElementById('mensajeAlerta');
      const icono = document.getElementById('alertaIcono');
      const texto = document.getElementById('alertaTexto');

      box.classList.remove('hidden', 'bg-rose-500/10', 'border-rose-500/30', 'text-rose-300',
                         'bg-emerald-500/10', 'border-emerald-500/30', 'text-emerald-300',
                         'bg-amber-500/10', 'border-amber-500/30', 'text-amber-300');

      if (tipo === 'error') {
        box.classList.add('bg-rose-500/10', 'border-rose-500/30', 'text-rose-300');
        icono.className = 'fa-solid fa-triangle-exclamation text-rose-400 mt-0.5 shrink-0';
      } else if (tipo === 'ok') {
        box.classList.add('bg-emerald-500/10', 'border-emerald-500/30', 'text-emerald-300');
        icono.className = 'fa-solid fa-circle-check text-emerald-400 mt-0.5 shrink-0';
      } else {
        box.classList.add('bg-amber-500/10', 'border-amber-500/30', 'text-amber-300');
        icono.className = 'fa-solid fa-clock text-amber-400 mt-0.5 shrink-0';
      }
      texto.innerText = mensaje;
    }

    function ocultarAlerta() {
      document.getElementById('mensajeAlerta').classList.add('hidden');
    }

    async function enviarLogin(e) {
      e.preventDefault();
      ocultarAlerta();
      const btn = document.getElementById('btnLoginSubmit');
      btn.disabled = true;
      btn.innerText = 'Verificando...';

      const username = document.getElementById('loginUsuario').value.trim();
      const password = document.getElementById('loginPassword').value.trim();

      try {
        const res = await fetch('/api/auth/login', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ username, password })
        });
        const data = await res.json();
        if (res.ok && data.status === 'ok') {
          if (data.usuario && data.usuario.gemini_api_key) {
            try { localStorage.setItem('unir_user_gemini_key', data.usuario.gemini_api_key); } catch(e) {}
          }
          mostrarAlerta('ok', 'Acceso correcto. Entrando al Hub...');
          setTimeout(() => { window.location.href = '/'; }, 600);
        } else {
          const msg = data.detail || 'Usuario o contraseña no válidos';
          const tipo = msg.includes('pendiente') ? 'info' : 'error';
          mostrarAlerta(tipo, msg);
        }
      } catch (err) {
        mostrarAlerta('error', 'Error al conectar con el servidor.');
      } finally {
        btn.disabled = false;
        btn.innerText = 'Entrar a la Plataforma';
      }
    }

    async function enviarRegistro(e) {
      e.preventDefault();
      ocultarAlerta();
      const btn = document.getElementById('btnRegSubmit');
      btn.disabled = true;

      const nombre = document.getElementById('regNombre').value.trim();
      const username = document.getElementById('regUsuario').value.trim();
      const password = document.getElementById('regPassword').value.trim();
      const apiKey = (document.getElementById('regApiKey').value || '').trim();

      if (!apiKey) {
        mostrarAlerta('error', 'Es obligatorio proporcionar tu clave gratuita de Google AI Studio (Gemini).');
        btn.disabled = false;
        return;
      }

      btn.innerText = 'Validando clave Gemini...';
      try {
        const valRes = await fetch('/api/validar-gemini-key', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ api_key: apiKey })
        });
        const valData = await valRes.json();
        if (!valData.valida) {
          mostrarAlerta('error', 'La clave de Gemini no es válida: ' + (valData.error || 'Verifica la clave copiada en Google AI Studio.'));
          btn.disabled = false;
          btn.innerText = 'Solicitar Cuenta';
          return;
        }
      } catch (err) {
        // Si hay error puntual de validación de red, continuar
      }

      btn.innerText = 'Registrando...';
      try {
        const res = await fetch('/api/auth/registro', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ nombre, username, password, api_key: apiKey })
        });
        const data = await res.json();
        if (res.ok && data.status === 'ok') {
          try { localStorage.setItem('unir_user_gemini_key', apiKey); } catch(e) {}
          mostrarAlerta('info', '¡Cuenta creada con éxito! El administrador revisará y activará tu cuenta.');
          document.getElementById('formRegistro').reset();
          setTimeout(() => { mostrarTab('login'); }, 4000);
        } else {
          mostrarAlerta('error', data.detail || 'No se pudo completar el registro.');
        }
      } catch (err) {
        mostrarAlerta('error', 'Error al procesar el registro.');
      } finally {
        btn.disabled = false;
        btn.innerText = 'Solicitar Cuenta';
      }
    }

    function mostrarModalToken() {
      const code = prompt('Introduce tu código o token de acceso directo:');
      if (code) {
        window.location.href = '/?token=' + encodeURIComponent(code.trim());
      }
    }
  </script>
</body>
</html>"""


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
        rutas_publicas_pwa = (
            "/ping",
            "/health",
            "/manifest.json",
            "/service-worker.js",
            "/pwa-icon.svg",
            "/pwa-icon-192.png",
            "/pwa-icon-512.png",
            "/apple-touch-icon.png",
            "/favicon.ico",
        )
        if (
            request.url.path in rutas_publicas_pwa
            or request.url.path.startswith("/static/")
            or request.url.path in ("/logout", "/api/validar-gemini-key")
            or request.url.path.startswith("/api/auth/")
        ):
            resp = await call_next(request)
            return _aplicar_cabeceras_seguridad(resp)

        # 1. Comprobar sesión de usuario registrada (SQLite)
        cookie_token = request.cookies.get(COOKIE_TOKEN)
        from src.core.auth import obtener_usuario_por_sesion
        usuario_sesion = obtener_usuario_por_sesion(cookie_token)
        if usuario_sesion:
            request.state.usuario = usuario_sesion
            resp = await call_next(request)
            return _aplicar_cabeceras_seguridad(resp)

        # 2. Comprobar token maestro directo en cookie
        if self._token_valido(cookie_token):
            request.state.usuario = {
                "id": 1,
                "username": "alexking44",
                "nombre": "Alex (Administrador)",
                "rol": "admin",
                "activo": 1
            }
            resp = await call_next(request)
            return _aplicar_cabeceras_seguridad(resp)

        if _es_loopback(cliente) and not es_nube_o_proxy:
            request.state.usuario = {
                "id": 1,
                "username": "alexking44",
                "nombre": "Alex (Administrador)",
                "rol": "admin",
                "activo": 1
            }
            resp = await call_next(request)
            return _aplicar_cabeceras_seguridad(resp)

        # 3. Comprobar bloqueo anti-fuerza bruta por IP
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

        # 4. Dispositivo de la red o acceso en la nube: token por parámetro (?token=...)
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
                            {"detail": "Demasiados intentos fallidos. Acceso bloqueado temporalmente durante 5 minutos."},
                            status_code=429,
                        )
                    )
                return _aplicar_cabeceras_seguridad(
                    HTMLResponse(
                        _PAGINA_TOKEN,
                        status_code=401,
                    )
                )

        # Si la cookie enviada ya no es válida (sesión caducada tras reinicio del servidor o logout),
        # limpiamos la cookie de la respuesta para evitar bucles y no penalizamos como ataque de fuerza bruta.
        if request.url.path.startswith("/api/"):
            resp = JSONResponse({"detail": "Se necesita iniciar sesión en la plataforma"}, status_code=401)
        else:
            resp = HTMLResponse(_PAGINA_TOKEN, status_code=401)

        if cookie_token:
            resp.delete_cookie(COOKIE_TOKEN)

        return _aplicar_cabeceras_seguridad(resp)
