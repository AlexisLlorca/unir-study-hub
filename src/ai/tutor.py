"""
Tutor Interactivo de Inteligencia Artificial para 2º DAM & PFU (UNIR).
Permite mantener conversaciones en directo, resolver dudas técnicas de código,
diseñar ejercicios prácticos y evaluar al alumno en cualquier tema.
"""

import sys
from pathlib import Path
from typing import List, Dict, Optional

# Importación modular
BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from rich.panel import Panel
from rich.markdown import Markdown
from rich.table import Table

from src.core.config import console
from src.ai.llm_client import LLMClient


class TutorIA:
    """Orquesta las interacciones pedagógicas con el alumno."""

    def __init__(self, llm_client: Optional[LLMClient] = None):
        self.client = llm_client or LLMClient()
        self.historial: List[Dict[str, str]] = []
        self.asignatura_actual: Optional[str] = None
        self.contexto_tema: Optional[str] = None

    def configurar_contexto(self, asignatura: str, contenido_tema: Optional[str] = None):
        """Asigna la asignatura o tema activo para las preguntas del alumno."""
        self.asignatura_actual = asignatura
        self.contexto_tema = contenido_tema

    def sesion_chat_interactivo(self):
        """Inicia un bucle de conversación continuo en la terminal."""
        if not self.client.esta_configurado():
            if not self.client.solicitar_api_key_interactiva():
                console.print(
                    "[yellow]⚠ No se ha configurado la clave de Gemini. Puedes añadirla en el archivo .env o en el menú.[/yellow]\n"
                )
                return

        console.print(
            Panel.fit(
                "[bold cyan]🤖 TUTOR ACADÉMICO DE IA — UNIR Study Hub[/bold cyan]\n"
                "[bold white]Pregúntame cualquier duda sobre 2º DAM o PFU Ingeniería del Software.[/bold white]\n\n"
                "[dim]Comandos especiales:[/dim]\n"
                "  [cyan]/test[/cyan]      → Pídeme un examen tipo test al instante\n"
                "  [cyan]/ejercicio[/cyan] → Pídeme un ejercicio práctico con código\n"
                "  [cyan]/limpiar[/cyan]   → Reiniciar conversación\n"
                "  [cyan]/salir[/cyan]     → Volver al menú principal\n",
                border_style="cyan",
            )
        )

        while True:
            try:
                entrada = console.input("[bold green]Tú 👉 [/bold green]").strip()
            except (KeyboardInterrupt, EOFError):
                console.print("\n[dim]Sesión de tutor finalizada.[/dim]")
                break

            if not entrada:
                continue

            if entrada.lower() in ["/salir", "salir", "exit", "quit"]:
                console.print("[dim]Volviendo al menú principal...[/dim]\n")
                break

            if entrada.lower() in ["/limpiar", "clear"]:
                self.historial.clear()
                console.print("[green]✔ Historial de conversación reiniciado.[/green]\n")
                continue

            # Construir prompt con contexto si existe
            prompt_completo = ""
            if self.asignatura_actual:
                prompt_completo += f"[Asignatura: {self.asignatura_actual}]\n"
            if self.contexto_tema:
                prompt_completo += f"[Contexto del tema en estudio:\n{self.contexto_tema[:2000]}...]\n"

            # Historial reciente
            if self.historial:
                prompt_completo += "\n[Historial reciente]:\n"
                for turno in self.historial[-4:]:
                    prompt_completo += f"{turno['rol']}: {turno['texto']}\n"

            prompt_completo += f"\nAlumno: {entrada}\nNOVA (Tutor):"

            console.print("\n[bold cyan]NOVA 🤖:[/bold cyan]")
            respuesta_acumulada = ""

            try:
                for chunk in self.client.generar_stream(prompt_completo):
                    sys.stdout.write(chunk)
                    sys.stdout.flush()
                    respuesta_acumulada += chunk
                print("\n")
            except Exception as e:
                console.print(f"[red]Error recibiendo respuesta:[/red] {e}")

            if respuesta_acumulada:
                self.historial.append({"rol": "Alumno", "texto": entrada})
                self.historial.append({"rol": "NOVA", "texto": respuesta_acumulada})

    def generar_resumen_avanzado(self, texto_tema: str, asignatura: str, tema: str) -> Optional[str]:
        """Solicita a la IA un resumen estructurado de alto rendimiento."""
        prompt = (
            f"Analiza el siguiente texto extraído del temario oficial de UNIR "
            f"para la asignatura '{asignatura}' ({tema}):\n\n"
            f"--- COMIENZO TEMARIO ---\n{texto_tema[:30000]}\n--- FIN TEMARIO ---\n\n"
            "Genera un documento Markdown exhaustivo con la siguiente estructura:\n"
            "1. # TÍTULO DEL TEMA Y ASIGNATURA\n"
            "2. ## 📌 1. Resumen Ejecutivo (los 5 puntos más importantes del tema sin rodeos)\n"
            "3. ## 🧠 2. Conceptos Clave y Definiciones de Examen (tabla Markdown con Término, Definición Técnica y Relevancia en Examen)\n"
            "4. ## 💻 3. Código Vivo y Ejemplos de Implementación (código completo, compilable y con comentarios explicativos)\n"
            "5. ## ⚠️ 4. Trampas y Errores Habituales de Examen (preguntas capciosas típicas que suele poner UNIR)\n"
        )
        return self.client.generar_contenido(prompt, temperatura=0.3)

    def generar_ejercicios_practicos(self, texto_tema: str, asignatura: str, tema: str) -> Optional[str]:
        """Genera un banco de ejercicios con soluciones paso a paso."""
        prompt = (
            f"A partir del siguiente temario de '{asignatura}' ({tema}) de UNIR:\n\n"
            f"{texto_tema[:30000]}\n\n"
            "Diseña 3 ejercicios prácticos de desarrollo de software acordes al nivel de 2º DAM.\n"
            "Para cada ejercicio incluye:\n"
            "- Enunciado claro del problema real a resolver.\n"
            "- Requisitos técnicos (clases, métodos, librerías requeridas).\n"
            "- Pistas para pensar antes de ver la solución.\n"
            "- Código de solución completo y explicado línea por línea.\n"
        )
        return self.client.generar_contenido(prompt, temperatura=0.4)

    def generar_examen_unir(self, texto_tema: str, asignatura: str, tema: str, num_preguntas: int = 5) -> Optional[str]:
        """Genera un simulacro de examen tipo test UNIR con justificaciones ocultas."""
        prompt = (
            f"Basándote en el siguiente temario de '{asignatura}' ({tema}) de UNIR:\n\n"
            f"{texto_tema[:30000]}\n\n"
            f"Genera un examen tipo test oficial de {num_preguntas} preguntas con el formato de UNIR:\n"
            "- 4 opciones por pregunta (A, B, C, D).\n"
            "- Solo 1 opción correcta.\n"
            "- Incluye la solución y la justificación técnica detallada dentro de una etiqueta HTML <details><summary>Ver respuesta</summary>...</details>.\n"
            "- Distribuye preguntas teóricas de concepto y preguntas prácticas con fragmentos de código donde haya que deducir la salida o el error.\n"
        )
        return self.client.generar_contenido(prompt, temperatura=0.3)

    def generar_resumen_clase_magistral(self, texto_transcripcion: str, asignatura: str, nombre_clase: str) -> Optional[str]:
        """Analiza la transcripción de una clase en directo y extrae avisos de examen, explicaciones de código y dudas de alumnos."""
        prompt = (
            f"Eres NOVA, copiloto y mentora académica de élite para un alumno de FP DAM y PFU en UNIR.\n"
            f"A continuación tienes la transcripción oficial y literal de una clase en directo de la asignatura '{asignatura}' ({nombre_clase}):\n\n"
            f"--- TRANSCRIPCIÓN LITERAL DE LA CLASE ---\n{texto_transcripcion[:60000]}\n--- FIN TRANSCRIPCIÓN ---\n\n"
            "Genera un INFORME DE ESTUDIO EXHAUSTIVO Y PROFESIONAL estructurado en Markdown limpio:\n\n"
            f"# 🎙️ INFORME DE CLASE MAGISTRAL: {nombre_clase}\n"
            f"> **Asignatura:** {asignatura} | **Analizado por:** NOVA Academic Engine\n\n"
            "## 📢 1. Avisos del Profesor para el Examen y Actividades (Prioridad Máxima)\n"
            "- ¿Qué temas, trampas o ejercicios dijo explícitamente el profesor que entrarían en examen?\n"
            "- Fechas de entregas de PACs, tareas, recomendaciones y advertencias metodológicas del docente.\n\n"
            "## 🧠 2. Conceptos Técnicos y Teoría Explicada\n"
            "- Resumen detallado paso a paso de los conceptos tratados en la sesión, sin omitir terminología técnica.\n\n"
            "## 💻 3. Código y Arquitectura Desarrollada en Directo\n"
            "- Explica detalladamente el proyecto, paquetes, clases, métodos o configuraciones que programó el profesor en directo.\n"
            "- Incluye fragmentos de código explicativos de la lógica implementada (ej. controladores, flujos I/O, etc.).\n\n"
            "## ⏱️ 4. Minutero y Momentos Clave de la Sesión\n"
            "- Crea una tabla Markdown con las marcas de tiempo [MM:SS] o [HH:MM:SS] más relevantes de la clase, el tema tratado y su importancia para el estudio.\n\n"
            "## 🙋‍♂️ 5. Preguntas Clave de los Alumnos y Respuestas del Docente\n"
            "- Resume las dudas reales planteadas por los estudiantes durante la clase y la aclaración que dio el profesor.\n\n"
            "## 🎯 6. 3 Preguntas de Examen sobre esta Sesión\n"
            "- Formato tipo test oficial UNIR (A, B, C, D) con la respuesta correcta y la justificación técnica oculta dentro de una etiqueta HTML `<details><summary>Ver solución y justificación</summary>...</details>`.\n"
        )
        return self.client.generar_contenido(prompt, temperatura=0.3)

