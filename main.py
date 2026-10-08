"""
Punto de entrada principal (CLI) para unir-study-hub.
Proporciona un menú interactivo en terminal para gestionar la autenticación segura,
el procesamiento de temarios y la extracción de código vivo de DAM y PFU.
"""

import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from src.core.config import (
    ensure_directories,
    BANDEJA_DAM_DIR,
    BANDEJA_PFU_DIR,
    PROCESADO_DIR,
    SESSION_STATE_FILE,
)
from src.auth.session_manager import login_interactivo, tiene_sesion_guardada
from src.extractors.pdf_parser import extraer_texto_pdf
from src.generators.exam_simulator import (
    ejecutar_test_interactivo,
    bateria_psp,
)

console = Console()


def mostrar_banner():
    console.print(
        Panel.fit(
            "[bold cyan]🎓 UNIR Study Hub v2.0[/bold cyan]\n"
            "[bold white]Asistente Autónomo de Estudio y Código para 2º DAM & PFU Ingeniería del Software[/bold white]\n"
            "[dim]Security by Design • Zero-Knowledge Auth • Code Extraction[/dim]",
            border_style="cyan",
        )
    )


def listar_archivos_bandeja():
    """
    Muestra en una tabla los documentos pendientes en la bandeja de entrada.
    """
    table = Table(title="📥 Bandeja de Entrada (Dropzone)", border_style="cyan")
    table.add_column("Programa", style="cyan", justify="center")
    table.add_column("Asignatura / Carpeta", style="green")
    table.add_column("Archivos PDF encontrados", style="white")

    archivos_dam = list(BANDEJA_DAM_DIR.glob("**/*.pdf"))
    archivos_pfu = list(BANDEJA_PFU_DIR.glob("**/*.pdf"))

    table.add_row("2º DAM", "data/bandeja_entrada/dam", f"{len(archivos_dam)} archivo(s)")
    table.add_row("PFU Ing. Software", "data/bandeja_entrada/pfu", f"{len(archivos_pfu)} archivo(s)")

    console.print(table)


def menu_principal():
    ensure_directories()
    while True:
        mostrar_banner()
        estado_auth = "[green]✔ Activa[/green]" if tiene_sesion_guardada() else "[yellow]⚠ No iniciada[/yellow]"
        console.print(f"Estado de sesión UNIR: {estado_auth}\n")

        console.print("[bold]Opciones disponibles:[/bold]")
        console.print("[cyan]1.[/cyan] 🔐 Iniciar sesión segura interactiva en UNIR (Playwright Storage State)")
        console.print("[cyan]2.[/cyan] 📥 Ver archivos en la Bandeja de Entrada (Dropzone)")
        console.print("[cyan]3.[/cyan] 📄 Procesar un PDF de prueba de la bandeja")
        console.print("[cyan]4.[/cyan] 📁 Ver estructura de carpetas del sistema")
        console.print("[cyan]5.[/cyan] 🎯 Practicar con Simulacro de Examen Tipo Test (PSP - Tema 1)")
        console.print("[cyan]0.[/cyan] 🚪 Salir\n")

        opcion = console.input("👉 Selecciona una opción: ").strip()

        if opcion == "1":
            login_interactivo()
        elif opcion == "2":
            listar_archivos_bandeja()
        elif opcion == "3":
            pdfs = list(BANDEJA_DAM_DIR.glob("**/*.pdf")) + list(BANDEJA_PFU_DIR.glob("**/*.pdf"))
            if not pdfs:
                console.print(
                    "\n[yellow]ℹ No hay ningún PDF en la bandeja de entrada todavía.[/yellow]\n"
                    "[dim]Copia cualquier PDF de apuntes en:[/dim]\n"
                    f"  [cyan]{BANDEJA_DAM_DIR}[/cyan] (para DAM)\n"
                    f"  [cyan]{BANDEJA_PFU_DIR}[/cyan] (para PFU)\n"
                )
            else:
                primer_pdf = pdfs[0]
                console.print(f"\n[green]Procesando:[/green] {primer_pdf.name}...")
                datos = extraer_texto_pdf(primer_pdf)
                console.print(f"[green]✔ Total páginas extraídas:[/green] {datos['total_paginas']}")
        elif opcion == "4":
            console.print(f"\n[green]✔ Base:[/green] {BASE_DIR}")
            console.print(f"[green]✔ DAM:[/green] {BANDEJA_DAM_DIR}")
            console.print(f"[green]✔ PFU:[/green] {BANDEJA_PFU_DIR}")
            console.print(f"[green]✔ Procesado:[/green] {PROCESADO_DIR}\n")
        elif opcion == "5":
            ejecutar_test_interactivo(bateria_psp)
        elif opcion == "0":
            console.print("\n[bold cyan]¡Hasta la próxima, Alexis! A por ese Grado y DAM.[/bold cyan] 👋\n")
            break
        else:
            console.print("[red]Opción no válida. Inténtalo de nuevo.[/red]\n")


if __name__ == "__main__":
    menu_principal()
