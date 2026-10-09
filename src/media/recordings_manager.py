"""
Gestor de Descarga y Transcripción de Clases Grabadas (UNIR).
Utiliza yt-dlp y ffmpeg para descargar el audio/vídeo de lecciones magistrales
y opcionalmente transcribirlas y resumirlas con Inteligencia Artificial.
"""

import sys
import subprocess
from pathlib import Path
from typing import Optional, Dict, Any

# Importación modular
BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from rich.panel import Panel
from rich.table import Table

from src.core.config import (
    console,
    CLASES_GRABADAS_DIR,
    TRANSCRIPCIONES_DIR,
    ensure_directories,
)
from src.ai.llm_client import LLMClient


class RecordingsManager:
    """Administra la captura multimedia y procesamiento de clases grabadas."""

    def __init__(self, llm_client: Optional[LLMClient] = None):
        self.llm_client = llm_client or LLMClient()
        ensure_directories()

    def descargar_clase(
        self,
        url_clase: str,
        asignatura: str,
        nombre_clase: str,
        solo_audio: bool = True,
    ) -> Optional[Path]:
        """
        Descarga una clase grabada desde su URL mediante yt-dlp.
        Por defecto extrae audio MP3 (ligero y óptimo para transcripción).
        """
        dir_destino = CLASES_GRABADAS_DIR / asignatura
        dir_destino.mkdir(parents=True, exist_ok=True)

        nombre_seguro = "".join(c for c in nombre_clase if c.isalnum() or c in (" ", "_", "-")).rstrip()
        nombre_seguro = nombre_seguro.replace(" ", "_")

        ext_salida = "mp3" if solo_audio else "mp4"
        archivo_salida = dir_destino / f"{nombre_seguro}.{ext_salida}"

        plantilla_salida = str(dir_destino / f"{nombre_seguro}.%(ext)s")

        console.print(f"\n[cyan]📥 Iniciando descarga de la clase:[/cyan] [bold]{nombre_clase}[/bold]")
        console.print(f"[dim]URL:[/dim] {url_clase}")
        console.print(f"[dim]Formato:[/dim] {'Audio MP3 (optimizado para IA)' if solo_audio else 'Vídeo MP4 completo'}")

        comando = ["yt-dlp", "--no-warnings", "-o", plantilla_salida]

        if solo_audio:
            comando.extend([
                "-x",
                "--audio-format", "mp3",
                "--audio-quality", "5",  # Calidad de voz óptima (~128kbps)
            ])
        else:
            comando.extend([
                "-f", "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
            ])

        comando.append(url_clase)

        try:
            proceso = subprocess.run(
                comando,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )

            if proceso.returncode != 0:
                console.print(f"[bold red]❌ Error descargando con yt-dlp:[/bold red]\n{proceso.stderr}")
                return None

            # Buscar el archivo resultante
            coincidencias = list(dir_destino.glob(f"{nombre_seguro}.*"))
            if coincidencias:
                archivo_final = coincidencias[0]
                tam_mb = archivo_final.stat().st_size / (1024 * 1024)
                console.print(f"[bold green]✔ Clase descargada con éxito:[/bold green] {archivo_final.name} ({tam_mb:.1f} MB)\n")
                return archivo_final
            else:
                console.print("[yellow]⚠ La descarga finalizó pero no se localizó el archivo resultante.[/yellow]")
                return None

        except FileNotFoundError:
            console.print("[bold red]❌ 'yt-dlp' no está instalado en el sistema.[/bold red]")
            return None
        except Exception as e:
            console.print(f"[bold red]❌ Error inesperado durante la descarga:[/bold red] {e}")
            return None

    def transcribir_y_resumir_clase(
        self,
        ruta_archivo: Path,
        asignatura: str,
        nombre_clase: str,
    ) -> Optional[Path]:
        """
        Envía el audio a Gemini para obtener una transcripción completa
        y un resumen estructurado con puntos clave para examen.
        """
        if not self.llm_client.esta_configurado():
            if not self.llm_client.solicitar_api_key_interactiva():
                console.print("[yellow]⚠ No se puede transcribir sin clave de Gemini configurada.[/yellow]")
                return None

        console.print(f"\n[cyan]🧠 Procesando clase grabada con IA:[/cyan] [bold]{nombre_clase}[/bold]...")

        resultado_md = self.llm_client.procesar_audio_clase(
            ruta_audio=ruta_archivo,
            asignatura=asignatura,
            tema=nombre_clase,
        )

        if not resultado_md:
            console.print("[bold red]❌ No se pudo generar la transcripción de la clase.[/bold red]")
            return None

        dir_transcripciones = TRANSCRIPCIONES_DIR / asignatura
        dir_transcripciones.mkdir(parents=True, exist_ok=True)

        archivo_md = dir_transcripciones / f"{nombre_clase}.md"
        with open(archivo_md, "w", encoding="utf-8") as f:
            f.write(f"# 🎙️ TRANSCRIPCIÓN Y APUNTES DE CLASE MAGISTRAL\n")
            f.write(f"**Asignatura:** {asignatura} | **Clase:** {nombre_clase}\n")
            f.write(f"> Archivo fuente original: `{ruta_archivo.name}`\n\n---\n\n")
            f.write(resultado_md)

        console.print(f"[bold green]✔ ¡Transcripción y apuntes de clase guardados en:[/bold green] {archivo_md}\n")
        return archivo_md

    def listar_clases_disponibles(self):
        """Muestra una tabla con todas las videoclases sincronizadas y sus transcripciones."""
        transcripciones = list(TRANSCRIPCIONES_DIR.glob("**/*.md"))
        fichas = list(CLASES_GRABADAS_DIR.glob("**/*_info.json"))
        medios = [a for a in CLASES_GRABADAS_DIR.glob("**/*.*") if a.is_file() and a.suffix.lower() in [".mp3", ".mp4", ".m4a", ".mkv"]]

        total_clases = len(transcripciones) or len(fichas)

        if total_clases == 0 and len(medios) == 0:
            console.print("[dim]No hay ninguna clase grabada sincronizada todavía. Usa la opción 3 > 1 para sincronizar con el campus.[/dim]\n")
            return

        table = Table(
            title=f"🎙️ Videoclases y Lecciones Magistrales UNIR ({total_clases} clases sincronizadas)",
            border_style="cyan"
        )
        table.add_column("Asignatura", style="cyan")
        table.add_column("Fecha", style="yellow")
        table.add_column("Sesión / Clase", style="white")
        table.add_column("Transcripción Oficial", style="green")
        table.add_column("Vídeo MP4", style="magenta")

        # Agrupar por asignatura
        for t in sorted(transcripciones, key=lambda x: (x.parent.name, x.name)):
            asig = t.parent.name
            nombre = t.stem
            fecha = nombre[:10] if len(nombre) >= 10 and nombre[:4].isdigit() else "Reciente"
            titulo = nombre[11:] if len(nombre) >= 11 and nombre[:4].isdigit() else nombre

            # Comprobar si hay vídeo descargado o ficha lista
            posible_mp4 = CLASES_GRABADAS_DIR / asig / f"{nombre}.mp4"
            posible_info = CLASES_GRABADAS_DIR / asig / f"{nombre}_info.json"

            estado_video = "💾 Guardado" if posible_mp4.exists() else ("🔗 Enlace Oficial" if posible_info.exists() else "No disp.")
            tam_kb = t.stat().st_size / 1024

            table.add_row(asig[:30], fecha, titulo[:38], f"✔ {tam_kb:.0f} KB (Markdown)", estado_video)

        console.print(table)
        console.print(f"[dim]Total: {total_clases} lecciones magistrales con transcripción completa palabra por palabra.[/dim]\n")
