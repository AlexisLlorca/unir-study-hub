"""
Generador Autónomo de Guías de Estudio, Tests y Exámenes por Tema para 2º DAM (UNIR).
Combina el temario oficial (PDF) y lo que el profesor explicó en clase magistral (transcripciones)
para producir una guía de alto rendimiento y simulacros tipo test interactivos.
"""

import json
import re
from pathlib import Path
from typing import Dict, List, Any, Optional

from rich.panel import Panel
from rich.table import Table

from src.core.config import (
    console,
    GUIAS_DIR,
    EXAMENES_DIR,
    EJERCICIOS_DIR,
    APUNTES_DIR,
    TRANSCRIPCIONES_DIR,
    ensure_directories,
)
from src.extractors.pdf_parser import extraer_texto_pdf
from src.generators.exam_simulator import PreguntaExamen, generar_markdown_examen
from src.ai.tutor import TutorIA


class GeneradorGuiaEstudio:
    """Genera el paquete completo de estudio (Guía + Test interactivo + Examen) por tema."""

    def __init__(self, tutor: Optional[TutorIA] = None):
        self.tutor = tutor or TutorIA()
        ensure_directories()

    def generar_paquete_tema(
        self,
        asignatura: str,
        nombre_tema: str,
        ruta_pdf: Optional[Path] = None,
        ruta_transcripcion: Optional[Path] = None,
    ) -> Dict[str, Any]:
        """
        Genera:
        1. Guía de estudio y apuntes estructurados (.md)
        2. Test interactivo (.json) y simulacro (.md)
        3. Ejercicios prácticos con soluciones (.md)
        """
        slug_tema = re.sub(r'[\\/*?:"<>|]', "", nombre_tema).replace(" ", "_")

        console.print(
            Panel.fit(
                f"[bold cyan]📘 GENERANDO GUÍA DE ESTUDIO Y EXAMEN CON IA[/bold cyan]\n"
                f"[white]Asignatura:[/white] [bold green]{asignatura}[/bold green]\n"
                f"[white]Tema:[/white] [bold yellow]{nombre_tema}[/bold yellow]",
                border_style="cyan",
            )
        )

        # 1. Extraer texto del PDF si existe
        texto_pdf = ""
        if ruta_pdf and ruta_pdf.exists():
            console.print(f"[cyan]1/4. Extrayendo contenido del temario oficial:[/cyan] {ruta_pdf.name}...")
            datos_pdf = extraer_texto_pdf(ruta_pdf)
            texto_pdf = datos_pdf.get("texto_completo", "")

        # 2. Extraer texto de clase grabada si existe
        texto_clase = ""
        if ruta_transcripcion and ruta_transcripcion.exists():
            console.print(f"[cyan]2/4. Vinculando lección magistral explicada por el profesor:[/cyan] {ruta_transcripcion.name}...")
            with open(ruta_transcripcion, "r", encoding="utf-8") as f:
                texto_clase = f.read()
        else:
            # Buscar automáticamente si hay transcripciones de esta asignatura
            dir_trans = TRANSCRIPCIONES_DIR / asignatura
            if dir_trans.exists():
                trans_disponibles = list(dir_trans.glob("*.md"))
                if trans_disponibles:
                    # Usar la transcripción más relevante o reciente
                    with open(trans_disponibles[-1], "r", encoding="utf-8") as f:
                        texto_clase = f.read()

        contexto_combinado = ""
        if texto_pdf:
            contexto_combinado += f"=== TEMARIO OFICIAL DEL TEMA ===\n{texto_pdf[:25000]}\n\n"
        if texto_clase:
            contexto_combinado += f"=== LO QUE EXPLICÓ EL PROFESOR EN CLASE MAGISTRAL ===\n{texto_clase[:20000]}\n\n"

        if not contexto_combinado:
            console.print("[red]❌ No se encontró texto de temario ni de transcripción para este tema.[/red]")
            return {}

        resultados = {}

        # 3. Generar Guía de Estudio de Alto Rendimiento
        console.print("\n[cyan]3/4. Redactando Guía de Estudio de Alto Rendimiento...[/cyan]")
        prompt_guia = (
            f"Eres NOVA, tutor de IA especializado en FP DAM (Desarrollo de Aplicaciones Multiplataforma) de UNIR.\n"
            f"A partir del siguiente material oficial de '{asignatura}' (Tema: {nombre_tema}):\n\n"
            f"{contexto_combinado}\n\n"
            "Redacta una GUÍA DE ESTUDIO COMPLETA Y EXHAUSTIVA en Markdown con esta estructura exacta:\n"
            "# 📘 GUÍA DE ESTUDIO OFICIAL: " + nombre_tema.upper() + " — " + asignatura.upper() + "\n"
            "> Material de alto rendimiento sintetizado con temario oficial y clases magistrales de UNIR.\n\n"
            "## 📌 1. Resumen Ejecutivo (Lo imprescindible que debes saber sin paja)\n"
            "## 📢 2. Avisos y Trampas del Profesor en Clase (Qué dijo el profesor en clase que caería en examen, avisos y errores comunes)\n"
            "## 🧠 3. Conceptos Clave y Definiciones de Examen (Tabla Markdown con Término, Definición Técnica y Relevancia en Examen)\n"
            "## 💻 4. Código y Patrones de Implementación (Código completo, limpio, moderno y comentado paso a paso)\n"
            "## ⚠️ 5. Puntos Críticos y Preguntas Capciosas de Examen (Preguntas con trampa típicas de UNIR y cómo responderlas)\n"
        )

        guia_md = self.tutor.client.generar_contenido(prompt_guia, temperatura=0.3)
        if not guia_md:
            console.print("[yellow]⚠ Reintentando redacción de guía con versión concisa...[/yellow]")
            prompt_guia_conciso = (
                f"Eres NOVA, tutor de IA de FP DAM en UNIR.\n"
                f"A partir del siguiente material oficial de '{asignatura}' ({nombre_tema}):\n\n"
                f"{contexto_combinado[:15000]}\n\n"
                "Redacta una GUÍA DE ESTUDIO COMPLETA en Markdown con:\n"
                "# 📘 GUÍA DE ESTUDIO: " + nombre_tema.upper() + "\n"
                "## 📌 1. Resumen Ejecutivo\n"
                "## 📢 2. Avisos de Examen y Trampas\n"
                "## 🧠 3. Conceptos Clave (Tabla)\n"
                "## 💻 4. Código Ejemplo\n"
                "## ⚠️ 5. Puntos Críticos y Preguntas Trampa\n"
            )
            guia_md = self.tutor.client.generar_contenido(prompt_guia_conciso, temperatura=0.3)

        if guia_md:
            dir_asig_guia = GUIAS_DIR / asignatura
            dir_asig_guia.mkdir(parents=True, exist_ok=True)
            ruta_guia = dir_asig_guia / f"{slug_tema}_guia.md"
            with open(ruta_guia, "w", encoding="utf-8") as f:
                f.write(guia_md)
            resultados["guia"] = ruta_guia
            console.print(f"[bold green]✔ Guía guardada en:[/bold green] {ruta_guia}")
        else:
            console.print(f"[bold red]❌ No se pudo redactar la guía de estudio para '{nombre_tema}'[/bold red]")

        # 4. Generar Banco de Preguntas Tipo Test (JSON + MD)
        console.print("\n[cyan]4/4. Diseñando Simulacro Tipo Test Oficial de UNIR (10 preguntas)...[/cyan]")
        prompt_test = (
            f"Genera un examen tipo test de 10 preguntas basado en este tema de '{asignatura}' ({nombre_tema}):\n\n"
            f"{contexto_combinado[:25000]}\n\n"
            "IMPORTANTE: Devuelve ÚNICAMENTE un bloque JSON válido con el siguiente formato, sin texto antes ni después:\n"
            "[\n"
            "  {\n"
            '    "enunciado": "Texto de la pregunta...",\n'
            '    "opciones": {\n'
            '      "A": "Opción A",\n'
            '      "B": "Opción B",\n'
            '      "C": "Opción C",\n'
            '      "D": "Opción D"\n'
            "    },\n"
            '    "correcta": "A",\n'
            '    "pista": "Pista conceptual breve que oriente al estudiante sin desvelar la letra de la respuesta...",\n'
            '    "justificacion": "Explicación técnica detallada de por qué es la correcta y por qué las demás fallan..."\n'
            "  }\n"
            "]\n"
        )

        test_json_str = self.tutor.client.generar_contenido(prompt_test, temperatura=0.2)
        if test_json_str:
            # Extraer bloque JSON
            match = re.search(r"\[\s*\{.*\}\s*\]", test_json_str, re.DOTALL)
            if match:
                test_json_str = match.group(0)

            try:
                preguntas_data = json.loads(test_json_str)
                preguntas_obj = [
                    PreguntaExamen(
                        enunciado=p["enunciado"],
                        opciones=p["opciones"],
                        correcta=p["correcta"],
                        justificacion=p["justificacion"],
                        pista=p.get("pista", ""),
                    )
                    for p in preguntas_data
                ]

                dir_examenes = EXAMENES_DIR / asignatura
                dir_examenes.mkdir(parents=True, exist_ok=True)

                # Guardar JSON para el test interactivo
                ruta_test_json = dir_examenes / f"{slug_tema}_test.json"
                with open(ruta_test_json, "w", encoding="utf-8") as f:
                    json.dump(preguntas_data, f, indent=2, ensure_ascii=False)
                resultados["test_json"] = ruta_test_json

                # Guardar Markdown con respuestas ocultas
                md_examen = generar_markdown_examen(
                    asignatura=asignatura,
                    tema=nombre_tema,
                    titulo=f"Test Oficial UNIR — {nombre_tema}",
                    preguntas=preguntas_obj,
                )
                ruta_test_md = dir_examenes / f"{slug_tema}_test.md"
                with open(ruta_test_md, "w", encoding="utf-8") as f:
                    f.write(md_examen)
                resultados["test_md"] = ruta_test_md

                console.print(f"[bold green]✔ Test interactivo y examen guardados en:[/bold green] {ruta_test_json}")

            except Exception as e:
                console.print(f"[yellow]⚠ Error procesando JSON de test: {e}[/yellow]")

        console.print(f"\n[bold green]✨ ¡Guía de estudio y examen de '{nombre_tema}' completados con éxito![/bold green]\n")
        return resultados
