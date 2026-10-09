"""
Simulador y Generador de Exámenes Tipo Test para unir-study-hub.
Modela preguntas con formato oficial UNIR (4 opciones A, B, C, D),
permite autoevaluación interactiva en terminal con puntuación sobre 10
y genera simulacros en Markdown con respuestas ocultas desplegables.
"""

import sys
from pathlib import Path
from typing import List, Dict, Any, Optional

# Asegura importaciones modulares
BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from rich.panel import Panel
from rich.table import Table
from src.core.config import console, PROCESADO_DIR


class PreguntaExamen:
    """
    Representa una pregunta de examen tipo test con sus 4 opciones y justificación.
    """

    def __init__(
        self,
        enunciado: str,
        opciones: Dict[str, str],
        correcta: str,
        justificacion: str,
    ):
        self.enunciado = enunciado
        self.opciones = opciones  # {"A": "...", "B": "...", "C": "...", "D": "..."}
        self.correcta = correcta.upper()  # "A", "B", "C" o "D"
        self.justificacion = justificacion


def generar_markdown_examen(
    asignatura: str,
    tema: str,
    titulo: str,
    preguntas: List[PreguntaExamen],
) -> str:
    """
    Genera un documento Markdown con las preguntas y las respuestas ocultas
    en bloques desplegables (<details>) para que el alumno pueda autoevaluarse.
    """
    md = []
    md.append(f"# SIMULACRO DE EXAMEN — {asignatura.upper()} ({tema.upper()})")
    md.append(f"### {titulo}")
    md.append(f"> **Total preguntas:** {len(preguntas)} | **Formato:** Tipo Test UNIR (4 opciones)\n")
    md.append("---\n")

    for i, p in enumerate(preguntas, start=1):
        md.append(f"### ❓ Pregunta {i}")
        md.append(f"**{p.enunciado}**\n")
        for letra in ["A", "B", "C", "D"]:
            texto_opc = p.opciones.get(letra, "")
            md.append(f"- **{letra})** {texto_opc}")

        md.append("\n<details>")
        md.append("<summary>👉 Toca para ver la respuesta correcta y justificación</summary>\n")
        md.append(f"**Respuesta Correcta:** `{p.correcta}`\n")
        md.append(f"**Justificación:** {p.justificacion}")
        md.append("</details>\n")
        md.append("---\n")

    return "\n".join(md)


def guardar_examen_markdown(asignatura: str, tema: str, contenido_md: str) -> Path:
    """
    Guarda el simulacro de examen en data/procesado/examenes/<asignatura>/<tema>_test.md
    """
    directorio_examenes = PROCESADO_DIR / "examenes" / asignatura.lower()
    directorio_examenes.mkdir(parents=True, exist_ok=True)

    ruta_archivo = directorio_examenes / f"{tema.lower()}_test.md"
    ruta_archivo.write_text(contenido_md, encoding="utf-8")
    return ruta_archivo


