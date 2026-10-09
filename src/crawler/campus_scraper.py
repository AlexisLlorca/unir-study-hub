"""
Crawler y Sincronizador del Campus Virtual de la UNIR.
Utiliza la sesión autenticada con Playwright (Zero-Knowledge) para recorrer
las aulas virtuales de las asignaturas, descargar temarios y recursos automáticamente,
e importar materiales existentes del respaldo local del alumno.
"""

import sys
import shutil
from pathlib import Path
from typing import List, Dict, Optional
from urllib.parse import urljoin

# Importación modular
BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from playwright.sync_api import sync_playwright
from rich.table import Table
from rich.panel import Panel

from src.core.config import (
    console,
    SESSION_STATE_FILE,
    MATERIALES_DIR,
    BANDEJA_DAM_DIR,
    UNIR_CAMPUS_URL,
    ensure_directories,
)
from src.auth.session_manager import tiene_sesion_guardada, login_interactivo

# Ruta del respaldo local de estudios de Alex
RESPALDO_LOCAL_DAM = Path("/home/alexllorca/Respaldo_Estudios/Proyectos/FP DAM unir")


class CampusScraper:
    """Navega por el campus virtual y descarga materiales docentes."""

    def __init__(self):
        ensure_directories()

    def sincronizar_desde_respaldo_local(self) -> int:
        """
        Escanea la carpeta de respaldo local de Alex e importa todos los PDFs
        y temarios oficiales existentes a la bandeja de trabajo de UNIR Study Hub.
        """
        if not RESPALDO_LOCAL_DAM.exists():
            console.print(f"[yellow]⚠ No se encontró el directorio de respaldo local en:[/yellow] {RESPALDO_LOCAL_DAM}")
            return 0

        console.print(f"\n[cyan]📂 Escaneando respaldo local en:[/cyan] [bold]{RESPALDO_LOCAL_DAM}[/bold]...")

        pdfs_encontrados = list(RESPALDO_LOCAL_DAM.glob("**/*.pdf"))
        if not pdfs_encontrados:
            console.print("[yellow]No se encontraron archivos PDF en el respaldo local.[/yellow]")
            return 0

        total_importados = 0
        table = Table(title="📦 Materiales Importados a UNIR Study Hub", border_style="cyan")
        table.add_column("Asignatura", style="cyan")
        table.add_column("Archivo PDF", style="white")
        table.add_column("Destino en Hub", style="green")

        for pdf in pdfs_encontrados:
            # Obtener el nombre de la asignatura a partir de la carpeta contenedora
            asignatura = pdf.parent.name
            if asignatura == "FP DAM unir":
                asignatura = "General"

            # Destino organizado en data/materiales/<asignatura>/
            dir_destino = MATERIALES_DIR / asignatura
            dir_destino.mkdir(parents=True, exist_ok=True)

            archivo_destino = dir_destino / pdf.name
            if not archivo_destino.exists():
                shutil.copy2(pdf, archivo_destino)
                total_importados += 1
                table.add_row(asignatura, pdf.name, f"materiales/{asignatura}")

        if total_importados > 0:
            console.print(table)
            console.print(f"[bold green]✔ ¡Se han importado {total_importados} temarios y PDFs al Hub con éxito![/bold green]\n")
        else:
            console.print(f"[dim]Todos los {len(pdfs_encontrados)} PDFs ya estaban sincronizados en el Hub.[/dim]\n")

        return total_importados

    def explorar_y_descargar_campus(self, url_aula_virtual: Optional[str] = None):
        """
        Abre una sesión de Playwright autenticada con las cookies guardadas
        para navegar por el campus virtual y buscar recursos descargables.
        """
        if not tiene_sesion_guardada():
            console.print("[yellow]⚠ No hay ninguna sesión guardada. Iniciando login interactivo...[/yellow]")
            if not login_interactivo():
                return

        url_objetivo = url_aula_virtual or UNIR_CAMPUS_URL

        console.print(f"\n[cyan]🌐 Conectando al Campus Virtual de UNIR...[/cyan]")
        console.print(f"[dim]URL:[/dim] {url_objetivo}")

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                storage_state=str(SESSION_STATE_FILE),
                viewport={"width": 1280, "height": 800},
            )
            page = context.new_page()

            try:
                page.goto(url_objetivo, wait_until="networkidle", timeout=30000)
                console.print(f"[green]✔ Acceso concedido al campus: {page.title()}[/green]")

                # Detectar enlaces a PDFs o recursos descargables en la página
                enlaces_recursos = page.query_selector_all("a[href*='.pdf'], a[href*='download'], a[href*='document']")
                console.print(f"[cyan]ℹ Enlaces a recursos detectados:[/cyan] {len(enlaces_recursos)}")

                # Detectar posibles enlaces a grabaciones o clases
                enlaces_video = page.query_selector_all("a[href*='kaltura'], a[href*='panopto'], a[href*='zoom'], a[href*='videoconferencia']")
                if enlaces_video:
                    console.print(f"[yellow]🎥 Se encontraron {len(enlaces_video)} enlaces a clases grabadas/vídeos.[/yellow]")

            except Exception as e:
                console.print(f"[bold red]❌ Error navegando por el campus:[/bold red] {e}")
            finally:
                browser.close()

    def listar_materiales_locales(self):
        """Muestra un resumen de todos los materiales organizados por asignatura."""
        table = Table(title="📚 Biblioteca de Materiales y Temarios en el Hub", border_style="cyan")
        table.add_column("Asignatura", style="cyan")
        table.add_column("PDFs Oficiales", style="white", justify="right")
        table.add_column("Ruta Local", style="dim")

        asignaturas = [d for d in MATERIALES_DIR.iterdir() if d.is_dir()]
        if not asignaturas:
            console.print("[dim]No hay materiales organizados todavía. Sincroniza desde el campus o el respaldo local.[/dim]\n")
            return

        for asig in sorted(asignaturas, key=lambda x: x.name):
            pdfs = list(asig.glob("*.pdf"))
            table.add_row(asig.name, f"{len(pdfs)} archivo(s)", str(asig.relative_to(BASE_DIR)))

        console.print(table)
        console.print("")

    def ejecutar_sincronizacion_completa(self) -> int:
        """
        Ejecuta la sincronización completa (respaldo + campus) y emite
        notificaciones de escritorio en caso de nuevos archivos.
        """
        console.print("\n[bold cyan]🔄 Iniciando ciclo de sincronización automática...[/bold cyan]")
        importados = self.sincronizar_desde_respaldo_local()

        if tiene_sesion_guardada():
            self.explorar_y_descargar_campus()
        else:
            console.print("[dim]Sesión del campus pendiente. Para sincronizar en la nube, inicia sesión en la opción 1.[/dim]")

        if importados > 0:
            try:
                import subprocess
                subprocess.run(
                    [
                        "notify-send",
                        "-a", "UNIR Study Hub",
                        "-i", "dialog-information",
                        "🎓 UNIR Study Hub Actualizado",
                        f"Se han incorporado {importados} nuevos temas y materiales al sistema.",
                    ],
                    check=False,
                )
            except Exception:
                pass

        return importados
