"""
Extractor y procesador de documentos PDF para apuntes y temarios de la UNIR.
Utiliza pypdf para extraer texto estructurado, metadatos y preparar el contenido
para el pipeline de resúmenes y extracción de código vivo.
"""

import sys
from pathlib import Path
from typing import Dict, List, Any

# Asegura importaciones modulares
BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from pypdf import PdfReader
from rich.panel import Panel
from src.core.config import console


def extraer_texto_pdf(ruta_pdf: Path) -> Dict[str, Any]:
    """
    Lee un archivo PDF y devuelve un diccionario con:
    - total_paginas: número de páginas del PDF
    - metadatos: título, autor, etc. si existen
    - paginas: lista con el texto extraído página por página
    - texto_completo: todo el texto concatenado y limpio
    """
    if not ruta_pdf.exists():
        raise FileNotFoundError(f"No se encontró el archivo: {ruta_pdf}")

    reader = PdfReader(str(ruta_pdf))
    total_paginas = len(reader.pages)
    paginas_texto: List[str] = []

    for idx, pagina in enumerate(reader.pages, start=1):
        texto = pagina.extract_text() or ""
        # Limpieza básica de espacios sobrantes preservando saltos de línea
        texto_limpio = "\n".join(line.rstrip() for line in texto.splitlines())
        paginas_texto.append(texto_limpio)

    texto_completo = "\n\n--- [SALTO DE PÁGINA] ---\n\n".join(paginas_texto)

    return {
        "archivo": ruta_pdf.name,
        "ruta": str(ruta_pdf),
        "total_paginas": total_paginas,
        "metadatos": reader.metadata or {},
        "paginas": paginas_texto,
        "texto_completo": texto_completo,
    }


if __name__ == "__main__":
    console.print("\n[bold cyan]📄 Módulo Extractor de PDFs (pdf_parser)[/bold cyan]")
    console.print("[dim]Listo para procesar temarios y diapositivas de la UNIR.[/dim]")
