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
    GUIAS_DIR,
    EXAMENES_DIR,
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

from src.core.academic_structure import (
    ASIGNATURAS_2DAM,
    ASIGNATURAS_1DAM,
    obtener_catalogo_academico,
    reorganizar_biblioteca_completa,
)
from src.core.study_navigator import NavegadorAcademico
from src.core.progress_tracker import GestorProgreso
from src.ui.web_server import iniciar_servidor_web

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


def menu_sincronizacion_y_ajustes(scraper: CampusScraper, scheduler: Any, llm_client: Any):
    console.print(
        Panel.fit(
            "[bold cyan]⚙️ SINCRONIZACIÓN Y AJUSTES DEL SISTEMA[/bold cyan]\n\n"
            "[cyan]1.[/cyan] 🔄 Sincronización completa ahora (Campus + Respaldo + Videoclases)\n"
            "[cyan]2.[/cyan] 🔐 Renovar sesión de UNIR con Playwright (2FA interactivo)\n"
            "[cyan]3.[/cyan] ⏰ Configurar Descargas Nocturnas Automáticas (systemd timer)\n"
            "[cyan]4.[/cyan] 🔑 Cambiar o Configurar Clave de API de Gemini\n"
            "[cyan]0.[/cyan] ↩ Volver al menú principal",
            border_style="cyan",
        )
    )
    op = console.input("👉 Selecciona una opción: ").strip()
    if op == "1":
        scraper.ejecutar_sincronizacion_completa()
    elif op == "2":
        login_interactivo()
    elif op == "3":
        menu_programador_tareas(scheduler)
    elif op == "4":
        llm_client.solicitar_api_key_interactiva()


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


def ver_biblioteca_completa(recordings: RecordingsManager):
    reorganizar_biblioteca_completa()
    catalogo = obtener_catalogo_academico()

    table = Table(title="📚 Biblioteca Académica Consolidada — 2º DAM", border_style="cyan")
    table.add_column("Asignatura", style="bold cyan")
    table.add_column("Temarios (PDF)", style="green", justify="right")
    table.add_column("Videoclases", style="yellow", justify="right")
    table.add_column("Guías / Tests IA", style="magenta", justify="right")

    for asig in ASIGNATURAS_2DAM:
        if asig in catalogo:
            info = catalogo[asig]
            n_mat = len(info["materiales"])
            n_clases = len(info["transcripciones"])
            dir_guias = GUIAS_DIR / asig
            dir_examenes = EXAMENES_DIR / asig
            n_guias = len(list(dir_guias.glob("*.md"))) if dir_guias.exists() else 0
            n_tests = len(list(dir_examenes.glob("*.json"))) if dir_examenes.exists() else 0
            estado_ia = f"{n_guias} guías | {n_tests} tests" if (n_guias or n_tests) else "Pendiente"

            table.add_row(asig, f"{n_mat} docs", f"{n_clases} clases", estado_ia)

    console.print(table)
    console.print("")
    recordings.listar_clases_disponibles()


def menu_simulacros_test():
    import json
    tests = list(EXAMENES_DIR.glob("**/*.json"))
    if not tests:
        console.print("[cyan]Iniciando test modelo oficial de PSP...[/cyan]\n")
        ejecutar_test_interactivo(bateria_psp)
        return

    table = Table(title="🎯 Simulacros de Examen Tipo Test Disponibles", border_style="cyan")
    table.add_column("#", style="cyan", justify="right")
    table.add_column("Asignatura", style="green")
    table.add_column("Test / Tema", style="white")

    table.add_row("1", "PSP", "Batería Oficial PSP — Multiproceso (Tema 1)")
    for idx, t in enumerate(tests, start=2):
        table.add_row(str(idx), t.parent.name, t.stem.replace("_", " "))

    console.print(table)
    sel = console.input("\n👉 Selecciona el test a practicar (o 0 para volver): ").strip()
    if sel == "1":
        ejecutar_test_interactivo(bateria_psp)
    else:
        try:
            i = int(sel) - 2
            if 0 <= i < len(tests):
                t_elegido = tests[i]
                with open(t_elegido, "r", encoding="utf-8") as f:
                    data = json.load(f)
                preguntas = [
                    PreguntaExamen(
                        enunciado=p["enunciado"],
                        opciones=p["opciones"],
                        correcta=p["correcta"],
                        justificacion=p["justificacion"],
                    )
                    for p in data
                ]
                ejecutar_test_interactivo(preguntas)
        except Exception as e:
            console.print(f"[red]Error cargando test: {e}[/red]")


def menu_principal():
    ensure_directories()
    llm_client = LLMClient()
    tutor_ia = TutorIA(llm_client)
    recordings = RecordingsManager(llm_client)
    scraper = CampusScraper()
    scheduler = SyncScheduler()
    navigator = NavegadorAcademico(tutor_ia)
    tracker = GestorProgreso()

    while True:
        mostrar_banner()

        console.print("[bold]Centro de Mando de Estudio (2º DAM):[/bold]\n")
        console.print("[cyan]1.[/cyan] 🎓 [bold white]Centro de Estudio por Asignaturas[/bold white] [dim](Temas, Guías IA, Videoclases y Tareas)[/dim]")
        console.print("[cyan]2.[/cyan] 🎯 [bold white]Simulacros de Examen y Tests[/bold white] [dim](Entrenar con nota y justificaciones)[/dim]")
        console.print("[cyan]3.[/cyan] 📊 [bold white]Expediente y Semáforo de Examen[/bold white] [dim](Notas medias y estado de preparación)[/dim]")
        console.print("[cyan]4.[/cyan] 🤖 [bold white]Tutor de IA NOVA en Directo[/bold white] [dim](Dudas de código y consultas libres)[/dim]")
        console.print("[cyan]5.[/cyan] 🎙️ [bold white]Videoclases Magistrales UNIR[/bold white] [dim](49 lecciones transcritas y enlaces)[/dim]")
        console.print("[cyan]6.[/cyan] 🌐 [bold white]Iniciar Interfaz Gráfica Web[/bold white] [dim](Dashboard visual en http://localhost:8080)[/dim]")
        console.print("[cyan]7.[/cyan] ⚙️ [bold white]Sincronización Campus y Ajustes[/bold white] [dim](Sesión, descargas y timer)[/dim]")
        console.print("[cyan]0.[/cyan] 🚪 Salir\n")

        opcion = console.input("👉 Selecciona una opción: ").strip()

        if opcion == "1":
            navigator.mostrar_menu_asignaturas()
        elif opcion == "2":
            menu_simulacros_test()
        elif opcion == "3":
            tracker.mostrar_dashboard_progreso()
        elif opcion == "4":
            tutor_ia.sesion_chat_interactivo()
        elif opcion == "5":
            menu_clases_grabadas(recordings, tutor_ia)
        elif opcion == "6":
            iniciar_servidor_web()
        elif opcion == "7":
            menu_sincronizacion_y_ajustes(scraper, scheduler, llm_client)
        elif opcion == "0":
            console.print("\n[bold green]👋 ¡Hasta pronto Alex! Éxitos con el estudio.[/bold green]\n")
            break


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] in ["--sync", "-s"]:
        ensure_directories()
        scraper = CampusScraper()
        scraper.ejecutar_sincronizacion_completa()
        sys.exit(0)
    elif len(sys.argv) > 1 and sys.argv[1] in ["--web", "-w"]:
        ensure_directories()
        iniciar_servidor_web()
        sys.exit(0)

    menu_principal()
