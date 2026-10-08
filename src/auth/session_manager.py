"""
Gestor de sesiones seguras para el campus virtual de la UNIR.
Implementa el patrón 'Storage State' de Playwright:
Permite autenticarse manualmente una sola vez con 2FA y reutilizar las cookies de sesión
sin almacenar jamás contraseñas en texto plano ni en variables de entorno.
"""

import json
import sys
from pathlib import Path
from typing import Optional

# Asegura que la raíz del proyecto esté en sys.path para importaciones modulares
BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from playwright.sync_api import sync_playwright, BrowserContext, Page
from rich.panel import Panel

from src.core.config import (
    console,
    SESSION_STATE_FILE,
    AUTH_DIR,
    UNIR_CAMPUS_URL,
    ensure_directories,
)


def login_interactivo(timeout_segundos: int = 300) -> bool:
    """
    Abre una ventana de Chromium real (con interfaz gráfica) para que el alumno
    inicie sesión de forma segura con su usuario, contraseña y 2FA directamente en la UNIR.
    Una vez logueado, guarda únicamente las cookies de sesión (Storage State).
    """
    ensure_directories()

    console.print(
        Panel.fit(
            "[bold cyan]🔐 INICIO DE SESIÓN SEGURO (Zero-Knowledge)[/bold cyan]\n\n"
            "[yellow]1.[/yellow] Se abrirá una ventana de Chromium en tu pantalla.\n"
            "[yellow]2.[/yellow] Introduce tu usuario, contraseña y código 2FA en la web oficial de UNIR.\n"
            "[yellow]3.[/yellow] Cuando veas tu panel de asignaturas (dentro del campus), vuelve aquí y pulsa [bold green]ENTER[/bold green].\n\n"
            "[italic green]Tus contraseñas NUNCA se guardan en ningún archivo ni son leídas por ningún script.[/italic green]",
            border_style="cyan",
        )
    )

    with sync_playwright() as p:
        # 1. Abre el navegador con interfaz gráfica visible (headless=False)
        browser = p.chromium.launch(headless=False)
        context = browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
        )
        page = context.new_page()

        console.print(f"[dim]Navegando a {UNIR_CAMPUS_URL}...[/dim]")
        page.goto(UNIR_CAMPUS_URL)

        # 2. Espera a que el usuario pulse Enter en la terminal tras haber completado el login
        try:
            input("\n👉 Pulsa ENTER en esta terminal una vez hayas iniciado sesión con éxito en la ventana del navegador...")
        except EOFError:
            pass

        # 3. Guarda el estado de almacenamiento (cookies de sesión cifradas y tokens de origen)
        context.storage_state(path=str(SESSION_STATE_FILE))
        browser.close()

    if SESSION_STATE_FILE.exists():
        console.print(
            Panel.fit(
                f"[bold green]✔ ¡Sesión guardada con éxito![/bold green]\n"
                f"[dim]Ruta segura:[/dim] [cyan]{SESSION_STATE_FILE}[/cyan]\n"
                f"[dim]Nota:[/dim] Este archivo está en el .gitignore y nunca se subirá a GitHub.",
                border_style="green",
            )
        )
        return True
    else:
        console.print("[bold red]❌ Error: No se pudo generar el archivo de sesión.[/bold red]")
        return False


def tiene_sesion_guardada() -> bool:
    """
    Comprueba si existe el archivo de estado de sesión.
    """
    return SESSION_STATE_FILE.exists() and SESSION_STATE_FILE.stat().st_size > 0


def crear_contexto_autenticado(p) -> Optional[BrowserContext]:
    """
    Crea un contexto de navegador en segundo plano (headless) reutilizando
    la sesión guardada previamente sin necesidad de pedir credenciales.
    """
    if not tiene_sesion_guardada():
        console.print("[yellow]⚠ No hay ninguna sesión guardada. Ejecuta primero el login interactivo.[/yellow]")
        return None

    browser = p.chromium.launch(headless=True)
    context = browser.new_context(storage_state=str(SESSION_STATE_FILE))
    return context


if __name__ == "__main__":
    console.print("\n[bold]Comprobador del Gestor de Sesiones[/bold]")
    if tiene_sesion_guardada():
        console.print(f"[green]✔ Ya existe un archivo de sesión en:[/green] {SESSION_STATE_FILE}")
        console.print("[dim]Si deseas renovar la sesión, ejecuta:[/dim] [cyan]login_interactivo()[/cyan]")
    else:
        console.print("[yellow]ℹ No se ha detectado ninguna sesión previa.[/yellow]")
        console.print("[dim]Para iniciar sesión por primera vez, llama a este script con:[/dim] [cyan]python -c 'from src.auth.session_manager import login_interactivo; login_interactivo()'[/cyan]")
