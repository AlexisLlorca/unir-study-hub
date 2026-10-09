"""
Factoría de Contenidos Académicos con Inteligencia Artificial.
Orquesta el análisis de PDFs de UNIR, extracción de código con Code Sniffer,
y generación de resúmenes, ejercicios prácticos y simulacros de examen.
"""

import sys
from pathlib import Path
from typing import Optional, List, Dict, Any

# Importación modular
BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from rich.panel import Panel
from rich.table import Table

from src.core.config import (
    console,
    APUNTES_DIR,
    EJERCICIOS_DIR,
    EXAMENES_DIR,
    CODIGO_DIR,
    ensure_directories,
)
from src.extractors.pdf_parser import extraer_texto_pdf
from src.extractors.code_sniffer import guardar_codigo_extraido, detectar_lenguaje
from src.ai.tutor import TutorIA


class FactoriaAcademicaIA:
    """Procesa temarios y genera todos los recursos de estudio derivados."""

    def __init__(self, tutor_ia: Optional[TutorIA] = None):
        self.tutor = tutor_ia or TutorIA()
        ensure_directories()

    def procesar_tema_completo(
        self,
        ruta_pdf: Path,
        asignatura: str,
        nombre_tema: str,
    ) -> Dict[str, Any]:
        """
        Ejecuta el ciclo de vida completo de un tema:
        1. Parsing del PDF oficial.
        2. Extracción de bloques de código vivo (.java, .kt, .sql).
        3. Generación con IA de Apuntes y Resumen Ejecutivo.
        4. Generación con IA de Banco de Ejercicios Prácticos.
        5. Generación con IA de Simulacro de Examen Tipo Test UNIR.
        """
        console.print(
            Panel.fit(
                f"[bold cyan]⚡ PROCESANDO TEMA CON INTELIGENCIA ARTIFICIAL[/bold cyan]\n"
                f"[white]Asignatura:[/white] [bold green]{asignatura}[/bold green] | [white]Tema:[/white] [bold yellow]{nombre_tema}[/bold yellow]\n"
                f"[dim]Archivo fuente: {ruta_pdf.name}[/dim]",
                border_style="cyan",
            )
        )

        resultados = {
            "pdf": ruta_pdf,
            "asignatura": asignatura,
            "tema": nombre_tema,
            "apuntes_md": None,
            "ejercicios_md": None,
            "examen_md": None,
            "codigo_extraido": [],
        }

        # 1. Extracción de texto del PDF
        console.print("[cyan]1/4. Extrayendo texto y estructura del PDF...[/cyan]")
        datos_pdf = extraer_texto_pdf(ruta_pdf)
        texto_completo = datos_pdf.get("texto_completo", "")
        if not texto_completo:
            console.print("[bold red]❌ No se pudo extraer texto del PDF (posiblemente sea un escaneo de imagen).[/bold red]")
            return resultados

        console.print(f"[green]✔ Extraídas {datos_pdf['total_paginas']} páginas ({len(texto_completo)} caracteres).[/green]")

        # 2. Resumen y Apuntes de Alto Rendimiento
        console.print("\n[cyan]2/4. Generando Apuntes y Resumen Ejecutivo con IA...[/cyan]")
        resumen_md = self.tutor.generar_resumen_avanzado(texto_completo, asignatura, nombre_tema)
        if resumen_md:
            dir_apuntes = APUNTES_DIR / asignatura
            dir_apuntes.mkdir(parents=True, exist_ok=True)
            ruta_apuntes = dir_apuntes / f"{nombre_tema}.md"
            with open(ruta_apuntes, "w", encoding="utf-8") as f:
                f.write(resumen_md)
            resultados["apuntes_md"] = ruta_apuntes
            console.print(f"[bold green]✔ Apuntes generados:[/bold green] {ruta_apuntes}")

        # 3. Banco de Ejercicios Prácticos
        console.print("\n[cyan]3/4. Diseñando Ejercicios Prácticos y Soluciones paso a paso con IA...[/cyan]")
        ejercicios_md = self.tutor.generar_ejercicios_practicos(texto_completo, asignatura, nombre_tema)
        if ejercicios_md:
            dir_ejercicios = EJERCICIOS_DIR / asignatura
            dir_ejercicios.mkdir(parents=True, exist_ok=True)
            ruta_ejercicios = dir_ejercicios / f"{nombre_tema}_ejercicios.md"
            with open(ruta_ejercicios, "w", encoding="utf-8") as f:
                f.write(f"# 🛠️ EJERCICIOS PRÁCTICOS — {asignatura.upper()} ({nombre_tema.upper()})\n\n")
                f.write(ejercicios_md)
            resultados["ejercicios_md"] = ruta_ejercicios
            console.print(f"[bold green]✔ Ejercicios generados:[/bold green] {ruta_ejercicios}")

        # 4. Simulacro de Examen Tipo Test UNIR
        console.print("\n[cyan]4/4. Creando Simulacro de Examen Tipo Test UNIR (con respuestas ocultas)...[/cyan]")
        examen_md = self.tutor.generar_examen_unir(texto_completo, asignatura, nombre_tema, num_preguntas=5)
        if examen_md:
            dir_examenes = EXAMENES_DIR / asignatura
            dir_examenes.mkdir(parents=True, exist_ok=True)
            ruta_examen = dir_examenes / f"{nombre_tema}_examen.md"
            with open(ruta_examen, "w", encoding="utf-8") as f:
                f.write(examen_md)
            resultados["examen_md"] = ruta_examen
            console.print(f"[bold green]✔ Examen generado:[/bold green] {ruta_examen}")

        console.print("\n[bold green]✨ ¡Tema procesado por completo y material generado con éxito![/bold green]\n")
        return resultados
