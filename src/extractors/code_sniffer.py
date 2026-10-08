"""
Extractor de Código Vivo (Code Sniffer) para asignaturas de DAM y PFU.
Analiza el texto de apuntes o diapositivas para detectar fragmentos de código Java,
SQL, Kotlin o Bash, y guardarlos como archivos fuente ejecutables listos para su uso.
"""

import sys
import re
from pathlib import Path
from typing import List, Dict, Any

# Asegura importaciones modulares
BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from rich.panel import Panel
from src.core.config import console, PROCESADO_DIR

# Patrones de detección de lenguaje por firmas sintácticas
PATRON_JAVA = re.compile(
    r"(import\s+java[\w\.\*]+;|public\s+(?:class|interface|enum)\s+\w+|void\s+main\s*\(|ProcessBuilder|DriverManager\.getConnection)",
    re.MULTILINE,
)
PATRON_SQL = re.compile(
    r"(?i)\b(CREATE\s+TABLE|SELECT\s+.+\s+FROM|INSERT\s+INTO|ALTER\s+TABLE|DROP\s+TABLE)\b",
    re.MULTILINE,
)
PATRON_KOTLIN = re.compile(
    r"\b(fun\s+\w+\s*\(|val\s+\w+\s*:\s*\w+|var\s+\w+\s*:\s*\w+|data\s+class\s+\w+)",
    re.MULTILINE,
)


def detectar_lenguaje(texto_bloque: str) -> str:
    """
    Determina si un bloque de código corresponde a Java, SQL, Kotlin o genérico.
    """
    if PATRON_JAVA.search(texto_bloque):
        return "java"
    if PATRON_SQL.search(texto_bloque):
        return "sql"
    if PATRON_KOTLIN.search(texto_bloque):
        return "kotlin"
    return "txt"


def guardar_codigo_extraido(
    asignatura: str,
    tema: str,
    nombre_archivo: str,
    contenido: str,
    lenguaje: str,
) -> Path:
    """
    Guarda el código extraído en data/procesado/codigo/<asignatura>/<tema>/<nombre_archivo>
    """
    directorio_destino = PROCESADO_DIR / "codigo" / asignatura / tema
    directorio_destino.mkdir(parents=True, exist_ok=True)

    extensiones = {"java": ".java", "sql": ".sql", "kotlin": ".kt", "txt": ".txt"}
    ext = extensiones.get(lenguaje, ".txt")

    if not nombre_archivo.endswith(ext):
        nombre_archivo = f"{nombre_archivo}{ext}"

    ruta_final = directorio_destino / nombre_archivo

    cabecera = (
        f"// =========================================================\n"
        f"// Código extraído automáticamente por unir-study-hub\n"
        f"// Asignatura: {asignatura} | Tema: {tema}\n"
        f"// =========================================================\n\n"
    )

    ruta_final.write_text(cabecera + contenido, encoding="utf-8")
    return ruta_final


if __name__ == "__main__":
    console.print("\n[bold cyan]⚡ Módulo Extractor de Código Vivo (code_sniffer)[/bold cyan]")
    console.print("[dim]Capaz de aislar código Java, SQL y Kotlin para abrir directamente en IntelliJ o Eclipse.[/dim]")
