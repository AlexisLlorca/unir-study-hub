"""
Cliente de Inteligencia Artificial para UNIR Study Hub basado en Google Gemini.
Maneja la autenticación, generación de texto, streaming y análisis multimodal de audio.
"""

import os
import sys
from pathlib import Path
from typing import Optional, Generator, List, Dict

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
Eres NOVA, la mentora de élite, copiloto de Inteligencia Artificial y consultora senior de software para el alumno Alex en UNIR Study Hub.
Especializada en 2º de FP DAM (Desarrollo de Aplicaciones Multiplataforma), Ingeniería del Software, Ciberseguridad y Certificaciones Cloud (AWS & Azure).

TUS ÁREAS CLAVE DE ESPECIALIZACIÓN TÉCNICA:
1. Acceso a Datos (AD): Manejo avanzado de ficheros y streams I/O, serialización de objetos (Serializable, Externalizable, serialVersionUID), XML/JSON (DOM, SAX, StAX, XPath, XSLT, Jackson, Gson), JDBC avanzado, Pooling de conexiones (HikariCP), Transacciones ACID (niveles de aislamiento, Savepoints, commit/rollback), Mapeo Objeto-Relacional (Hibernate / JPA, ciclo de vida de entidades, SessionFactory), Bases de datos documentales (MongoDB, BSON, agregaciones) y Spring Data.
2. Programación de Servicios y Procesos (PSP): Gestión de procesos en el SO con ProcessBuilder y Process, concurrencia y multihilo en Java (Thread, Runnable, Callable, Future, ExecutorService), sincronización de hilos (monitores, bloques synchronized, semáforos, ReentrantLock, variables volátiles, problemas clásicos: filósofos, productor-consumidor), programación de sockets de red (TCP ServerSocket/Socket, UDP DatagramSocket, multiplexación) y desarrollo de servicios seguros (criptografía, SSL/TLS, servicios REST).
3. Arquitecturas en la Nube y Cloud Computing: AWS (EC2, S3, RDS, VPC, subnets públicas/privadas, IAM, roles y políticas, LabRole en AWS Academy, CloudWatch, presupuestos y FinOps), Azure (AZ-900: servicios core, regiones, disponibilidad, modelos de despliegue IaaS/PaaS/SaaS, costes y SLAs).
4. Programación Multimedia y Dispositivos Móviles (PMDM): Kotlin moderno, Jetpack Compose, Corrutinas (Dispatchers, Flow), Arquitectura MVVM / Clean Architecture, ciclo de vida de componentes Android y consumo de APIs REST con Retrofit.
5. Sistemas de Gestión Empresarial (SGE), Desarrollo de Interfaces (DI), Ciberseguridad e Inglés Técnico.

