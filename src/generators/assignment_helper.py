"""
Asistente y Resolutor de Actividades Oficiales de UNIR (tisupdam*.docx / PDFs).
Analiza los enunciados de las tareas de entrega obligatoria, extrae los requisitos
de la rúbrica y genera una plantilla de proyecto guiada con arquitectura y código.
"""

import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Optional, List, Dict, Any

from rich.panel import Panel
from rich.table import Table

from src.core.config import console, MATERIALES_DIR, PROCESADO_DIR, ensure_directories
from src.extractors.pdf_parser import extraer_texto_pdf
from src.ai.tutor import TutorIA

ACTIVIDADES_DIR = PROCESADO_DIR / "actividades"


def extraer_texto_docx(ruta_docx: Path) -> str:
    """Extrae todo el texto plano de un archivo .docx usando zipfile nativo."""
    try:
        with zipfile.ZipFile(ruta_docx) as doc:
            xml_content = doc.read("word/document.xml")
            tree = ET.fromstring(xml_content)
            texts = [node.text for node in tree.iter() if node.text]
            return " ".join(texts)
    except Exception as e:
        console.print(f"[yellow]Error leyendo docx {ruta_docx.name}: {e}[/yellow]")
        return ""


class AsistenteActividades:
    """Orquesta el análisis de tareas y generación de plantillas de entrega."""

    def __init__(self, tutor: Optional[TutorIA] = None):
        self.tutor = tutor or TutorIA()
        ensure_directories()
        ACTIVIDADES_DIR.mkdir(parents=True, exist_ok=True)

    def listar_actividades_asignatura(self, asignatura: str) -> List[Path]:
        """Localiza todos los archivos de actividades en una asignatura."""
        dir_asig = MATERIALES_DIR / asignatura
        if not dir_asig.exists():
            return []

        archivos = []
        for f in dir_asig.glob("*"):
            nombre_low = f.name.lower()
            if any(k in nombre_low for k in ["act", "tarea", "practica", "tisupdam", "entrega"]):
                if f.suffix.lower() in [".docx", ".pdf"]:
                    archivos.append(f)
        return sorted(archivos)

    def generar_guia_actividad(self, ruta_archivo: Path, asignatura: str) -> Optional[Path]:
        """Lee el enunciado oficial y genera la plantilla de solución con IA."""
        console.print(
            Panel.fit(
                f"[bold cyan]🛠️ ASISTENTE DE ACTIVIDADES PRÁCTICAS — UNIR[/bold cyan]\n"
                f"Asignatura: [green]{asignatura}[/green] | Archivo: [yellow]{ruta_archivo.name}[/yellow]",
                border_style="cyan",
            )
        )

        texto_enunciado = ""
        if ruta_archivo.suffix.lower() == ".docx":
            texto_enunciado = extraer_texto_docx(ruta_archivo)
        elif ruta_archivo.suffix.lower() == ".pdf":
            datos_pdf = extraer_texto_pdf(ruta_archivo)
            texto_enunciado = datos_pdf.get("texto_completo", "")

        if not texto_enunciado or len(texto_enunciado) < 50:
            console.print("[red]❌ No se pudo extraer el texto del enunciado de la actividad.[/red]")
            return None

        console.print(f"[green]✔ Enunciado leído correctamente ({len(texto_enunciado)} caracteres). Analizando con IA...[/green]")

        prompt = (
            f"Eres NOVA, asistente de software y tutor de 2º DAM en UNIR.\n"
            f"A continuación tienes el enunciado oficial de una actividad práctica de la asignatura '{asignatura}':\n\n"
            f"--- ENUNCIADO OFICIAL ---\n{texto_enunciado[:30000]}\n--- FIN ENUNCIADO ---\n\n"
            "Genera una GUÍA DE DESARROLLO Y PLANTILLA DE PROYECTO en Markdown con la siguiente estructura profesional:\n"
            "# 🛠️ GUÍA DE RESOLUCIÓN: " + ruta_archivo.stem.upper() + " (" + asignatura.upper() + ")\n"
            "## 🎯 1. Desglose de Requisitos y Criterios de Evaluación (Qué pide la rúbrica para obtener un 10)\n"
            "## 🏗️ 2. Arquitectura del Proyecto y Estructura de Ficheros (Árbol de directorios, paquetes y clases recomendadas)\n"
            "## 💻 3. Código Base y Skeleton Guiado (Clases principales con estructura limpia, comentarios explicativos y métodos listos para completar)\n"
            "## 🧪 4. Pruebas y Casos de Test Recomendados (Cómo probar que cumple lo que pide el profesor)\n"
            "## 📝 5. Guion Recomendado para la Memoria de Entrega en PDF (Estructura de apartados que debe llevar el documento de entrega)\n"
        )

        guia_md = self.tutor.client.generar_contenido(prompt, temperatura=0.3)
        if not guia_md:
            console.print("[red]❌ No se pudo generar la guía de la actividad.[/red]")
            return None

        dir_dest = ACTIVIDADES_DIR / asignatura
        dir_dest.mkdir(parents=True, exist_ok=True)
        ruta_salida = dir_dest / f"{ruta_archivo.stem}_guia_resolucion.md"

        with open(ruta_salida, "w", encoding="utf-8") as f_out:
            f_out.write(guia_md)

        console.print(f"\n[bold green]✔ ¡Guía de resolución y plantilla generada con éxito en:[/bold green]\n{ruta_salida}\n")
        return ruta_salida
