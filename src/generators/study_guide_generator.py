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
        guias_dir: Optional[Path] = None,
        examenes_dir: Optional[Path] = None,
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
        generar_test = tipo_generacion in ("ambos", "solo_test", "verdadero_falso")
        es_vf = tipo_generacion == "verdadero_falso"

        if es_vf:
            num_preguntas = 10
        elif not num_preguntas or num_preguntas <= 0:
            if profundidad == "corta":
                num_preguntas = 5
            elif profundidad == "robusta":
                num_preguntas = 20
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
                "\n\nDIRECTRICES OBLIGATORIAS DE ESTILO Y FORMATO IDÉNTICO AL SISTEMA ACADÉMICO OFICIAL:\n"
                "- CERO EMOJIS: Prohibido estrictamente utilizar emoticonos o iconos (nada de ⚡, 🚀, 📌, 💡, 💻, etc.) en títulos ni texto.\n"
                "- TÍTULO PRINCIPAL (H1): Encabezado conceptual, limpio y académico (ejemplo: '# Naturaleza del software y sus retos'). Sin 'Tema X', sin 'Guía' ni adornos.\n"
                "- ESTRUCTURA TEMÁTICA (H2 y H3): Divide el tema en 4 a 6 secciones conceptuales sólidas ('## ...') y subsecciones ('### ...') cuando se detallen modelos, variantes o fases específicas.\n"
                "- TONO Y REDACCIÓN: Prosa académica universitaria, rigurosa, didáctica y formal en castellano impecable. Desarrolla párrafos claros y articulados que expliquen las causas, consecuencias, funcionamiento y distinciones técnicas.\n"
                "- CONCEPTOS EN NEGRITA: Enfatiza en **negrita** los términos técnicos clave, nombres de estándares, modelos y metodologías para favorecer la lectura activa.\n"
                "- LISTAS Y CLASIFICACIONES: Cuando se presenten listas o dimensiones, usa viñetas con el concepto en negrita al inicio (* **Concepto:** Explicación...). Anida sub-viñetas cuando haya clasificaciones secundarias (como factores externos vs internos).\n"
                "- SÍNTESIS FINAL OBLIGATORIA (H2): Concluye siempre con una sección '## Síntesis esencial' (o '## Síntesis de los aspectos clave' / '## Ideas esenciales') con 2-3 párrafos profundos o 4-6 viñetas que condensen las conclusiones fundamentales de todo el tema.\n"
                "- PIE DE PÁGINA: Termina el documento con:\n---\n*Resumen generado con IA*\n"
                "- PROHIBIDO INVENTAR: Basa el contenido rigurosamente en el material aportado sin añadir conceptos ajenos ni descontextualizados.\n"
            )

            prompt_guia = (
                f"Eres el sistema oficial de síntesis académica universitaria.\n"
                f"A partir del siguiente material oficial de '{asignatura}' (Tema: {nombre_tema}):\n\n"
                f"{contexto_combinado}\n\n"
                f"{directivas_veracidad}\n\n"
                "Redacta el RESUMEN OFICIAL COMPLETO CON INTELIGENCIA ARTIFICIAL del tema, siguiendo exactamente la estructura, tono formal y elegancia de los resúmenes académicos oficiales en Markdown."
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
                    f"Eres el sistema oficial de síntesis académica universitaria.\n"
                    f"A partir del siguiente material oficial de '{asignatura}' ({nombre_tema}):\n\n"
                    f"{contexto_combinado[:40000]}\n\n"
                    f"{directivas_veracidad}\n\n"
                    "Redacta el RESUMEN OFICIAL CON INTELIGENCIA ARTIFICIAL del tema, con sus secciones temáticas conceptuales y su Síntesis esencial final en Markdown."
                )
                guia_md = self.tutor.client.generar_contenido(
                    prompt_guia_conciso,
                    temperatura=0.3,
                    max_output_tokens=8192,
                    api_key_override=api_key_override
                )

            if guia_md:
                dir_asig_guia = (guias_dir or GUIAS_DIR) / asignatura
                dir_asig_guia.mkdir(parents=True, exist_ok=True)
                ruta_guia_base = dir_asig_guia / f"{slug_tema}_guia.md"
                with open(ruta_guia_base, "w", encoding="utf-8") as f:
                    f.write(guia_md)
                resultados["guia"] = ruta_guia_base
                console.print(f"[bold green]✔ Guía guardada en:[/bold green] {ruta_guia_base}")
            else:
                console.print(f"[bold red]❌ No se pudo redactar la guía de estudio para '{nombre_tema}'[/bold red]")
        else:
            console.print("[dim]3/4. Redacción de Guía omitida a petición del usuario.[/dim]")

        # 4. Generar Banco de Preguntas Tipo Test (JSON + MD)
        if generar_test:
            if es_vf:
                console.print(f"\n[cyan]4/4. Diseñando Autoevaluación de Verdadero o Falso con IA ({num_preguntas} afirmaciones)...[/cyan]")
                prompt_test = (
                    f"Genera una actividad oficial de autoevaluación de VERDADERO O FALSO de exactamente {num_preguntas} preguntas con rigor pedagógico y técnico, "
                    f"basada en el tema '{nombre_tema}' de '{asignatura}'.\n\n"
                    f"{contexto_combinado[:35000]}\n\n"
                    f"REGLAS OBLIGATORIAS:\n"
                    f"1. Genera exactamente {num_preguntas} afirmaciones conceptuales basadas estrictamente en el contenido oficial.\n"
                    "2. Debe haber un equilibrio aproximado entre afirmaciones Verdaderas y Falsas (aproximadamente 5 verdaderas y 5 falsas).\n"
                    "3. Cada elemento debe tener exactamente 2 opciones: {\"A\": \"Verdadero\", \"B\": \"Falso\"}.\n"
                    "4. 'correcta': 'A' si la afirmación es Verdadera, o 'B' si es Falsa.\n"
                    "5. 'pista': Pista socrática y orientativa que guíe al alumno sin desvelar la respuesta.\n"
                    "6. 'justificacion': Justificación exhaustiva explicando por qué la afirmación es Verdadera o Falsa según el temario oficial.\n"
                    "7. FIDELIDAD ESTRICTA AL TEMARIO (PROHIBIDO INVENTAR): Todo debe basarse exclusivamente en el contenido oficial.\n"
                    "8. FORMATO VISUAL DEL CÓDIGO: Si incluye código, formátalo con saltos de línea e indentación adecuada dentro de bloques de código Markdown.\n"
                    "9. Devuelve ÚNICAMENTE un bloque JSON válido con el siguiente formato, sin texto antes ni después:\n"
                    "[\n"
                    "  {\n"
                    '    "enunciado": "Afirmación técnica sobre el tema...",\n'
                    '    "opciones": {\n'
                    '      "A": "Verdadero",\n'
                    '      "B": "Falso"\n'
                    "    },\n"
                    '    "correcta": "A",\n'
                    '    "pista": "Pista orientativa...",\n'
                    '    "justificacion": "Explicación técnica detallada de por qué es Verdadera o Falsa..."\n'
                    "  }\n"
                    "]\n"
                )
            else:
                console.print(f"\n[cyan]4/4. Diseñando Simulacro Tipo Test Oficial ({num_preguntas} preguntas, nivel {profundidad.upper()})...[/cyan]")
                prompt_test = (
                    f"Genera un examen tipo test oficial de {num_preguntas} preguntas con rigor pedagógico y técnico, "
                    f"basado en el tema '{nombre_tema}' de '{asignatura}'. Nivel de profundidad: {profundidad.upper()}.\n\n"
                    f"{contexto_combinado[:35000]}\n\n"
                    f"REGLAS OBLIGATORIAS:\n"
                    f"1. Genera exactamente {num_preguntas} preguntas.\n"
                    "2. Incluye preguntas conceptuales rigurosas, análisis de fragmentos de código y trampas de examen oficiales.\n"
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
                max_output_tokens=16384,
                response_mime_type="application/json",
                api_key_override=api_key_override,
            )
            if not test_json_str:
                console.print("[yellow]⚠ Reintentando generación de test con contexto sintetizado...[/yellow]")
                prompt_test_reintento = prompt_test.replace(contexto_combinado[:35000], contexto_combinado[:20000])
                test_json_str = self.tutor.client.generar_contenido(
                    prompt_test_reintento,
                    temperatura=0.2,
                    max_output_tokens=16384,
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
                    for p in preguntas_data:
                        if isinstance(p, dict) and isinstance(p.get("opciones"), dict):
                            ops = p["opciones"]
                            for campo in ("correcta", "pista", "justificacion", "explicacion"):
                                for k in list(ops.keys()):
                                    if k.lower() == campo:
                                        val = ops.pop(k)
                                        if not p.get(campo):
                                            p[campo] = val
                            limpias = {}
                            for k, v in ops.items():
                                k_str = str(k).strip().upper()
                                if len(k_str) == 1 and k_str in "ABCDE":
                                    limpias[k_str] = v
                            p["opciones"] = limpias

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

                        dir_examenes = (examenes_dir or EXAMENES_DIR) / asignatura
                        dir_examenes.mkdir(parents=True, exist_ok=True)

                        # Guardar JSON para el test interactivo
                        sufijo_test = "_vf_test" if es_vf else "_test"
                        ruta_test_json = dir_examenes / f"{slug_tema}{sufijo_test}.json"
                        with open(ruta_test_json, "w", encoding="utf-8") as f:
                            json.dump(preguntas_data, f, indent=2, ensure_ascii=False)
                        resultados["test_json"] = ruta_test_json

                        # Guardar Markdown con respuestas ocultas
                        titulo_examen = f"Autoevaluación Verdadero o Falso — {nombre_tema}" if es_vf else f"Test Oficial — {nombre_tema}"
                        md_examen = generar_markdown_examen(
                            asignatura=asignatura,
                            tema=nombre_tema,
                            titulo=titulo_examen,
                            preguntas=preguntas_obj,
                        )
                        ruta_test_md = dir_examenes / f"{slug_tema}{sufijo_test}.md"
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
