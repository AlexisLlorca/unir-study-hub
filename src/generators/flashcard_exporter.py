"""
Exportador de Flashcards para Anki y AnkiDroid (2º DAM UNIR).
Extrae conceptos clave, definiciones oficiales y trampas de examen desde las
guías de estudio y genera archivos TSV listos para importar en Anki con un clic.
"""

import re
from pathlib import Path
from typing import List, Dict, Tuple, Optional

from rich.panel import Panel
from rich.table import Table

from src.core.config import console, GUIAS_DIR, PROCESADO_DIR, ensure_directories

FLASHCARDS_DIR = PROCESADO_DIR / "flashcards"


class ExportadorFlashcards:
    """Extrae tarjetas de memoria mnemotécnicas de las guías de estudio."""

    def __init__(self):
        ensure_directories()
        FLASHCARDS_DIR.mkdir(parents=True, exist_ok=True)

    def exportar_asignatura(self, asignatura: str) -> Optional[Path]:
        """Extrae flashcards de todas las guías de una asignatura."""
        dir_guias = GUIAS_DIR / asignatura
        if not dir_guias.exists():
            console.print(f"[yellow]No hay guías de estudio generadas para {asignatura}.[/yellow]")
            return None

        guias = list(dir_guias.glob("*.md"))
        if not guias:
            console.print(f"[yellow]No se encontraron archivos de guía en {dir_guias}.[/yellow]")
            return None

        tarjetas: List[Tuple[str, str, str]] = []  # (Anverso/Pregunta, Reverso/Respuesta, Etiqueta)

        for guia in guias:
            tema_nombre = guia.stem.replace("_guia", "").replace("_", " ")
            with open(guia, "r", encoding="utf-8") as f:
                contenido = f.read()

            # 1. Extraer filas de la tabla de conceptos clave
            # | **Término** | Definición Técnica | Relevancia |
            filas_tabla = re.findall(r"\|\s*\*\*([^*]+)\*\*\s*\|\s*([^|]+)\s*\|\s*([^|]+)\s*\|", contenido)
            for termino, definicion, relevancia in filas_tabla:
                termino = termino.strip()
                definicion = definicion.strip()
                relevancia = relevancia.strip()
                if termino.lower() in ["término", "concepto", "parametro"]:
                    continue
                anverso = f"¿Qué es y para qué sirve en examen: <b>{termino}</b>? ({asignatura})"
                reverso = f"{definicion}<br><br><small><b>Importancia en Examen:</b> {relevancia}</small>"
                tarjetas.append((anverso, reverso, tema_nombre))

            # 2. Extraer preguntas capciosas y trampas
            # **Pregunta...** \n * **Respuesta:** ...
            bloques_preguntas = re.findall(
                r"\d+\.\s*\*\*([^*]+)\*\*\s*\n\s*\*\s*\*\*Respuesta:\*\*\s*([^\n]+)",
                contenido,
            )
            for preg, resp in bloques_preguntas:
                anverso = f"⚠️ <b>Pregunta Trampa UNIR:</b><br>{preg.strip()}"
                reverso = f"<b>Solución oficial:</b><br>{resp.strip()}"
                tarjetas.append((anverso, reverso, f"{tema_nombre}::Trampas"))

        if not tarjetas:
            console.print(f"[yellow]No se pudieron extraer tarjetas estructuradas para {asignatura}.[/yellow]")
            return None

        slug_asig = re.sub(r'[\\/*?:"<>|]', "", asignatura).replace(" ", "_")
        ruta_tsv = FLASHCARDS_DIR / f"{slug_asig}_anki.tsv"

        with open(ruta_tsv, "w", encoding="utf-8") as f_out:
            # Cabecera para Anki
            f_out.write("#separator:tab\n#html:true\n#tags column:3\n")
            for front, back, tag in tarjetas:
                # Reemplazar tabulaciones y saltos de línea para respetar formato TSV
                f_front = front.replace("\t", " ").replace("\n", "<br>")
                f_back = back.replace("\t", " ").replace("\n", "<br>")
                f_tag = tag.replace(" ", "_").replace("\t", "")
                f_out.write(f"{f_front}\t{f_back}\t{f_tag}\n")

        console.print(
            Panel.fit(
                f"[bold green]✔ ¡{len(tarjetas)} Flashcards para Anki exportadas con éxito![/bold green]\n"
                f"Archivo: [white]{ruta_tsv}[/white]\n\n"
                "[dim]Para importar en Anki: Abre Anki en tu ordenador o móvil > 'Importar archivo' > selecciona este .tsv.[/dim]",
                border_style="green",
            )
        )
        return ruta_tsv
