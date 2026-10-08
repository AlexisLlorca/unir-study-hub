"""
Configuración centralizada y gestión de rutas del proyecto unir-study-hub.
Define las rutas base de forma relativa y dinámica para máxima portabilidad.
"""

from pathlib import Path
import os
from dotenv import load_dotenv
from rich.console import Console

# 1. Carga variables de entorno si existe un archivo .env local
load_dotenv()

# 2. Inicializa consola de Rich para salidas elegantes en terminal
console = Console()

# 3. BASE_DIR: Calcula dinámicamente la raíz del proyecto (3 niveles arriba de este archivo)
# __file__ = src/core/config.py -> parent = src/core -> parent = src -> parent = unir-study-hub
BASE_DIR = Path(__file__).resolve().parent.parent.parent

# 4. Rutas del sistema de datos (Entrada y Salida)
DATA_DIR = BASE_DIR / "data"
BANDEJA_ENTRADA_DIR = DATA_DIR / "bandeja_entrada"
BANDEJA_DAM_DIR = BANDEJA_ENTRADA_DIR / "dam"
BANDEJA_PFU_DIR = BANDEJA_ENTRADA_DIR / "pfu"
PROCESADO_DIR = DATA_DIR / "procesado"

# 5. CIBERSEGURIDAD: Rutas de autenticación y sesiones
AUTH_DIR = BASE_DIR / "auth"
SESSION_STATE_FILE = AUTH_DIR / "session_state.json"

# 6. URL del Campus Virtual de la UNIR
UNIR_CAMPUS_URL = os.getenv("UNIR_CAMPUS_URL", "https://campusvirtual.unir.net/")


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
        PROCESADO_DIR,
        AUTH_DIR,
    ]
    for d in directorios:
        d.mkdir(parents=True, exist_ok=True)


# 7. Bloque de prueba ejecutable si lanzamos este script directamente
if __name__ == "__main__":
    console.print("\n[bold cyan]🔧 Comprobando Configuración de unir-study-hub...[/bold cyan]")
    ensure_directories()
    console.print(f"[green]✔ Raíz del proyecto (BASE_DIR):[/green] {BASE_DIR}")
    console.print(f"[green]✔ Bandeja DAM:[/green] {BANDEJA_DAM_DIR}")
    console.print(f"[green]✔ Bandeja PFU:[/green] {BANDEJA_PFU_DIR}")
    console.print(f"[green]✔ Archivo de sesión segura:[/green] {SESSION_STATE_FILE}")
    console.print("[bold green]✨ ¡Todas las rutas verificadas y carpetas aseguradas![/bold green]\n")
