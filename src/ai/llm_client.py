"""
Cliente de Inteligencia Artificial para UNIR Study Hub basado en Google Gemini.
Maneja la autenticación, generación de texto, streaming y análisis multimodal de audio.
"""

import os
import sys
from pathlib import Path
from typing import Optional, Generator

# Asegura importación modular
BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from google import genai
from google.genai import types
from rich.panel import Panel

from src.core.config import (
    console,
    get_gemini_api_key,
    save_gemini_api_key,
    DEFAULT_GEMINI_MODEL,
)

SYSTEM_PROMPT_TUTOR = """
Eres NOVA, el Tutor de Inteligencia Artificial y Copiloto de Estudio para 2º de FP DAM
(Desarrollo de Aplicaciones Multiplataforma) y el PFU de Ingeniería del Software en la UNIR.

Tus áreas de máxima especialización incluyen:
1. Programación de Servicios y Procesos (PSP): Java, ProcessBuilder, concurrencia, hilos (Threads, Runnable),
   sincronización, sockets TCP/UDP, arquitectura cliente-servidor, servicios REST y criptografía.
2. Acceso a Datos (AD): Manejo de ficheros, XML/JSON, JDBC, Hibernate/JPA, bases de datos orientadas a objetos y Spring Data.
3. Programación Multimedia y Dispositivos Móviles (PMDM): Kotlin, Jetpack Compose, ciclo de vida de actividades,
   APIs REST en Android y motores como Unity.
4. Desarrollo de Interfaces (DI): Usabilidad, componentes, patrones MVC/MVVM.
5. Arquitecturas Software y Cloud (PFU): Microservicios, diseño orientado a objetos, UML, clean architecture.

Directrices pedagógicas:
- Sé riguroso, didáctico y conciso.
- Cuando expliques código, proporciona código compilable y funcional listo para copiar en IDEs (IntelliJ, Eclipse, Android Studio).
- Explica los porqués de las soluciones y los errores típicos de examen en UNIR.
- Destaca advertencias para los exámenes oficiales (tipo test o casos prácticos).
"""


class LLMClient:
    """Gestiona la comunicación con la API de Google Gemini."""

    def __init__(self, model_name: Optional[str] = None):
        self.model_name = model_name or DEFAULT_GEMINI_MODEL
        self._client: Optional[genai.Client] = None

    def _obtener_cliente(self) -> Optional[genai.Client]:
        """Inicializa de forma perezosa el cliente de Gemini."""
        if self._client:
            return self._client

        api_key = get_gemini_api_key()
        if not api_key:
            return None

        try:
            self._client = genai.Client(api_key=api_key)
            return self._client
        except Exception as e:
            console.print(f"[red]Error inicializando cliente de Gemini:[/red] {e}")
            return None

    def esta_configurado(self) -> bool:
        """Comprueba si hay una clave de API guardada y utilizable."""
        return bool(get_gemini_api_key())

    def solicitar_api_key_interactiva(self) -> bool:
        """Pide al usuario su clave de Gemini si aún no la ha configurado."""
        console.print(
            Panel.fit(
                "[bold cyan]🔑 CONFIGURACIÓN DE INTELIGENCIA ARTIFICIAL (Google Gemini)[/bold cyan]\n\n"
                "Para activar el Tutor de IA, resúmenes automáticos y simulacros de examen,\n"
                "necesitas una clave gratuita de Google Gemini.\n\n"
                "👉 Consíguela gratis en: [bold underline white]https://aistudio.google.com/app/apikey[/bold underline white]\n",
                border_style="cyan",
            )
        )
        clave = console.input("👉 Pega tu Gemini API Key (o pulsa Enter para omitir): ").strip()
        if clave:
            save_gemini_api_key(clave)
            self._client = None
            console.print("[bold green]✔ Clave de Gemini guardada correctamente en .env.[/bold green]\n")
            return True
        return False

    def generar_contenido(
        self,
        prompt: str,
        system_instruction: Optional[str] = None,
        temperatura: float = 0.5,
    ) -> Optional[str]:
        """Genera una respuesta completa a partir del prompt proporcionado."""
        client = self._obtener_cliente()
        if not client:
            return None

        sys_inst = system_instruction or SYSTEM_PROMPT_TUTOR

        config = types.GenerateContentConfig(
            system_instruction=sys_inst,
            temperature=temperatura,
        )

        try:
            response = client.models.generate_content(
                model=self.model_name,
                contents=prompt,
                config=config,
            )
            return response.text
        except Exception as e:
            console.print(f"[bold red]❌ Error de comunicación con Gemini:[/bold red] {e}")
            return None

    def generar_stream(
        self,
        prompt: str,
        system_instruction: Optional[str] = None,
        temperatura: float = 0.5,
    ) -> Generator[str, None, None]:
        """Genera una respuesta en streaming para salidas interactivas en vivo."""
        client = self._obtener_cliente()
        if not client:
            return

        sys_inst = system_instruction or SYSTEM_PROMPT_TUTOR

        config = types.GenerateContentConfig(
            system_instruction=sys_inst,
            temperature=temperatura,
        )

        try:
            response_stream = client.models.generate_content_stream(
                model=self.model_name,
                contents=prompt,
                config=config,
            )
            for chunk in response_stream:
                if chunk.text:
                    yield chunk.text
        except Exception as e:
            console.print(f"[bold red]❌ Error en streaming con Gemini:[/bold red] {e}")

    def procesar_audio_clase(
        self,
        ruta_audio: Path,
        asignatura: str,
        tema: str,
    ) -> Optional[str]:
        """
        Sube un archivo de audio grabado de clase a Gemini para transcribirlo
        y estructurarlo en apuntes claros y accionables.
        """
        client = self._obtener_cliente()
        if not client:
            return None

        if not ruta_audio.exists():
            console.print(f"[red]No se encontró el archivo de audio:[/red] {ruta_audio}")
            return None

        console.print(f"[cyan]🎙️ Subiendo audio de la clase ({ruta_audio.name}) a Gemini...[/cyan]")
        try:
            archivo_subido = client.files.upload(file=str(ruta_audio))
            console.print("[green]✔ Audio subido. Generando transcripción y resumen técnico...[/green]")

            prompt = (
                f"Este es el audio de una clase magistral de la asignatura '{asignatura}', "
                f"correspondiente al '{tema}' en UNIR.\n\n"
                "Por favor, realiza lo siguiente:\n"
                "1. Escribe un resumen detallado de los temas explicados por el profesor.\n"
                "2. Extrae todas las menciones a preguntas típicas de examen, fechas o advertencias que haya dicho el docente.\n"
                "3. Reconstruye el código fuente o ejemplos que el profesor haya explicado verbalmente o en pizarra.\n"
                "4. Estructura el resultado en formato Markdown limpio y profesional."
            )

            response = client.models.generate_content(
                model=self.model_name,
                contents=[archivo_subido, prompt],
            )
            return response.text
        except Exception as e:
            console.print(f"[bold red]❌ Error procesando audio de clase:[/bold red] {e}")
            return None
