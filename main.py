"""
Punto de entrada principal (CLI) para UNIR Study Hub v2.0.
Suite integral de estudio autónomo para 2º DAM & PFU Ingeniería del Software.
Maneja sincronización del campus, descarga de clases, tutor de IA,
generación automática de apuntes, ejercicios y simulacros de examen.
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
    MATERIALES_DIR,
    CLASES_GRABADAS_DIR,
    PROCESADO_DIR,
    BANDEJA_DAM_DIR,
    BANDEJA_PFU_DIR,
    TRANSCRIPCIONES_DIR,
    APUNTES_DIR,
    get_gemini_api_key,
)
from src.auth.session_manager import login_interactivo, tiene_sesion_guardada
from src.crawler.campus_scraper import CampusScraper
from src.media.recordings_manager import RecordingsManager
from src.media.unir_recordings_crawler import UnirRecordingsCrawler
from src.ai.tutor import TutorIA
from src.ai.llm_client import LLMClient
from src.generators.ai_generator import FactoriaAcademicaIA
from src.generators.exam_simulator import (
    ejecutar_test_interactivo,
    bateria_psp,
)

console = Console()


def mostrar_banner():
    estado_auth = "[green]✔ Activa[/green]" if tiene_sesion_guardada() else "[yellow]⚠ No iniciada[/yellow]"
    estado_ia = "[green]✔ Conectada[/green]" if get_gemini_api_key() else "[yellow]⚠ Pendiente de clave[/yellow]"

    console.print(
        Panel.fit(
            "[bold cyan]🎓 UNIR Study Hub — Sistema Autónomo de Estudio y Copiloto de IA[/bold cyan]\n"
            "[bold white]Especializado en 2º DAM & PFU Ingeniería del Software[/bold white]\n"
            f"[dim]Sesión Campus: {estado_auth}  |  Inteligencia Artificial: {estado_ia}[/dim]",
            border_style="cyan",
        )
    )


def menu_sincronizacion(scraper: CampusScraper):
    console.print(
        Panel.fit(
            "[bold cyan]📥 SINCRONIZACIÓN Y DESCARGA DE MATERIALES[/bold cyan]\n\n"
            "[cyan]1.[/cyan] 📦 Importar automáticamente todos los PDFs de tu respaldo local de DAM\n"
            "[cyan]2.[/cyan] 🌐 Conectar al Campus Virtual para buscar recursos nuevos con Playwright\n"
            "[cyan]0.[/cyan] ↩ Volver al menú principal",
            border_style="cyan",
        )
    )
    op = console.input("👉 Selecciona una opción: ").strip()
    if op == "1":
        scraper.sincronizar_desde_respaldo_local()
    elif op == "2":
        scraper.explorar_y_descargar_campus()


def menu_clases_grabadas(recordings: RecordingsManager, tutor: TutorIA):
    crawler = UnirRecordingsCrawler(tutor)
    console.print(
        Panel.fit(
            "[bold cyan]🎙️ GESTOR DE CLASES GRABADAS Y LECCIONES MAGISTRALES (UNIR)[/bold cyan]\n\n"
            "[cyan]1.[/cyan] 🔄 Sincronizar videoclases del campus (Descargar transcripciones oficiales y catálogo)\n"
            "[cyan]2.[/cyan] 📋 Ver catálogo de clases y leer transcripciones disponibles\n"
            "[cyan]3.[/cyan] 🧠 Analizar y resumir clase con IA (Avisos de examen, teoría y dudas)\n"
            "[cyan]4.[/cyan] 📥 Descargar clase externa por URL (Kaltura, Panopto, Zoom con yt-dlp)\n"
            "[cyan]0.[/cyan] ↩ Volver al menú principal",
            border_style="cyan",
        )
    )
    op = console.input("👉 Selecciona una opción: ").strip()
    if op == "1":
        crawler.sincronizar_todas_las_clases()
    elif op == "2":
        trans = list(TRANSCRIPCIONES_DIR.glob("**/*.md"))
        if not trans:
            console.print("[yellow]⚠ No hay transcripciones de clase descargadas aún. Usa la opción 1 para sincronizar.[/yellow]\n")
            return

        table = Table(title=f"🎙️ Catálogo de Clases Grabadas ({len(trans)} disponibles)", border_style="cyan")
        table.add_column("#", style="cyan", justify="right")
        table.add_column("Asignatura", style="green")
        table.add_column("Fecha", style="yellow")
        table.add_column("Sesión / Clase", style="white")

        for idx, t in enumerate(trans[:25], start=1):
            asig = t.parent.name
            nombre = t.stem
            fecha = nombre[:10] if len(nombre) >= 10 and nombre[:4].isdigit() else "Reciente"
            titulo = nombre[11:] if len(nombre) >= 11 and nombre[:4].isdigit() else nombre
            table.add_row(str(idx), asig[:30], fecha, titulo[:40])

        console.print(table)
        console.print(f"[dim]Mostrando 25 de {len(trans)} clases disponibles. Introduce un número para leerla o 0 para volver.[/dim]")
        sel = console.input("👉 Selecciona número de clase: ").strip()
        try:
            i = int(sel) - 1
            if 0 <= i < len(trans):
                elegida = trans[i]
                console.print(f"\n[bold cyan]📄 Lectura de la clase:[/bold cyan] [bold white]{elegida.name}[/bold white]\n")
                with open(elegida, "r", encoding="utf-8") as f:
                    lineas = f.readlines()
                    console.print("".join(lineas[:35]))
                console.print(f"\n[dim]Archivo completo guardado en: {elegida}[/dim]\n")
        except ValueError:
            pass

    elif op == "3":
        trans = list(TRANSCRIPCIONES_DIR.glob("**/*.md"))
        if not trans:
            console.print("[yellow]⚠ No hay transcripciones de clase descargadas aún. Usa la opción 1 para sincronizar.[/yellow]\n")
            return

        table = Table(title="🧠 Selecciona una clase para analizar con IA", border_style="cyan")
        table.add_column("#", style="cyan", justify="right")
        table.add_column("Asignatura", style="green")
        table.add_column("Clase", style="white")

        for idx, t in enumerate(trans[:20], start=1):
            table.add_row(str(idx), t.parent.name[:32], t.stem[:45])

        console.print(table)
        sel = console.input("👉 Selecciona número de clase a procesar (o 0 para cancelar): ").strip()
        try:
            i = int(sel) - 1
            if 0 <= i < len(trans):
                elegida = trans[i]
                asig = elegida.parent.name
                nombre = elegida.stem
                with open(elegida, "r", encoding="utf-8") as f:
                    texto = f.read()

                console.print(f"\n[cyan]🧠 Analizando clase con IA:[/cyan] [bold]{nombre}[/bold]...")
                resumen = tutor.generar_resumen_clase_magistral(texto, asig, nombre)
                if resumen:
                    destino = APUNTES_DIR / asig / f"{nombre}_analisis_clase.md"
                    destino.parent.mkdir(parents=True, exist_ok=True)
                    with open(destino, "w", encoding="utf-8") as f_out:
                        f_out.write(resumen)
                    console.print(f"\n[bold green]✔ Análisis, apuntes y preguntas de examen generados con éxito en:[/bold green]\n{destino}\n")
        except ValueError:
            pass

    elif op == "4":
        url = console.input("👉 Pega la URL de la clase grabada: ").strip()
        if not url:
            return
        asig = console.input("👉 Asignatura (ej: PSP, Acceso a Datos, PMDM): ").strip() or "General"
        nom = console.input("👉 Nombre o título de la clase: ").strip() or "Clase_Magistral"
        recordings.descargar_clase(url, asig, nom, solo_audio=True)


def menu_procesar_tema_ia(factoria: FactoriaAcademicaIA):
    # Buscar todos los PDFs disponibles en materiales y bandejas
    pdfs = list(MATERIALES_DIR.glob("**/*.pdf")) + list(BANDEJA_DAM_DIR.glob("**/*.pdf"))
    if not pdfs:
        console.print(
            "\n[yellow]ℹ No hay ningún PDF cargado en el sistema todavía.[/yellow]\n"
            "[dim]Utiliza la opción 2 del menú para importar tu respaldo de PDFs de DAM.[/dim]\n"
        )
        return

    table = Table(title="📄 Selecciona un PDF para procesar con IA", border_style="cyan")
    table.add_column("#", style="cyan", justify="right")
    table.add_column("Asignatura", style="green")
    table.add_column("Archivo PDF", style="white")

    for i, pdf in enumerate(pdfs[:15], start=1):
        asig = pdf.parent.name
        table.add_row(str(i), asig, pdf.name)

    console.print(table)
    seleccion = console.input("\n👉 Introduce el número del PDF a procesar (o 0 para cancelar): ").strip()
    try:
        idx = int(seleccion) - 1
        if 0 <= idx < len(pdfs):
            pdf_elegido = pdfs[idx]
            asig_elegida = pdf_elegido.parent.name
            tema_elegido = pdf_elegido.stem.replace(" ", "_")
            factoria.procesar_tema_completo(pdf_elegido, asig_elegida, tema_elegido)
    except ValueError:
        pass


from src.scheduler.sync_scheduler import SyncScheduler


def menu_programador_tareas(scheduler: SyncScheduler):
    estado = scheduler.estado_programacion()
    texto_estado = "[green]✔ Activo[/green]" if estado["activo"] else "[yellow]⚠ Inactivo[/yellow]"

    console.print(
        Panel.fit(
            f"[bold cyan]⏰ PROGRAMADOR AUTOMÁTICO DE DESCARGAS (systemd timer)[/bold cyan]\n\n"
            f"Estado actual: {texto_estado}\n"
            f"Próxima ejecución: [white]{estado['proxima_ejecucion']}[/white]\n\n"
            "[bold]Opciones de configuración:[/bold]\n"
            "[cyan]1.[/cyan] 🌙 Activar: Todos los días a las 04:00 AM (Recomendado)\n"
            "[cyan]2.[/cyan] 🔄 Activar: Cada 12 horas (00:00 y 12:00)\n"
            "[cyan]3.[/cyan] ⚡ Activar: Cada 6 horas\n"
            "[cyan]4.[/cyan] 💼 Activar: De Lunes a Viernes a las 08:00 AM\n"
            "[cyan]5.[/cyan] 🛑 Desactivar temporizador automático\n"
            "[cyan]0.[/cyan] ↩ Volver al menú principal",
            border_style="cyan",
        )
    )
    op = console.input("👉 Selecciona una opción: ").strip()
    if op in ["1", "2", "3", "4"]:
        scheduler.activar_programacion(op)
    elif op == "5":
        scheduler.desactivar_programacion()


def ver_biblioteca_completa(scraper: CampusScraper, recordings: RecordingsManager):
    scraper.listar_materiales_locales()
    recordings.listar_clases_disponibles()


def menu_principal():
    ensure_directories()
    llm_client = LLMClient()
    tutor_ia = TutorIA(llm_client)
    recordings = RecordingsManager(llm_client)
    scraper = CampusScraper()
    factoria = FactoriaAcademicaIA(tutor_ia)
    scheduler = SyncScheduler()

    while True:
        mostrar_banner()

        console.print("[bold]Menú de Operaciones:[/bold]")
        console.print("[cyan]1.[/cyan] 🔐 Iniciar sesión segura interactiva en UNIR (Playwright 2FA)")
        console.print("[cyan]2.[/cyan] 📥 Sincronizar e importar materiales (Campus & Respaldo local)")
        console.print("[cyan]3.[/cyan] 🎙️ Clases Grabadas (Descargar con yt-dlp y transcribir con IA)")
        console.print("[cyan]4.[/cyan] 🤖 Chat con tu Tutor de IA (Preguntas, dudas de código y explicaciones)")
        console.print("[cyan]5.[/cyan] ⚡ Procesar Tema con IA (Apuntes + Ejercicios prácticos + Examen)")
        console.print("[cyan]6.[/cyan] 🎯 Practicar Simulacro de Examen Tipo Test (PSP - Tema 1)")
        console.print("[cyan]7.[/cyan] 📚 Ver Biblioteca de Materiales y Clases guardadas")
        console.print("[cyan]8.[/cyan] 🔑 Configurar Clave de API de IA (Google Gemini)")
        console.print("[cyan]9.[/cyan] ⏰ Programar Descarga Automática (systemd timer desatendido)")
        console.print("[cyan]0.[/cyan] 🚪 Salir\n")

        opcion = console.input("👉 Selecciona una opción: ").strip()

        if opcion == "1":
            login_interactivo()
        elif opcion == "2":
            menu_sincronizacion(scraper)
        elif opcion == "3":
            menu_clases_grabadas(recordings, tutor_ia)
        elif opcion == "4":
            tutor_ia.sesion_chat_interactivo()
        elif opcion == "5":
            menu_procesar_tema_ia(factoria)
        elif opcion == "6":
            ejecutar_test_interactivo(bateria_psp)
        elif opcion == "7":
            ver_biblioteca_completa(scraper, recordings)
        elif opcion == "8":
            llm_client.solicitar_api_key_interactiva()
        elif opcion == "9":
            menu_programador_tareas(scheduler)
        elif opcion == "0":
            console.print("\n[bold green]👋 ¡Hasta pronto Alex! Éxitos con el estudio.[/bold green]\n")
            break


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] in ["--sync", "-s"]:
        ensure_directories()
        scraper = CampusScraper()
        scraper.ejecutar_sincronizacion_completa()
        sys.exit(0)

    menu_principal()