def ejecutar_test_interactivo(
    preguntas: List[PreguntaExamen],
    asignatura: Optional[str] = None,
    tema: Optional[str] = None,
):
    """
    Lanza un simulacro interactivo en la terminal usando Rich para practicar
    y registra la calificación en el expediente académico si se indica asignatura y tema.
    """
    console.print(
        Panel.fit(
            "[bold cyan]🎯 SIMULACRO INTERACTIVO DE EXAMEN[/bold cyan]\n"
            "[dim]Responde A, B, C o D en cada pregunta. Al final obtendrás tu nota sobre 10.[/dim]",
            border_style="cyan",
        )
    )

    aciertos = 0
    total = len(preguntas)

    for i, p in enumerate(preguntas, start=1):
        console.print(f"\n[bold yellow]Pregunta {i}/{total}:[/bold yellow] [bold white]{p.enunciado}[/bold white]")
        for letra in ["A", "B", "C", "D"]:
            console.print(f"  [cyan]{letra})[/cyan] {p.opciones.get(letra, '')}")

        respuesta = console.input("\n👉 Tu respuesta [A/B/C/D]: ").strip().upper()

        if respuesta == p.correcta:
            console.print("[bold green]✔ ¡CORRECTO![/bold green]")
            console.print(f"[dim]{p.justificacion}[/dim]")
            aciertos += 1
        else:
            console.print(f"[bold red]❌ INCORRECTO.[/bold red] La correcta era [bold green]{p.correcta}[/bold green].")
            console.print(f"[dim]{p.justificacion}[/dim]")

    nota = (aciertos / total) * 10
    color_nota = "green" if nota >= 5.0 else "red"

    console.print(
        Panel.fit(
            f"[bold]RESULTADO FINAL:[/bold]\n"
            f"Aciertos: [cyan]{aciertos}/{total}[/cyan]\n"
            f"Nota de Examen: [{color_nota}][bold]{nota:.2f} / 10.00[/bold][/{color_nota}]\n"
            f"{'🎉 ¡Aprobado!' if nota >= 5.0 else '📚 Toca repasar un poco más.'}",
            border_style=color_nota,
        )
    )

    if asignatura and tema:
        from src.core.progress_tracker import GestorProgreso
        tracker = GestorProgreso()
        tracker.registrar_resultado_test(asignatura, tema, nota, aciertos, total)
        console.print(f"[dim]✔ Calificación guardada en tu expediente académico para {asignatura}.[/dim]\n")


# Batería de prueba con preguntas reales de PSP (Tema 1: Procesos)
bateria_psp = [
    PreguntaExamen(
        enunciado="¿Cuál es la principal diferencia entre un proceso y un hilo (thread)?",
        opciones={
            "A": "Un proceso comparte memoria con otros procesos, mientras que los hilos tienen memoria aislada.",
            "B": "Un proceso tiene su propio espacio de direcciones de memoria aislado, mientras que los hilos de un mismo proceso comparten memoria.",
            "C": "Java no permite crear procesos nativos, solo hilos gestionados por la JVM.",
            "D": "Los procesos siempre se ejecutan en modo kernel y los hilos en modo usuario.",
        },
        correcta="B",
        justificacion="Los procesos poseen aislamiento de memoria en el sistema operativo, lo que aporta robustez. Los hilos comparten el segmento de datos del proceso padre.",
    ),
    PreguntaExamen(
        enunciado="En Java, ¿qué método de la clase Process bloquea el hilo actual hasta que el subproceso finaliza y devuelve su código de retorno?",
        opciones={
            "A": "p.start()",
            "B": "p.destroy()",
            "C": "p.waitFor()",
            "D": "p.getInputStream()",
        },
        correcta="C",
        justificacion="waitFor() causa que el hilo invocador espere hasta que el proceso representado por el objeto Process haya finalizado, devolviendo el código de salida (exit code).",
    ),
    PreguntaExamen(
        enunciado="¿Qué ventaja principal ofrece la clase ProcessBuilder frente a Runtime.getRuntime().exec() en Java?",
        opciones={
            "A": "ProcessBuilder permite configurar el entorno de variables, el directorio de trabajo y redirigir flujos de E/S de forma más flexible y segura.",
            "B": "ProcessBuilder ejecuta los comandos directamente en la GPU para mayor velocidad.",
            "C": "ProcessBuilder solo funciona en Windows y MacOS, no en distribuciones Linux.",
            "D": "ProcessBuilder no genera excepciones verificadas de tipo IOException.",
        },
        correcta="A",
        justificacion="ProcessBuilder introdujo en Java 5 una API modular orientada a objetos para configurar argumentos, variables de entorno y redirección de flujos estándar antes de iniciar el proceso.",
    ),
]

if __name__ == "__main__":
    console.print("\n[bold cyan]🧪 Generando simulacro de examen en Markdown para PSP...[/bold cyan]")
    md_test = generar_markdown_examen(
        asignatura="PSP",
        tema="tema1",
        titulo="Gestión de Procesos y Concurrencia en Java",
        preguntas=bateria_psp,
    )

    ruta = guardar_examen_markdown("psp", "tema1", md_test)
    console.print(f"[bold green]✔ Examen guardado en:[/bold green] [cyan]{ruta}[/cyan]\n")
