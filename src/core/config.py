"""
Configuración centralizada y gestión de rutas del proyecto unir-study-hub.
Define las rutas base de forma relativa y dinámica para máxima portabilidad.
"""

from pathlib import Path
from typing import Optional
import os
from dotenv import load_dotenv
from rich.console import Console

# 1. BASE_DIR: Calcula dinámicamente la raíz del proyecto (3 niveles arriba de este archivo)
# __file__ = src/core/config.py -> parent = src/core -> parent = src -> parent = unir-study-hub
BASE_DIR = Path(__file__).resolve().parent.parent.parent
ENV_FILE = BASE_DIR / ".env"

# 2. Carga variables de entorno si existe un archivo .env local
load_dotenv(dotenv_path=ENV_FILE)

# 3. Inicializa consola de Rich protegida contra BrokenPipeError cuando corre como daemon web
class SafeConsole(Console):
    """Consola Rich que ignora BrokenPipeError / OSError si stdout/stderr se cierra en segundo plano."""

    def print(self, *args, **kwargs) -> None:
        try:
            super().print(*args, **kwargs)
        except (BrokenPipeError, OSError):
            pass

    def log(self, *args, **kwargs) -> None:
        try:
            super().log(*args, **kwargs)
        except (BrokenPipeError, OSError):
            pass

    def _write_buffer(self) -> None:
        try:
            super()._write_buffer()
        except (BrokenPipeError, OSError):
            try:
                self._buffer.clear()
            except Exception:
                pass


console = SafeConsole()

# Crear carpetas es idempotente, pero no gratis: recordamos lo ya comprobado
# para no repetir decenas de mkdir en cada petición web.
_DIRECTORIOS_ASEGURADOS = False
_RUTAS_BASE_ASEGURADAS = False
_ESPACIOS_USUARIO_ASEGURADOS: set = set()

# 4. Rutas del sistema de datos (Entrada, Almacén y Procesado)
DATA_DIR = BASE_DIR / "data"

# Bandejas de entrada manual o externa
BANDEJA_ENTRADA_DIR = DATA_DIR / "bandeja_entrada"
BANDEJA_DAM_DIR = BANDEJA_ENTRADA_DIR / "dam"
BANDEJA_PFU_DIR = BANDEJA_ENTRADA_DIR / "pfu"

# Descargas automáticas del campus
MATERIALES_DIR = DATA_DIR / "materiales"
CLASES_GRABADAS_DIR = DATA_DIR / "clases_grabadas"

# Contenido generado y procesado
PROCESADO_DIR = DATA_DIR / "procesado"
APUNTES_DIR = PROCESADO_DIR / "apuntes"
GUIAS_DIR = PROCESADO_DIR / "guias"
EJERCICIOS_DIR = PROCESADO_DIR / "ejercicios"
EXAMENES_DIR = PROCESADO_DIR / "examenes"
CODIGO_DIR = PROCESADO_DIR / "codigo"
TRANSCRIPCIONES_DIR = PROCESADO_DIR / "transcripciones"
RESUMENES_CLASES_DIR = PROCESADO_DIR / "resumenes_clases"
RESUMENES_CLASES_DIR.mkdir(parents=True, exist_ok=True)

# Espacios privados por cuenta de usuario (guías, tests, resúmenes, progreso y flashcards aislados)
USUARIOS_ESPACIOS_DIR = DATA_DIR / "usuarios_espacios"
USUARIOS_ESPACIOS_DIR.mkdir(parents=True, exist_ok=True)


def obtener_espacio_usuario(username: Optional[str] = None) -> Path:
    """Devuelve el directorio privado de estudio del usuario (`data/usuarios_espacios/<username>`)."""
    import re
    u = re.sub(r"[^a-zA-Z0-9_\-]", "", (username or "alexking44").strip().lower())
    if not u or u == "local":
        u = "alexking44"
    espacio = USUARIOS_ESPACIOS_DIR / u
    if u not in _ESPACIOS_USUARIO_ASEGURADOS:
        espacio.mkdir(parents=True, exist_ok=True)
        _ESPACIOS_USUARIO_ASEGURADOS.add(u)
    return espacio


def obtener_rutas_usuario(username: Optional[str] = None) -> dict:
    """Devuelve las rutas unificadas de materiales, guías, exámenes, resúmenes, progreso y flashcards."""
    global _RUTAS_BASE_ASEGURADAS
    espacio = obtener_espacio_usuario(username)
    flashcards_dir = PROCESADO_DIR / "flashcards"
    if not _RUTAS_BASE_ASEGURADAS:
        for d in (MATERIALES_DIR, GUIAS_DIR, EXAMENES_DIR, RESUMENES_CLASES_DIR, flashcards_dir):
            d.mkdir(parents=True, exist_ok=True)
        _RUTAS_BASE_ASEGURADAS = True
    return {
        "espacio": espacio,
        "materiales": MATERIALES_DIR,
        "guias": GUIAS_DIR,
        "examenes": EXAMENES_DIR,
        "resumenes_clases": RESUMENES_CLASES_DIR,
        "progreso_file": PROCESADO_DIR / "progreso.json",
        "flashcards_file": flashcards_dir / "mazos.json",
    }

# 5. CIBERSEGURIDAD: Rutas de autenticación y sesiones
AUTH_DIR = BASE_DIR / "auth"
SESSION_STATE_FILE = AUTH_DIR / "session_state.json"

# 6. Carpeta externa de respaldo de estudios (configurable con UNIR_RESPALDO_DIR en .env)
RESPALDO_DIR = Path(os.getenv("UNIR_RESPALDO_DIR", str(Path.home() / "Respaldo_Estudios"))).expanduser()

