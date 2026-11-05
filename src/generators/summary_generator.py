"""
Generador de resúmenes y apuntes estructurados para unir-study-hub.
Transforma el contenido extraído de temarios de la UNIR en documentos
Markdown organizados, listos para estudiar en el PC o en el móvil.
"""

import sys
from pathlib import Path
from typing import List, Dict, Any

# 1. Asegura que la raíz del proyecto esté en sys.path para importaciones modulares
BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from rich.panel import Panel
from src.core.config import console, PROCESADO_DIR


def formatear_apunte_markdown(
    asignatura: str,
    tema: str,
    titulo: str,
    resumen_teoria: str,
    conceptos_clave: List[Dict[str, str]],
    ejemplos_codigo: List[Dict[str, str]] = None,
) -> str:
    """
    Construye una plantilla Markdown estructurada y profesional con el contenido del tema.
    """
    md = []

    # Cabecera del tema
    md.append(f"# {asignatura.upper()} — {tema.upper()}: {titulo}")
    md.append(f"> **Documento generado automáticamente por NOVA Study Hub**\n")
    md.append("---\n")

    # Sección 1: Resumen Ejecutivo
    md.append("## 📌 1. Resumen Ejecutivo del Tema")
    md.append(resumen_teoria.strip())
    md.append("\n---\n")

    # Sección 2: Conceptos Clave para Examen
    md.append("## 🧠 2. Conceptos Clave y Definiciones de Examen")
    md.append("| Concepto / Término | Definición Técnica | Importancia en Examen |")
    md.append("| :--- | :--- | :--- |")
    for item in conceptos_clave:
        termino = item.get("termino", "")
        definicion = item.get("definicion", "")
        importancia = item.get("importancia", "Media")
        md.append(f"| **{termino}** | {definicion} | `{importancia}` |")
    md.append("\n---\n")

    # Sección 3: Fragmentos de Código Relevantes (si existen)
    if ejemplos_codigo:
        md.append("## 💻 3. Código Vivo y Ejemplos Prácticos")
        for ej in ejemplos_codigo:
            nombre = ej.get("nombre", "Ejemplo")
            lenguaje = ej.get("lenguaje", "java")
            codigo = ej.get("codigo", "").strip()
            explicacion = ej.get("explicacion", "")

            md.append(f"### {nombre}")
            if explicacion:
                md.append(f"*{explicacion}*\n")
            md.append(f"```{lenguaje}")
            md.append(codigo)
            md.append("```\n")
        md.append("---\n")

    # Pie del documento
    md.append("## 🎯 4. Puntos a Recordar para el Examen")
    md.append("- Repasar las definiciones de la tabla antes de entrar a la prueba.")
    md.append("- Practicar la sintaxis del código ejecutándolo en tu IDE local.")

    return "\n".join(md)


def guardar_apunte(asignatura: str, tema: str, contenido_md: str) -> Path:
    """
    Guarda el apunte Markdown en la carpeta data/procesado/apuntes/<asignatura>/<tema>.md
    """
    directorio_apuntes = PROCESADO_DIR / "apuntes" / asignatura.lower()
    directorio_apuntes.mkdir(parents=True, exist_ok=True)

    ruta_archivo = directorio_apuntes / f"{tema.lower()}.md"
    ruta_archivo.write_text(contenido_md, encoding="utf-8")
    return ruta_archivo


# Bloque de prueba ejecutable
if __name__ == "__main__":
    console.print("\n[bold cyan]📝 Generando Apunte de Prueba para 2º DAM (PSP)...[/bold cyan]")

    # Datos simulados de ejemplo para el Tema 1 de PSP
    ejemplo_teoria = (
        "En este tema se estudian los fundamentos de la programación concurrente y la gestión de procesos "
        "en sistemas operativos modernos. Se profundiza en la clase ProcessBuilder de Java para invocar "
        "subprocesos nativos, redirigir flujos estándar (stdin, stdout, stderr) y controlar códigos de retorno."
    )

    ejemplo_conceptos = [
        {
            "termino": "Proceso",
            "definicion": "Programa en ejecución con su propio espacio de memoria aislado e independiente.",
            "importancia": "Alta (Examen)",
        },
        {
            "termino": "Hilo (Thread)",
            "definicion": "Unidad básica de ejecución dentro de un proceso. Comparte memoria con otros hilos.",
            "importancia": "Alta (Examen)",
        },
        {
            "termino": "ProcessBuilder",
            "definicion": "Clase de Java que permite crear y configurar procesos del sistema operativo.",
            "importancia": "Práctica Clave",
        },
    ]

    ejemplo_codigo = [
        {
            "nombre": "Creación básica de un proceso en Java",
            "lenguaje": "java",
            "explicacion": "Lanza el comando 'ls -la' en Linux y espera su código de salida.",
            "codigo": (
                "ProcessBuilder pb = new ProcessBuilder(\"ls\", \"-la\");\n"
                "Process p = pb.start();\n"
                "int exitCode = p.waitFor();\n"
                "System.out.println(\"Código de salida: \" + exitCode);"
            ),
        }
    ]

    documento = formatear_apunte_markdown(
        asignatura="PSP",
        tema="tema1",
        titulo="Gestión de Procesos y Concurrencia",
        resumen_teoria=ejemplo_teoria,
        conceptos_clave=ejemplo_conceptos,
        ejemplos_codigo=ejemplo_codigo,
    )

    ruta_guardada = guardar_apunte("psp", "tema1", documento)
    console.print(f"[bold green]✔ ¡Apunte generado con éxito![/bold green] Guardado en:")
    console.print(f"[cyan]{ruta_guardada}[/cyan]\n")