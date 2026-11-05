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
        profundidad: str = "robusta",
        num_preguntas: Optional[int] = None,
        tipo_generacion: str = "ambos",
        api_key_override: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Genera:
        1. Guía de estudio y apuntes estructurados (.md) con profundidad configurable ('corta', 'media', 'robusta')
        2. Test interactivo (.json) y simulacro (.md) con número de preguntas configurable
        3. Permite elegir 'ambos', 'solo_guia' o 'solo_test'
        """
        slug_tema = re.sub(r'[\\/*?:"<>|]', "", nombre_tema).replace(" ", "_")
        profundidad = (profundidad or "robusta").lower().strip()
        if profundidad not in ("corta", "media", "robusta"):
            profundidad = "robusta"

        tipo_generacion = (tipo_generacion or "ambos").lower().strip()
        generar_guia = tipo_generacion in ("ambos", "solo_guia")
        generar_test = tipo_generacion in ("ambos", "solo_test")

        if not num_preguntas or num_preguntas <= 0:
            if profundidad == "corta":
                num_preguntas = 5
            elif profundidad == "robusta":
                num_preguntas = 15
            else:
                num_preguntas = 10
        else:
            num_preguntas = min(max(int(num_preguntas), 3), 25)

        etiquetas_profundidad = {
            "corta": "🟢 CORTA (Repaso Exprés)",
            "media": "🔵 MEDIA (Equilibrada)",
            "robusta": "🟣 ROBUSTA (Alto Rendimiento / Nivel Pro)",
        }

        console.print(
            Panel.fit(
                f"[bold cyan]📘 GENERANDO GUÍA DE ESTUDIO Y EXAMEN CON IA[/bold cyan]\n"
                f"[white]Asignatura:[/white] [bold green]{asignatura}[/bold green]\n"
                f"[white]Tema:[/white] [bold yellow]{nombre_tema}[/bold yellow]\n"
                f"[white]Profundidad:[/white] [bold magenta]{etiquetas_profundidad.get(profundidad, profundidad)}[/bold magenta]\n"
                f"[white]Preguntas Test:[/white] [bold cyan]{num_preguntas}[/bold cyan]",
                border_style="cyan",
            )
        )

        # 1. Extraer texto del PDF si existe
        texto_pdf = ""
        if ruta_pdf and ruta_pdf.exists():
            console.print(f"[cyan]1/4. Extrayendo contenido del temario oficial:[/cyan] {ruta_pdf.name}...")
            datos_pdf = extraer_texto_pdf(ruta_pdf)
            texto_pdf = datos_pdf.get("texto_completo", "")

        # 2. Extraer texto de clase grabada si existe y corresponde realmente al tema
        texto_clase = ""
        if ruta_transcripcion and ruta_transcripcion.exists():
            console.print(f"[cyan]2/4. Vinculando lección magistral explicada por el profesor:[/cyan] {ruta_transcripcion.name}...")
            with open(ruta_transcripcion, "r", encoding="utf-8") as f:
                texto_clase = f.read()
        else:
            # Buscar únicamente si alguna transcripción coincide realmente con el tema solicitado
            dir_trans = TRANSCRIPCIONES_DIR / asignatura
            if dir_trans.exists():
                num_match = re.search(r"(?:tema|unidad|t|u|bloque|m[oó]dulo|parte|pr[aá]ctica)[\s_\-]*0*(\d+)\b", nombre_tema, re.IGNORECASE)
                if not num_match:
                    num_match = re.search(r"^\s*0*(\d+)\b", nombre_tema)
                palabras = [p for p in re.findall(r"[a-záéíóúñ]{4,}", nombre_tema.lower()) if p not in {"tema", "unidad", "bloque", "modulo", "parte", "introduccion"}]
                for t_path in sorted(dir_trans.glob("*.md")):
                    if t_path.name.endswith("_resumen_ia.md"):
                        continue
                    stem_sin_fecha = re.sub(r"\b20\d{2}[-_]?\d{2}[-_]?\d{2}\b", "", t_path.stem.lower())
                    if num_match:
                        num = int(num_match.group(1))
                        if re.search(rf"(?:tema|unidad|t|u|bloque|m[oó]dulo|parte|sesi[oó]n|clase)[\s_\-]*0*{num}\b", stem_sin_fecha):
                            with open(t_path, "r", encoding="utf-8") as f:
                                texto_clase = f.read()
                            break
                    if palabras and sum(1 for p in palabras if p in stem_sin_fecha) >= 2:
                        with open(t_path, "r", encoding="utf-8") as f:
                            texto_clase = f.read()
                        break

        contexto_combinado = ""
        if texto_pdf:
            contexto_combinado += f"=== TEMARIO OFICIAL DEL TEMA ===\n{texto_pdf[:160000]}\n\n"
        if texto_clase:
            contexto_combinado += f"=== LO QUE EXPLICÓ EL PROFESOR EN CLASE MAGISTRAL ===\n{texto_clase[:60000]}\n\n"

        if not contexto_combinado:
            console.print("[red]❌ No se encontró texto de temario ni de transcripción para este tema.[/red]")
            return {}

        resultados = {
            "profundidad": profundidad,
            "num_preguntas": num_preguntas,
        }

        # 3. Generar Guía de Estudio según la profundidad seleccionada
        if generar_guia:
            console.print(f"\n[cyan]3/4. Redactando Guía de Estudio de Alto Rendimiento ({profundidad.upper()})...[/cyan]")

            directivas_veracidad = (
                "\n\nDIRECTRICES OBLIGATORIAS DE MÁXIMA FIDELIDAD AL MATERIAL OFICIAL (PROHIBIDO INVENTAR):\n"
                "- Basa todo el contenido EXCLUSIVAMENTE en el temario oficial y las transcripciones de clase aportadas arriba.\n"
                "- PROHIBIDO INVENTAR: No introduzcas librerías ajenas a UNIR, conceptos no tratados en el tema ni declaraciones o fechas ficticias del profesor.\n"
                "- Código adaptado al nivel de clase (2º DAM): claro, limpio, compilable, usando 'System.out.println()' estándar para ejemplos sin sobreingeniería de Logger.\n"
                "- Si en la transcripción no hay menciones explícitas a avisos de examen, enfoca los avisos en las dificultades técnicas reales del temario oficial sin inventar citas docentes falsas.\n"
                "- ESTUDIO ACTIVO (ACTIVE RECALL): Incluye cajas de autoevaluación interactiva utilizando etiquetas HTML nativas <details><summary>💡 Ver respuesta y razonamiento técnico</summary>...</details> para que el estudiante pueda ponerse a prueba antes de ver la solución.\n"
            )

            if profundidad == "corta":
                prompt_guia = (
                    f"Eres NOVA, mentora de alto rendimiento y tutora de FP DAM en UNIR.\n"
                    f"Basándote en el siguiente material oficial de '{asignatura}' (Tema: {nombre_tema}):\n\n"
                    f"{contexto_combinado}\n\n"
                    f"{directivas_veracidad}\n\n"
                    "Genera un REPASO EXPRÉS de alta densidad conceptual (~1.500 palabras) en Markdown estructurado exactamente así:\n"
                    f"# ⚡ REPASO EXPRÉS: {nombre_tema.upper()} — {asignatura.upper()}\n"
                    "> Guía rápida y condensada para repaso intensivo antes de exámenes y asimilación ágil de conceptos clave.\n\n"
                    "## 🚀 1. Resumen de Alto Impacto (Ideas fuerza y conceptos imprescindibles sin relleno)\n"
                    "## 📑 2. Chuleta Rápida de Examen y Sintaxis (Cheatsheet Markdown con clases, comandos, métodos clave y firmas)\n"
                    "## 💡 3. Conceptos Mínimos Viables de Aprobado (Tabla Markdown con Término, Definición concisa y Clave en Test)\n"
                    "## 💻 4. Micro-ejemplo de Código Práctico (Snippet conciso, limpio, moderno con System.out.println() y comentado)\n"
                    "## ❓ 5. Reto de Active Recall Rápido (Pregunta de autoevaluación con <details><summary>💡 Ver respuesta</summary>...</details>)\n"
                    "## ⚠️ 6. Los 3 Errores Fatales en Examen (Trampas habituales de UNIR y cómo evitarlas)\n"
                )
            elif profundidad == "robusta":
                prompt_guia = (
                    f"Eres NOVA, mentora de élite, consultora senior de software y tutora de FP DAM en UNIR.\n"
                    f"A partir del siguiente material oficial de '{asignatura}' (Tema: {nombre_tema}):\n\n"
                    f"{contexto_combinado}\n\n"
                    f"{directivas_veracidad}\n\n"
                    "Redacta una GUÍA MAESTRA DE ALTO RENDIMIENTO (MÉTODO CORNELL & ACTIVE RECALL / NIVEL PRO) exhaustiva, profunda y de máxima ingeniería (~4.500+ palabras) en Markdown estructurado exactamente así:\n"
                    f"# 🏆 GUÍA MAESTRA DE ALTO RENDIMIENTO (NIVEL PRO): {nombre_tema.upper()} — {asignatura.upper()}\n"
                    "> Dossier técnico exhaustivo con Método Cornell y Active Recall para dominio absoluto de la materia y preparación de examen en UNIR (2º DAM).\n\n"
                    "## 🧭 1. Mapa Conceptual y Arquitectura del Tema\n"
                    "- Resumen ejecutivo de alto nivel y marco arquitectónico.\n"
                    "- Diagrama visual de flujo, ciclo de vida o arquitectura técnica (en formato ASCII estructurado o bloque Mermaid ```mermaid ... ``` explicativo).\n\n"
                    "## 🏛️ 2. Fundamentos Teóricos Profundos y Funcionamiento Interno\n"
                    "- Explicación exhaustiva, rigurosa y sin saltarse ningún epígrafe del PDF oficial de UNIR.\n"
                    "- Análisis de funcionamiento interno a bajo nivel (JVM, hilos, memoria RAM/Stack/Heap, gestión del Sistema Operativo o Sockets de red).\n"
                    "- Cajas de autoevaluación inmediata (Active Recall) usando bloques interactivos:\n"
                    "  <details><summary>💡 Pregunta Active Recall: [Concepto clave del tema]</summary>\n"
                    "  [Explicación concisa y técnica de la respuesta]\n"
                    "  </details>\n\n"
                    "## 📑 3. Chuleta Exhaustiva de Sintaxis, Clases y Firmas de Métodos (Cheatsheet)\n"
                    "- Tabla Markdown completa con todos los métodos y clases indispensables del tema:\n"
                    "  | Clase / Interfaz | Método y Firma | Parámetros | Tipo Retorno | Excepciones Obligatorias (`throws`) | Clave en Examen |\n"
                    "- Reglas nemotécnicas para no confundir métodos parecidos.\n\n"
                    "## 🎙️ 4. Énfasis del Profesor en Videoclase y Dificultades Críticas\n"
                    "- Puntos críticos destacados en clase magistral, dudas frecuentes de alumnos y aclaraciones del docente.\n"
                    "- <details><summary>💡 Pregunta Active Recall de Videoclase: [Duda habitual de alumnos]</summary>[Solución explicada]</details>\n\n"
                    "## 💻 5. Laboratorio de Código Práctico (Implementación Paso a Paso)\n"
                    "- Implementación Java completa, limpia, moderna y 100% compilable adaptada al temario de 2º DAM.\n"
                    "- Sin sobreingeniería ni Logger: uso de `System.out.println()` pedagógico.\n"
                    "- Código ampliamente comentado línea a línea explicando el porqué de cada sentencia.\n"
                    "- Traza de ejecución paso a paso con salida por consola esperada.\n\n"
                    "## 🔄 6. Comparativas Técnicas y Cuándo Usar Cada Solución\n"
                    "- Tabla comparativa entre las diferentes alternativas que ofrece la tecnología explicada (pros, contras, rendimiento y casos de uso).\n\n"
                    "## ⚠️ 7. Desglose de Preguntas Trampa y Distractores de Exámenes UNIR\n"
                    "- Análisis detallado de cómo formula UNIR las preguntas capciosas de este tema.\n"
                    "- Análisis de distractores en preguntas tipo test: por qué una respuesta incorrecta parece correcta a primera vista y cuál es el fallo técnico sutil.\n"
                    "- <details><summary>💡 Simulacro de Pregunta Trampa de Examen</summary>[Análisis del distractor y respuesta correcta fundamentada]</details>\n\n"
                    "## 🧠 8. Glosario Técnico Exhaustivo (Diccionario de Examen)\n"
                    "- Tabla Markdown completa: | Término Técnico | Definición Rigurosa | Trampa Asociada en Test |\n\n"
                    "## 🚀 9. Checklist de Dominio Pre-Examen\n"
                    "- Lista de comprobación interactiva `[ ]` con:\n"
                    "  - [ ] Objetivos mínimos imprescindibles para asegurar el Aprobado (5.0)\n"
                    "  - [ ] Hitos avanzados para aspirar a Matrícula de Honor (9.0 - 10)\n"
                )
            else:  # media
                prompt_guia = (
                    f"Eres NOVA, tutor de IA especializado en FP DAM (Desarrollo de Aplicaciones Multiplataforma) de UNIR.\n"
                    f"A partir del siguiente material oficial de '{asignatura}' (Tema: {nombre_tema}):\n\n"
                    f"{contexto_combinado}\n\n"
                    f"{directivas_veracidad}\n\n"
                    "Redacta una GUÍA DE ESTUDIO OFICIAL EQUILIBRADA Y PRODUCTIVA (~3.000 palabras) con Método Cornell y Active Recall en Markdown estructurado así:\n"
                    f"# 📘 GUÍA DE ESTUDIO OFICIAL: {nombre_tema.upper()} — {asignatura.upper()}\n"
                    "> Material de alto rendimiento sintetizado con temario oficial, clases magistrales de UNIR y herramientas de estudio activo.\n\n"
                    "## 📌 1. Resumen Ejecutivo y Marco Teórico (Fundamentos y arquitectura general del tema)\n"
                    "## 📑 2. Chuleta de Sintaxis y Firmas de Métodos (Tabla Markdown con Clase, Método, Tipo Retorno y Excepciones)\n"
                    "## 📢 3. Avisos y Trampas del Profesor en Clase (Qué enfatizó el docente en videoclase, avisos y errores comunes)\n"
                    "## 🧠 4. Conceptos Clave y Cajas Active Recall (Definiciones de examen + <details><summary>💡 Comprobar comprensión</summary>...</details>)\n"
                    "## 💻 5. Laboratorio de Código Práctico (Código completo, pedagógico, compilable con System.out.println() y comentado)\n"
                    "## ⚠️ 6. Puntos Críticos y Preguntas Capciosas de Examen (Distractores típicos de UNIR y cómo detectarlos)\n"
                    "## 🚀 7. Checklist de Comprobación Pre-Examen (Lista [ ] de conceptos que debes dominar)\n"
                )

            guia_md = self.tutor.client.generar_contenido(
                prompt_guia,
                temperatura=0.3,
                max_output_tokens=8192,
                api_key_override=api_key_override
            )
            if not guia_md:
                console.print("[yellow]⚠ Reintentando redacción de guía con versión concisa...[/yellow]")
                prompt_guia_conciso = (
                    f"Eres NOVA, tutor de IA de FP DAM en UNIR.\n"
                    f"A partir del siguiente material oficial de '{asignatura}' ({nombre_tema}):\n\n"
                    f"{contexto_combinado[:35000]}\n\n"
                    f"{directivas_veracidad}\n\n"
                    "Redacta una GUÍA DE ESTUDIO COMPLETA en Markdown con:\n"
                    f"# 📘 GUÍA DE ESTUDIO: {nombre_tema.upper()}\n"
                    "## 📌 1. Resumen Ejecutivo y Mapa Conceptual\n"
                    "## 📑 2. Chuleta de Métodos y Clases Clave\n"
                    "## 📢 3. Avisos de Examen y Trampas Docentes\n"
                    "## 🧠 4. Conceptos Clave (Tabla con Active Recall)\n"
                    "## 💻 5. Código Ejemplo Comentado\n"
                    "## ⚠️ 6. Puntos Críticos y Preguntas Trampa\n"
                    "## 🚀 7. Checklist Pre-Examen\n"
                )
                guia_md = self.tutor.client.generar_contenido(
                    prompt_guia_conciso,
                    temperatura=0.3,
                    max_output_tokens=8192,
                    api_key_override=api_key_override
                )

            if guia_md:
                dir_asig_guia = GUIAS_DIR / asignatura
                dir_asig_guia.mkdir(parents=True, exist_ok=True)
                ruta_guia_base = dir_asig_guia / f"{slug_tema}_guia.md"
                # B4: Evitar que una guía corta machaque silenciosamente una guía robusta previa
                if ruta_guia_base.exists() and profundidad != "media":
                    ruta_guia = dir_asig_guia / f"{slug_tema}_{profundidad}_guia.md"
                else:
                    ruta_guia = ruta_guia_base
                with open(ruta_guia, "w", encoding="utf-8") as f:
                    f.write(guia_md)
                resultados["guia"] = ruta_guia
                console.print(f"[bold green]✔ Guía guardada en:[/bold green] {ruta_guia}")
            else:
                console.print(f"[bold red]❌ No se pudo redactar la guía de estudio para '{nombre_tema}'[/bold red]")
        else:
            console.print("[dim]3/4. Redacción de Guía omitida a petición del usuario.[/dim]")

        # 4. Generar Banco de Preguntas Tipo Test (JSON + MD)
        if generar_test:
            console.print(f"\n[cyan]4/4. Diseñando Simulacro Tipo Test Oficial de UNIR ({num_preguntas} preguntas, nivel {profundidad.upper()})...[/cyan]")
            prompt_test = (
                f"Genera un examen tipo test oficial de {num_preguntas} preguntas con rigor pedagógico y técnico, "
                f"basado en el tema '{nombre_tema}' de '{asignatura}'. Nivel de profundidad: {profundidad.upper()}.\n\n"
                f"{contexto_combinado[:60000]}\n\n"
                f"REGLAS OBLIGATORIAS:\n"
                f"1. Genera exactamente {num_preguntas} preguntas.\n"
                "2. Incluye preguntas conceptuales rigurosas, análisis de fragmentos de código y trampas de examen de UNIR.\n"
                "3. Cada pregunta debe tener 4 opciones (A, B, C, D) con distractores verosímiles y técnicos.\n"
                "4. 'pista': Pista socrática y orientativa que guíe al alumno sin desvelar la letra de la respuesta.\n"
                "5. 'justificacion': Justificación exhaustiva: por qué la opción correcta lo es Y por qué cada uno de los 3 distractores es incorrecto.\n"
                "6. FIDELIDAD ESTRICTA AL TEMARIO (PROHIBIDO INVENTAR): Todas las preguntas, opciones y códigos deben basarse EXCLUSIVAMENTE en el contenido del temario oficial y las clases proporcionadas arriba. No inventes APIs o sintaxis que no correspondan a 2º DAM.\n"
                "7. FORMATO VISUAL DEL CÓDIGO (CRUCIAL): Si la pregunta incluye un fragmento de código, NUNCA lo comprimas en una sola línea. Escribe el código con saltos de línea e indentación de 2 o 4 espacios dentro del bloque ```java ... ```. Separa claramente el texto introductorio, el bloque de código, y la pregunta final al final en un párrafo aparte.\n"
                "8. Devuelve ÚNICAMENTE un bloque JSON válido con el siguiente formato, sin texto antes ni después:\n"
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

            test_json_str = self.tutor.client.generar_contenido(
                prompt_test,
                temperatura=0.2,
                response_mime_type="application/json",
                api_key_override=api_key_override,
            )
            if test_json_str:
                preguntas_data = []
                limpio = test_json_str.strip()
                limpio = re.sub(r"^```(?:json)?", "", limpio).strip()
                limpio = re.sub(r"```$", "", limpio).strip()
                match = re.search(r"\[\s*\{.*\}\s*\]", limpio, re.DOTALL)
                if match:
                    limpio = match.group(0)

                limpio_sin_comas = re.sub(r",\s*([\]}])", r"\1", limpio)
                try:
                    preguntas_data = json.loads(limpio_sin_comas)
                except Exception as e:
                    console.print(f"[yellow]⚠ JSON en bloque presentó error de sintaxis ({e}). Reparando con extractor tolerante...[/yellow]")
                    # Extracción tolerante pregunta a pregunta
                    candidatos = re.finditer(r"\{\s*\"enunciado\"\s*:\s*\".*?\}\s*(?=,\s*\{|\s*\])", test_json_str, re.DOTALL)
                    for cand in candidatos:
                        try:
                            cand_limpio = re.sub(r",\s*([\]}])", r"\1", cand.group(0))
                            p_obj = json.loads(cand_limpio)
                            if "enunciado" in p_obj and "opciones" in p_obj and "correcta" in p_obj:
                                preguntas_data.append(p_obj)
                        except Exception:
                            continue

                if preguntas_data:
                    try:
                        preguntas_obj = [
                            PreguntaExamen(
                                enunciado=p["enunciado"],
                                opciones=p["opciones"],
                                correcta=p["correcta"],
                                justificacion=p.get("justificacion", ""),
                                pista=p.get("pista", ""),
                            )
                            for p in preguntas_data
                            if "enunciado" in p and "opciones" in p and "correcta" in p
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
        else:
            console.print("[dim]4/4. Generación de Test omitida a petición del usuario.[/dim]")

        console.print(f"\n[bold green]✨ ¡Guía de estudio y examen de '{nombre_tema}' completados con éxito![/bold green]\n")
        return resultados