# 7. Variables del Campus y Servicios de IA
UNIR_CAMPUS_URL = os.getenv("UNIR_CAMPUS_URL", "https://campusfp.unir.net/")
DEFAULT_GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.5-flash")


def get_gemini_api_key() -> str:
    """Devuelve la clave de API de Gemini configurada o cadena vacía si no existe."""
    load_dotenv(dotenv_path=ENV_FILE, override=True)
    return os.getenv("GEMINI_API_KEY", "").strip()


def _guardar_variable_env(nombre: str, valor: str):
    """Guarda o actualiza una variable NOMBRE=valor en el archivo .env local."""
    os.environ[nombre] = valor
    lineas = []
    encontrada = False

    if ENV_FILE.exists():
        with open(ENV_FILE, "r", encoding="utf-8") as f:
            for line in f:
                if line.startswith(f"{nombre}="):
                    lineas.append(f"{nombre}={valor}\n")
                    encontrada = True
                else:
                    lineas.append(line)

    if not encontrada:
        if lineas and not lineas[-1].endswith("\n"):
            lineas[-1] += "\n"
        lineas.append(f"{nombre}={valor}\n")

    with open(ENV_FILE, "w", encoding="utf-8") as f:
        f.writelines(lineas)
    try:
        os.chmod(ENV_FILE, 0o600)  # Solo legible por el usuario propietario
    except OSError:
        pass


def save_gemini_api_key(api_key: str):
    """Guarda o actualiza la clave GEMINI_API_KEY en el archivo .env local."""
    _guardar_variable_env("GEMINI_API_KEY", api_key.strip())


def get_openrouter_api_key() -> str:
    """Obtiene la clave gratuita de OpenRouter desde las variables de entorno."""
    load_dotenv(dotenv_path=ENV_FILE, override=True)
    return os.getenv("OPENROUTER_API_KEY", "").strip()


def save_openrouter_api_key(api_key: str):
    """Guarda o actualiza OPENROUTER_API_KEY en .env."""
    _guardar_variable_env("OPENROUTER_API_KEY", api_key.strip())


def get_groq_api_key() -> str:
    """Obtiene la clave gratuita de Groq (prefijo gsk_) desde las variables de entorno."""
    load_dotenv(dotenv_path=ENV_FILE, override=True)
    key = os.getenv("GROQ_API_KEY", "").strip()
    if key.startswith("sk-or-"):
        return ""
    return key


def save_groq_api_key(api_key: str):
    """Guarda o actualiza GROQ_API_KEY en .env."""
    k = api_key.strip()
    if k.startswith("sk-or-"):
        _guardar_variable_env("OPENROUTER_API_KEY", k)
    else:
        _guardar_variable_env("GROQ_API_KEY", k)


def get_hub_access_token() -> str:
    """
    Devuelve el token de acceso a la web para dispositivos de la red (móvil, tablet...).
    Si no existe, genera uno aleatorio criptográficamente seguro y lo guarda en .env.
    """
    token = os.getenv("HUB_ACCESS_TOKEN", "").strip()
    if not token:
        import secrets
        token = secrets.token_urlsafe(24)
        _guardar_variable_env("HUB_ACCESS_TOKEN", token)
    return token


def escribir_json_atomico(ruta: Path, datos) -> None:
    """
    Escribe un JSON de forma atómica: primero en un archivo temporal y luego lo renombra.
    Así, si el proceso se interrumpe o hay dos escrituras a la vez, nunca queda un archivo a medias.
    """
    import json
    import tempfile

    ruta = Path(ruta)
    ruta.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=ruta.parent, prefix=f".{ruta.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(datos, f, indent=2, ensure_ascii=False)
        os.replace(tmp, ruta)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def ensure_directories():
    """
    Crea automáticamente todas las carpetas necesarias si no existen todavía.
    Garantiza que el proyecto funcione al clonarlo en cualquier equipo.
    Solo hace el trabajo la primera vez; después es una salida inmediata.
    """
    global _DIRECTORIOS_ASEGURADOS
    if _DIRECTORIOS_ASEGURADOS:
        return
    directorios = [
        DATA_DIR,
        BANDEJA_ENTRADA_DIR,
        BANDEJA_DAM_DIR,
        BANDEJA_PFU_DIR,
        MATERIALES_DIR,
        CLASES_GRABADAS_DIR,
        PROCESADO_DIR,
        APUNTES_DIR,
        GUIAS_DIR,
        EJERCICIOS_DIR,
        EXAMENES_DIR,
        CODIGO_DIR,
        TRANSCRIPCIONES_DIR,
        AUTH_DIR,
    ]
    for d in directorios:
        d.mkdir(parents=True, exist_ok=True)
    _DIRECTORIOS_ASEGURADOS = True


if __name__ == "__main__":
    console.print("\n[bold cyan]🔧 Comprobando Configuración de unir-study-hub...[/bold cyan]")
    ensure_directories()
    console.print(f"[green]✔ Raíz del proyecto (BASE_DIR):[/green] {BASE_DIR}")
    console.print(f"[green]✔ Clases Grabadas:[/green] {CLASES_GRABADAS_DIR}")
    console.print(f"[green]✔ Materiales Campus:[/green] {MATERIALES_DIR}")
    console.print(f"[green]✔ Transcripciones:[/green] {TRANSCRIPCIONES_DIR}")
    console.print(f"[green]✔ Estado Gemini API Key:[/green] {'[green]Configurada[/green]' if get_gemini_api_key() else '[yellow]Pendiente[/yellow]'}")
    console.print("[bold green]✨ ¡Todas las rutas verificadas y carpetas aseguradas![/bold green]\n")