DIRECTRICES DE MÁXIMA ROBUSTEZ Y RIGOR TÉCNICO:
- PROFUNDIDAD DE INGENIERÍA: Prohibido dar respuestas superficiales, telegráficas o simplistas. Desarrolla siempre la explicación con rigor académico y profesional.
- EL "POR QUÉ" Y "BAJO EL CAPÓ": Explica siempre qué sucede a nivel de máquina, JVM, sistema operativo, protocolos de red o motor de base de datos.
- CÓDIGO DE PRODUCCIÓN COMPLETO: Proporciona código real, compilable, fuertemente tipado y moderno (Java 17/21, Kotlin actual). Nada de snippets vagos con '// aquí tu lógica'; implementa la lógica completa con gestión exhaustiva de excepciones (try-with-resources, logging, excepciones específicas).
- TRAMPAS Y CASOS CRÍTICOS DE EXAMEN UNIR: Señala explícitamente las preguntas capciosas típicas en los exámenes de UNIR, confusiones conceptuales habituales, casos borde y excepciones frecuentes (ej: NotSerializableException, ConcurrentModificationException, SQLException de concurrencia).
- ESTRUCTURA DIDÁCTICA IMPECABLE: Emplea Markdown enriquecido con títulos jerárquicos, listas ordenadas, negritas sobre términos clave, bloques de código sintáctico y tablas comparativas para contrastar tecnologías o patrones.
- CITAS EXACTAS: Si se consulta sobre videoclases, cita con precisión el minuto exacto [MM:SS], el profesor titular y el contenido literal tratado.
- TONO: Mentora técnica experta, directa, empática, rigurosa y orientada a conseguir la Matrícula de Honor.
"""


class LLMClient:
    """Gestiona la comunicación con la API de Google Gemini."""

    _modelos_agotados: set = set()

    def __init__(self, model_name: Optional[str] = None):
        self.model_name = model_name or DEFAULT_GEMINI_MODEL
        self._client: Optional[genai.Client] = None

    @staticmethod
    def _log_seguro(mensaje: str) -> None:
        """Imprime en consola ignorando BrokenPipeError cuando el servidor corre en segundo plano."""
        try:
            console.print(mensaje)
        except Exception:
            pass

    def _obtener_lista_modelos(self) -> List[str]:
        """Devuelve la lista de modelos priorizando los que tienen cuota activa en esta sesión."""
        candidatos = [
            self.model_name,
            "gemini-3.5-flash-lite",
            "gemini-flash-lite-latest",
            "gemini-3.1-flash-lite",
            "gemini-3.5-flash",
            "gemini-3.7-flash",
        ]
        activos = []
        agotados = []
        for m in candidatos:
            if not m or m in activos or m in agotados:
                continue
            if m in self._modelos_agotados:
                agotados.append(m)
            else:
                activos.append(m)
        return activos + agotados

    def _registrar_error_modelo(self, mod: str, err: Exception) -> None:
        err_str = str(err)
        if "429" in err_str or "RESOURCE_EXHAUSTED" in err_str or "404" in err_str or "NOT_FOUND" in err_str:
            self._modelos_agotados.add(mod)

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
            self._log_seguro(f"[red]Error inicializando cliente de Gemini:[/red] {e}")
            return None

    def esta_configurado(self) -> bool:
        """Comprueba si hay una clave de API guardada y utilizable."""
        return bool(get_gemini_api_key())

    def solicitar_api_key_interactiva(self) -> bool:
        """Pide al usuario su clave de Gemini si aún no la ha configurado."""
        self._log_seguro(
            "[bold cyan]🔑 CONFIGURACIÓN DE INTELIGENCIA ARTIFICIAL (Google Gemini)[/bold cyan]\n"
            "Consíguela gratis en: https://aistudio.google.com/app/apikey"
        )
        clave = console.input("👉 Pega tu Gemini API Key (o pulsa Enter para omitir): ").strip()
        if clave:
            save_gemini_api_key(clave)
            self._client = None
            self._log_seguro("[bold green]✔ Clave de Gemini guardada correctamente en .env.[/bold green]\n")
            return True
        return False

    def generar_contenido(
        self,
        prompt: str,
        system_instruction: Optional[str] = None,
        temperatura: float = 0.5,
        max_output_tokens: Optional[int] = None,
        response_mime_type: Optional[str] = None,
    ) -> Optional[str]:
        """Genera una respuesta completa a partir del prompt proporcionado."""
        client = self._obtener_cliente()
        if not client:
            return None

        sys_inst = system_instruction or SYSTEM_PROMPT_TUTOR

        config_args = {
            "system_instruction": sys_inst,
            "temperature": temperatura,
        }
        if max_output_tokens:
            config_args["max_output_tokens"] = max_output_tokens
        if response_mime_type:
            config_args["response_mime_type"] = response_mime_type

        config = types.GenerateContentConfig(**config_args)
        modelos = self._obtener_lista_modelos()

        for mod in modelos:
            try:
                response = client.models.generate_content(
                    model=mod,
                    contents=prompt,
                    config=config,
                )
                if response and response.text:
                    self._modelos_agotados.discard(mod)
                    return response.text
            except Exception as e:
                self._registrar_error_modelo(mod, e)
                self._log_seguro(f"[dim]Reintentando con modelo de respaldo tras error en {mod}: {e}[/dim]")
                continue

        self._log_seguro("[bold red]❌ Error de comunicación con Gemini en todos los modelos disponibles.[/bold red]")
        return None

    def generar_chat(
        self,
        mensajes: List[Dict[str, str]],
        system_instruction: Optional[str] = None,
        temperatura: float = 0.4,
    ) -> Optional[str]:
        """Genera una respuesta en un diálogo multi-turno manteniendo la memoria contextual exacta."""
        client = self._obtener_cliente()
        if not client:
            return None

        sys_inst = system_instruction or SYSTEM_PROMPT_TUTOR

        config = types.GenerateContentConfig(
            system_instruction=sys_inst,
            temperature=temperatura,
        )

        contents = []
        for m in mensajes:
            rol = m.get("role") or m.get("rol", "user")
            texto = m.get("text") or m.get("texto", "")
            if not texto:
                continue
            if rol not in ["user", "model"]:
                rol = "user" if str(rol).lower() in ["alumno", "human", "usuario", "user"] else "model"
            contents.append(
                types.Content(
                    role=rol,
                    parts=[types.Part.from_text(text=texto)],
                )
            )

        if not contents:
            return None

        # Asegurar alternancia limpia de roles para el protocolo de chat
        contents_limpio = []
        for c in contents:
            if contents_limpio and contents_limpio[-1].role == c.role:
                contents_limpio[-1].parts.extend(c.parts)
            else:
                contents_limpio.append(c)

        modelos = self._obtener_lista_modelos()

        for mod in modelos:
            try:
                response = client.models.generate_content(
                    model=mod,
                    contents=contents_limpio,
                    config=config,
                )
                if response and response.text:
                    self._modelos_agotados.discard(mod)
                    return response.text
            except Exception as e:
                self._registrar_error_modelo(mod, e)
                self._log_seguro(f"[dim]Reintentando chat con modelo de respaldo tras error en {mod}: {e}[/dim]")
                continue

        self._log_seguro("[bold red]❌ Error de comunicación con Gemini en chat multi-turno.[/bold red]")
        return None

    def generar_chat_stream(
        self,
        mensajes: List[Dict[str, str]],
        system_instruction: Optional[str] = None,
        temperatura: float = 0.4,
    ) -> Generator[str, None, None]:
        """Genera una respuesta en streaming en un diálogo multi-turno con respaldo automático de modelos."""
        client = self._obtener_cliente()
        if not client:
            return

        sys_inst = system_instruction or SYSTEM_PROMPT_TUTOR
        config = types.GenerateContentConfig(
            system_instruction=sys_inst,
            temperature=temperatura,
        )

        contents = []
        for m in mensajes:
            rol = m.get("role") or m.get("rol", "user")
            texto = m.get("text") or m.get("texto", "")
            if not texto:
                continue
            if rol not in ["user", "model"]:
                rol = "user" if str(rol).lower() in ["alumno", "human", "usuario", "user"] else "model"
            contents.append(
                types.Content(
                    role=rol,
                    parts=[types.Part.from_text(text=texto)],
                )
            )

        if not contents:
            return

        contents_limpio = []
        for c in contents:
            if contents_limpio and contents_limpio[-1].role == c.role:
                contents_limpio[-1].parts.extend(c.parts)
            else:
                contents_limpio.append(c)

        modelos = self._obtener_lista_modelos()

        for mod in modelos:
            emitido = False
            try:
                response_stream = client.models.generate_content_stream(
                    model=mod,
                    contents=contents_limpio,
                    config=config,
                )
                for chunk in response_stream:
                    if chunk.text:
                        emitido = True
                        yield chunk.text
                if emitido:
                    self._modelos_agotados.discard(mod)
                    return
            except Exception as e:
                self._registrar_error_modelo(mod, e)
                self._log_seguro(f"[dim]Reintentando chat stream con modelo de respaldo tras error en {mod}: {e}[/dim]")
                if emitido:
                    return
                continue

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
