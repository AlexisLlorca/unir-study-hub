"""
Configuración centralizada y gestión de rutas del proyecto unir-study-hub.
Define las rutas base de forma relativa y dinámica para máxima portabilidad.
"""

from pathlib import Path
import os
from dotenv import load_dotenv
from rich.console import Console

# 1. BASE_DIR: Calcula dinámicamente la raíz del proyecto (3 niveles arriba de este archivo)
# __file__ = src/core/config.py -> parent = src/core -> parent = src -> parent = unir-study-hub
BASE_DIR = Path(__file__).resolve().parent.parent.parent
ENV_FILE = BASE_DIR / ".env"

# 2. Carga variables de entorno si existe un archivo .env local
load_dotenv(dotenv_path=ENV_FILE)

# 3. Inicializa consola de Rich para salidas elegantes en terminal
console = Console()

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
EJERCICIOS_DIR = PROCESADO_DIR / "ejercicios"
EXAMENES_DIR = PROCESADO_DIR / "examenes"
CODIGO_DIR = PROCESADO_DIR / "codigo"
TRANSCRIPCIONES_DIR = PROCESADO_DIR / "transcripciones"

# 5. CIBERSEGURIDAD: Rutas de autenticación y sesiones
AUTH_DIR = BASE_DIR / "auth"
SESSION_STATE_FILE = AUTH_DIR / "session_state.json"

# 6. Variables del Campus y Servicios de IA
UNIR_CAMPUS_URL = os.getenv("UNIR_CAMPUS_URL", "https://campusvirtual.unir.net/")
DEFAULT_GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")


def get_gemini_api_key() -> str:
    """Devuelve la clave de API de Gemini configurada o cadena vacía si no existe."""
    load_dotenv(dotenv_path=ENV_FILE, override=True)
    return os.getenv("GEMINI_API_KEY", "").strip()


def save_gemini_api_key(api_key: str):
    """Guarda o actualiza la clave GEMINI_API_KEY en el archivo .env local."""
    api_key = api_key.strip()
    os.environ["GEMINI_API_KEY"] = api_key

    lineas = []
    clave_encontrada = False

    if ENV_FILE.exists():
        with open(ENV_FILE, "r", encoding="utf-8") as f:
            for line in f:
                if line.startswith("GEMINI_API_KEY="):
                    lineas.append(f"GEMINI_API_KEY={api_key}\n")
                    clave_encontrada = True
                else:
                    lineas.append(line)

    if not clave_encontrada:
        lineas.append(f"GEMINI_API_KEY={api_key}\n")

    with open(ENV_FILE, "w", encoding="utf-8") as f:
        f.writelines(lineas)


def ensure_directories():
    """
    Crea automáticamente todas las carpetas necesarias si no existen todavía.
    Garantiza que el proyecto funcione al clonarlo en cualquier equipo.
    """
    directorios = [
        DATA_DIR,
        BANDEJA_ENTRADA_DIR,
        BANDEJA_DAM_DIR,
        BANDEJA_PFU_DIR,
        MATERIALES_DIR,
        CLASES_GRABADAS_DIR,
        PROCESADO_DIR,
        APUNTES_DIR,
        EJERCICIOS_DIR,
        EXAMENES_DIR,
        CODIGO_DIR,
        TRANSCRIPCIONES_DIR,
        AUTH_DIR,
    ]
    for d in directorios:
        d.mkdir(parents=True, exist_ok=True)


if __name__ == "__main__":
    console.print("\n[bold cyan]🔧 Comprobando Configuración de unir-study-hub...[/bold cyan]")
    ensure_directories()
    console.print(f"[green]✔ Raíz del proyecto (BASE_DIR):[/green] {BASE_DIR}")
    console.print(f"[green]✔ Clases Grabadas:[/green] {CLASES_GRABADAS_DIR}")
    console.print(f"[green]✔ Materiales Campus:[/green] {MATERIALES_DIR}")
    console.print(f"[green]✔ Transcripciones:[/green] {TRANSCRIPCIONES_DIR}")
    console.print(f"[green]✔ Estado Gemini API Key:[/green] {'[green]Configurada[/green]' if get_gemini_api_key() else '[yellow]Pendiente[/yellow]'}")
    console.print("[bold green]✨ ¡Todas las rutas verificadas y carpetas aseguradas![/bold green]\n")
