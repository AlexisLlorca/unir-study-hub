"""
Navegador Académico y Centro de Estudio Interactivo para 2º DAM (UNIR).
Permite explorar asignaturas de forma ordenada, acceder a temarios oficiales,
leer transcripciones de clases grabadas, generar guías de estudio por tema
y realizar tests de examen interactivos en la terminal.
"""

import sys
import json
from pathlib import Path
from typing import Optional, List, Dict, Any

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from src.core.config import (
    console,
    MATERIALES_DIR,
    TRANSCRIPCIONES_DIR,
    CLASES_GRABADAS_DIR,
    GUIAS_DIR,
    EXAMENES_DIR,
    EJERCICIOS_DIR,
    ensure_directories,
)
from src.core.academic_structure import (
    ASIGNATURAS_2DAM,
    ASIGNATURAS_1DAM,
    obtener_catalogo_academico,
    reorganizar_biblioteca_completa,
)
from src.ai.tutor import TutorIA
from src.generators.study_guide_generator import GeneradorGuiaEstudio
from src.generators.exam_simulator import PreguntaExamen, ejecutar_test_interactivo


class NavegadorAcademico:
    """Navegador interactivo por asignaturas y temarios de 2º DAM."""

    def __init__(self, tutor: Optional[TutorIA] = None):
        self.tutor = tutor or TutorIA()
        self.generador = GeneradorGuiaEstudio(self.tutor)
        ensure_directories()

    def mostrar_menu_asignaturas(self):
        """Muestra la tabla limpia de asignaturas con sus recursos reales."""
        reorganizar_biblioteca_completa()
        catalogo = obtener_catalogo_academico()

        while True:
            console.print(
                Panel.fit(
                    "[bold cyan]📚 BIBLIOTECA Y NAVEGADOR ACADÉMICO — 2º DAM (UNIR)[/bold cyan]\n"
                    "[dim]Selecciona una asignatura para explorar sus temas, clases grabadas y tests.[/dim]",
                    border_style="cyan",
                )
            )

            table = Table(title="🎓 Asignaturas Oficiales de 2º DAM", border_style="cyan")
            table.add_column("#", style="cyan", justify="right")
            table.add_column("Asignatura", style="bold white")
            table.add_column("Temarios (PDF)", style="green", justify="right")
            table.add_column("Clases Grabadas", style="yellow", justify="right")
            table.add_column("Guías / Tests IA", style="magenta", justify="right")

            lista_asignaturas = [a for a in ASIGNATURAS_2DAM if a in catalogo]

            for i, asig in enumerate(lista_asignaturas, start=1):
                info = catalogo[asig]
                n_mat = len(info["materiales"])
                n_clases = len(info["transcripciones"])
                
                dir_guias = GUIAS_DIR / asig
                dir_examenes = EXAMENES_DIR / asig
                n_guias = len(list(dir_guias.glob("*.md"))) if dir_guias.exists() else 0
                n_tests = len(list(dir_examenes.glob("*.json"))) if dir_examenes.exists() else 0
                
                estado_ia = f"{n_guias} guías | {n_tests} tests" if (n_guias or n_tests) else "Pendiente"

                table.add_row(
                    str(i),
                    asig,
                    f"{n_mat} archivo(s)",
                    f"{n_clases} clase(s)" if n_clases > 0 else "Sin clases",
                    estado_ia,
                )

            console.print(table)
            console.print("[dim]Introduce el número de asignatura, 'H' para ver 1º DAM histórico o 0 para volver.[/dim]")

            sel = console.input("\n👉 Selecciona una opción: ").strip()
            if sel in ["0", "salir", "exit", "q"]:
                break

            if sel.lower() == "h":
                self.mostrar_historico_1dam(catalogo)
                continue

            try:
                idx = int(sel) - 1
                if 0 <= idx < len(lista_asignaturas):
                    asig_elegida = lista_asignaturas[idx]
                    self.navegar_asignatura(asig_elegida, catalogo[asig_elegida])
            except ValueError:
                pass

    def navegar_asignatura(self, asignatura: str, info: Dict[str, Any]):
        """Menú dedicado para una asignatura específica."""
        while True:
            dir_guias = GUIAS_DIR / asignatura
            dir_examenes = EXAMENES_DIR / asignatura
            guias_existentes = list(dir_guias.glob("*.md")) if dir_guias.exists() else []
            tests_existentes = list(dir_examenes.glob("*.json")) if dir_examenes.exists() else []

            console.print(
                Panel.fit(
                    f"[bold cyan]📖 ASIGNATURA: {asignatura.upper()}[/bold cyan]\n\n"
                    f"• 📄 [white]Temarios y Recursos oficiales:[/white] [green]{len(info['materiales'])} archivo(s)[/green]\n"
                    f"• 🎙️ [white]Videoclases con transcripción:[/white] [yellow]{len(info['transcripciones'])} clase(s)[/yellow]\n"
                    f"• 📘 [white]Guías de estudio generadas:[/white] [magenta]{len(guias_existentes)} tema(s)[/magenta]\n"
                    f"• 🎯 [white]Tests interactivos listos:[/white] [cyan]{len(tests_existentes)} test(s)[/cyan]\n\n"
                    "[bold]Opciones de estudio:[/bold]\n"
                    "[cyan]1.[/cyan] 📘 Generar Guía de Estudio + Test por Tema con IA (Elige tema a procesar)\n"
                    "[cyan]2.[/cyan] 🎯 Practicar Test de Examen Interactivo de esta asignatura\n"
                    "[cyan]3.[/cyan] 🎴 Exportar Flashcards para Anki / AnkiDroid de esta asignatura\n"
                    "[cyan]4.[/cyan] 🛠️ Asistente de Actividades Prácticas (tisupdam / tareas evaluables)\n"
                    "[cyan]5.[/cyan] 🎙️ Ver y leer Clases Grabadas de esta asignatura\n"
                    "[cyan]6.[/cyan] 📄 Ver lista de Temarios Oficiales y PDFs\n"
                    "[cyan]7.[/cyan] 🤖 Abrir chat con NOVA enfocado en esta asignatura\n"
                    "[cyan]0.[/cyan] ↩ Volver a la lista de asignaturas",
                    border_style="cyan",
                )
            )

            op = console.input("👉 Selecciona una opción: ").strip()
            if op == "0":
                break
            elif op == "1":
                self.menu_generar_guia_tema(asignatura, info)
            elif op == "2":
                self.menu_practicar_test_asignatura(asignatura)
            elif op == "3":
                from src.generators.flashcard_exporter import ExportadorFlashcards
                ExportadorFlashcards().exportar_asignatura(asignatura)
            elif op == "4":
                self.menu_actividades_asignatura(asignatura)
            elif op == "5":
                self.menu_leer_clases_asignatura(asignatura, info)
            elif op == "6":
                self.menu_listar_materiales_asignatura(asignatura, info)
            elif op == "7":
                self.tutor.configurar_contexto(asignatura)
                self.tutor.sesion_chat_interactivo()

    def menu_generar_guia_tema(self, asignatura: str, info: Dict[str, Any]):
        """Permite al alumno seleccionar un tema oficial para generar Guía + Test + Examen."""
        materiales = [f for f in info["materiales"] if f.suffix.lower() == ".pdf"]
        if not materiales:
            console.print("[yellow]⚠ No hay archivos PDF en esta asignatura para procesar.[/yellow]\n")
            return

        table = Table(title=f"📄 Temas disponibles en {asignatura}", border_style="cyan")
        table.add_column("#", style="cyan", justify="right")
        table.add_column("Archivo del Tema", style="white")

        for idx, m in enumerate(materiales[:20], start=1):
            table.add_row(str(idx), m.name)

        console.print(table)
        sel = console.input("\n👉 Introduce el número del tema a generar con IA (o 0 para volver): ").strip()
        try:
            i = int(sel) - 1
            if 0 <= i < len(materiales):
                pdf_elegido = materiales[i]
                nombre_tema = pdf_elegido.stem.replace("_", " ")

                # Buscar si hay alguna clase transcrita que coincida
                trans_elegida = None
                if info["transcripciones"]:
                    trans_elegida = info["transcripciones"][0]

                self.generador.generar_paquete_tema(
                    asignatura=asignatura,
                    nombre_tema=nombre_tema,
                    ruta_pdf=pdf_elegido,
                    ruta_transcripcion=trans_elegida,
                )
        except ValueError:
            pass

    def menu_practicar_test_asignatura(self, asignatura: str):
        """Busca los tests en JSON generados para la asignatura y lanza el simulador interactivo."""
        dir_examenes = EXAMENES_DIR / asignatura
        tests = list(dir_examenes.glob("*.json")) if dir_examenes.exists() else []

        if not tests:
            console.print(
                f"\n[yellow]⚠ No hay tests generados todavía para {asignatura}.[/yellow]\n"
                "[dim]Utiliza la opción 1 para generar la guía y el test de cualquier tema con IA.[/dim]\n"
            )
            return

        table = Table(title=f"🎯 Tests Interactivos Disponibles — {asignatura}", border_style="cyan")
        table.add_column("#", style="cyan", justify="right")
        table.add_column("Tema / Test", style="white")

        for idx, t in enumerate(tests, start=1):
            table.add_row(str(idx), t.stem.replace("_", " "))

        console.print(table)
        sel = console.input("\n👉 Selecciona el número de test a realizar (o 0 para volver): ").strip()
        try:
            i = int(sel) - 1
            if 0 <= i < len(tests):
                test_elegido = tests[i]
                with open(test_elegido, "r", encoding="utf-8") as f:
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
                ejecutar_test_interactivo(
                    preguntas,
                    asignatura=asignatura,
                    tema=test_elegido.stem.replace("_test", "").replace("_", " "),
                )
        except Exception as e:
            console.print(f"[red]Error cargando test: {e}[/red]")

    def menu_actividades_asignatura(self, asignatura: str):
        """Muestra las actividades prácticas disponibles y permite generar su plantilla con IA."""
        from src.generators.assignment_helper import AsistenteActividades
        asistente = AsistenteActividades(self.tutor)
        acts = asistente.listar_actividades_asignatura(asignatura)

        if not acts:
            console.print(f"[yellow]⚠ No se detectaron enunciados de actividades evaluables en {asignatura}.[/yellow]\n")
            return

        table = Table(title=f"🛠️ Actividades y Tareas Evaluables — {asignatura}", border_style="cyan")
        table.add_column("#", style="cyan", justify="right")
        table.add_column("Archivo de la Tarea", style="white")

        for idx, a in enumerate(acts, start=1):
            table.add_row(str(idx), a.name)

        console.print(table)
        sel = console.input("\n👉 Selecciona el número de actividad para generar la plantilla con IA (o 0 para volver): ").strip()
        try:
            i = int(sel) - 1
            if 0 <= i < len(acts):
                asistente.generar_guia_actividad(acts[i], asignatura)
        except ValueError:
            pass

    def menu_leer_clases_asignatura(self, asignatura: str, info: Dict[str, Any]):
        """Permite leer las transcripciones literales de las clases magistrales."""
        trans = info["transcripciones"]
        if not trans:
            console.print(f"[yellow]⚠ No hay clases grabadas sincronizadas para {asignatura}.[/yellow]\n")
            return

        table = Table(title=f"🎙️ Clases Grabadas de {asignatura}", border_style="cyan")
        table.add_column("#", style="cyan", justify="right")
        table.add_column("Fecha", style="yellow")
        table.add_column("Sesión", style="white")

        for idx, t in enumerate(trans, start=1):
            fecha = t.stem[:10] if len(t.stem) >= 10 and t.stem[:4].isdigit() else "Reciente"
            titulo = t.stem[11:] if len(t.stem) >= 11 and t.stem[:4].isdigit() else t.stem
            table.add_row(str(idx), fecha, titulo[:45])

        console.print(table)
        sel = console.input("\n👉 Introduce el número de clase para leer su transcripción (o 0 para volver): ").strip()
        try:
            i = int(sel) - 1
            if 0 <= i < len(trans):
                clase_elegida = trans[i]
                console.print(f"\n[bold cyan]📄 Lectura de la lección magistral:[/bold cyan] {clase_elegida.name}\n")
                with open(clase_elegida, "r", encoding="utf-8") as f:
                    lineas = f.readlines()
                    console.print("".join(lineas[:40]))
                console.print(f"\n[dim]Archivo completo guardado en: {clase_elegida}[/dim]\n")
        except ValueError:
            pass

    def menu_listar_materiales_asignatura(self, asignatura: str, info: Dict[str, Any]):
        """Muestra los PDFs y documentos oficiales."""
        materiales = info["materiales"]
        table = Table(title=f"📄 Materiales Oficiales de {asignatura}", border_style="cyan")
        table.add_column("#", style="cyan", justify="right")
        table.add_column("Documento", style="white")
        table.add_column("Formato", style="green")

        for idx, m in enumerate(materiales, start=1):
            table.add_row(str(idx), m.name, m.suffix.upper())

        console.print(table)
        console.input("\n[dim]Pulsa Enter para volver al menú de la asignatura...[/dim]")

    def mostrar_historico_1dam(self, catalogo: Dict[str, Any]):
        """Muestra las asignaturas archivadas de 1er curso."""
        table = Table(title="📦 Materiales de 1º DAM (Histórico)", border_style="yellow")
        table.add_column("Asignatura", style="white")
        table.add_column("Documentos", style="green", justify="right")

        for asig in ASIGNATURAS_1DAM:
            if asig in catalogo:
                info = catalogo[asig]
                table.add_row(asig, f"{len(info['materiales'])} archivo(s)")

        console.print(table)
        console.input("\n[dim]Pulsa Enter para volver...[/dim]")
