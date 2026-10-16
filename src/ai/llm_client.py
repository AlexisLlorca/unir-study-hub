"""
Cliente de Inteligencia Artificial para UNIR Study Hub basado en Google Gemini.
Maneja la autenticación, generación de texto, streaming y análisis multimodal de audio.
"""

import json
import os
import sys
import urllib.request
from pathlib import Path
from typing import Optional, Generator, List, Dict, Any

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
    get_openrouter_api_key,
    get_groq_api_key,
    DEFAULT_GEMINI_MODEL,
)

CATALOGO_MODELOS_GRATUITOS: List[Dict[str, Any]] = [
    {
        "id": "auto",
        "nombre": "⚡ Auto (Rotación Inteligente Gratuita)",
        "proveedor": "auto",
        "gratis": True,
        "descripcion": "Salta automáticamente al mejor modelo gratuito con cuota libre",
    },
    {
        "id": "gemini-3-flash-preview",
        "nombre": "Gemini 3 Flash Preview",
        "proveedor": "google",
        "gratis": True,
        "descripcion": "Google AI Studio · Rápido y alta capacidad (Cuota independiente)",
    },
    {
        "id": "gemini-3.5-flash-lite",
        "nombre": "Gemini 3.5 Flash Lite",
        "proveedor": "google",
        "gratis": True,
        "descripcion": "Google AI Studio · Ultra rápido y cuota diaria amplia",
    },
    {
        "id": "gemini-flash-lite-latest",
        "nombre": "Gemini Flash Lite Latest",
        "proveedor": "google",
        "gratis": True,
        "descripcion": "Google AI Studio · Estable con bolsa de cuota propia",
    },
    {
        "id": "gemma-4-31b-it",
        "nombre": "Gemma 4 31B Instruct",
        "proveedor": "google",
        "gratis": True,
        "descripcion": "Google Open Models · 31B parámetros (Gratis con tu API Key)",
    },
    {
        "id": "gemma-4-26b-a4b-it",
        "nombre": "Gemma 4 26B MoE",
        "proveedor": "google",
        "gratis": True,
        "descripcion": "Google Open Models · Mixture-of-Experts rápido (Gratis)",
    },
    {
        "id": "gemini-3.5-flash",
        "nombre": "Gemini 3.5 Flash",
        "proveedor": "google",
        "gratis": True,
        "descripcion": "Google AI Studio · 20 peticiones/día en capa gratuita",
    },
    {
        "id": "gemini-3.7-flash",
        "nombre": "Gemini 3.7 Flash",
        "proveedor": "google",
        "gratis": True,
        "descripcion": "Google AI Studio · Última generación Flash",
    },
    {
        "id": "gemini-3.8-flash",
        "nombre": "Gemini 3.8 Flash",
        "proveedor": "google",
        "gratis": True,
        "descripcion": "Google AI Studio · Experimental de alta velocidad",
    },
    {
        "id": "gemini-3.1-flash-lite",
        "nombre": "Gemini 3.1 Flash Lite",
        "proveedor": "google",
        "gratis": True,
        "descripcion": "Google AI Studio · Ligero de respaldo",
    },
    {
        "id": "openrouter:deepseek/deepseek-chat-v3-0324:free",
        "nombre": "DeepSeek V3 0324 (Free)",
        "proveedor": "openrouter",
        "gratis": True,
        "descripcion": "OpenRouter Gratis · Excelente en código y arquitectura",
    },
    {
        "id": "openrouter:qwen/qwen3-235b-a22b:free",
        "nombre": "Qwen 3 235B MoE (Free)",
        "proveedor": "openrouter",
        "gratis": True,
        "descripcion": "OpenRouter Gratis · Gran capacidad de razonamiento",
    },
    {
        "id": "openrouter:meta-llama/llama-3.3-70b-instruct:free",
        "nombre": "Llama 3.3 70B Instruct (Free)",
        "proveedor": "openrouter",
        "gratis": True,
        "descripcion": "OpenRouter Gratis · Meta Llama 3.3 70B",
    },
    {
        "id": "openrouter:mistralai/mistral-small-3.1-24b-instruct:free",
        "nombre": "Mistral Small 3.1 24B (Free)",
        "proveedor": "openrouter",
        "gratis": True,
        "descripcion": "OpenRouter Gratis · Rápido y preciso en español",
    },
    {
        "id": "groq:llama-3.3-70b-versatile",
        "nombre": "Llama 3.3 70B LPU (Groq Free)",
        "proveedor": "groq",
        "gratis": True,
        "descripcion": "Groq Cloud Gratis · Velocidad instantánea en LPU",
    },
    {
        "id": "groq:deepseek-r1-distill-llama-70b",
        "nombre": "DeepSeek R1 Distill 70B (Groq Free)",
        "proveedor": "groq",
        "gratis": True,
        "descripcion": "Groq Cloud Gratis · Razonamiento profundo paso a paso",
    },
]

NOMBRES_MODELOS_MAP: Dict[str, str] = {m["id"]: m["nombre"] for m in CATALOGO_MODELOS_GRATUITOS}

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
    """Gestiona la comunicación multi-modelo con Google Gemini, Gemma, OpenRouter Free y Groq Free."""

    _modelos_agotados: set = set()

    def __init__(self, model_name: Optional[str] = None):
        self.model_name = model_name or DEFAULT_GEMINI_MODEL
        self._client: Optional[genai.Client] = None
        self.ultimo_modelo_usado: str = "gemini-3-flash-preview"

    @staticmethod
    def _log_seguro(mensaje: str) -> None:
        """Imprime en consola ignorando BrokenPipeError cuando el servidor corre en segundo plano."""
        try:
            console.print(mensaje)
        except Exception:
            pass

    @classmethod
    def obtener_nombre_legible(cls, mod_id: str) -> str:
        return NOMBRES_MODELOS_MAP.get(mod_id, mod_id)

    def obtener_estado_modelos(self) -> Dict[str, Any]:
        """Devuelve el catálogo completo de modelos gratuitos y el estado de cada proveedor."""
        tiene_gemini = bool(get_gemini_api_key())
        tiene_openrouter = bool(get_openrouter_api_key())
        tiene_groq = bool(get_groq_api_key())

        modelos_info = []
        for m in CATALOGO_MODELOS_GRATUITOS:
            prov = m["proveedor"]
            disponible = True
            if prov == "google":
                disponible = tiene_gemini
            elif prov == "openrouter":
                disponible = tiene_openrouter
            elif prov == "groq":
                disponible = tiene_groq

            agotado = m["id"] in self._modelos_agotados
            modelos_info.append({
                **m,
                "configurado": disponible,
                "agotado_temporalmente": agotado,
            })

        return {
            "modelos": modelos_info,
            "ultimo_modelo_usado": self.ultimo_modelo_usado,
            "ultimo_modelo_nombre": self.obtener_nombre_legible(self.ultimo_modelo_usado),
            "proveedores": {
                "google": tiene_gemini,
                "openrouter": tiene_openrouter,
                "groq": tiene_groq,
            },
        }

    def _obtener_lista_modelos(self, modelo_preferido: Optional[str] = None) -> List[str]:
        """Devuelve la cadena de modelos gratuitos priorizando el elegido y los que tienen cuota activa."""
        candidatos_base = [
            "gemini-3-flash-preview",
            "gemini-3.5-flash-lite",
            "gemini-flash-lite-latest",
            "gemma-4-26b-a4b-it",
            "gemma-4-31b-it",
            self.model_name,
            "gemini-3.5-flash",
            "gemini-3.7-flash",
            "gemini-3.8-flash",
            "gemini-3.1-flash-lite",
        ]
        if get_groq_api_key():
            candidatos_base.extend([
                "groq:llama-3.3-70b-versatile",
                "groq:deepseek-r1-distill-llama-70b",
            ])
        if get_openrouter_api_key():
            candidatos_base.extend([
                "openrouter:deepseek/deepseek-chat-v3-0324:free",
                "openrouter:qwen/qwen3-235b-a22b:free",
                "openrouter:meta-llama/llama-3.3-70b-instruct:free",
                "openrouter:mistralai/mistral-small-3.1-24b-instruct:free",
            ])

        activos = []
        agotados = []
        for m in candidatos_base:
            if not m or m in activos or m in agotados:
                continue
            if m in self._modelos_agotados:
                agotados.append(m)
            else:
                activos.append(m)

        orden = activos + agotados
        if modelo_preferido and modelo_preferido != "auto":
            orden = [modelo_preferido] + [m for m in orden if m != modelo_preferido]
        return orden

    def _registrar_error_modelo(self, mod: str, err: Exception) -> None:
        err_str = str(err)
        if any(k in err_str for k in ["429", "RESOURCE_EXHAUSTED", "404", "NOT_FOUND", "503", "UNAVAILABLE"]):
            self._modelos_agotados.add(mod)

    def _llamar_openai_compatible(
        self,
        mod: str,
        mensajes_norm: List[Dict[str, str]],
        sys_inst: str,
        temperatura: float,
    ) -> Optional[str]:
        """Invoca proveedores gratuitos compatibles con OpenAI (OpenRouter Free / Groq Free)."""
        if mod.startswith("openrouter:"):
            api_key = get_openrouter_api_key()
            if not api_key:
                return None
            url = "https://openrouter.ai/api/v1/chat/completions"
            model_real = mod.split("openrouter:", 1)[1]
            extra_headers = {"HTTP-Referer": "http://localhost:8080", "X-Title": "UNIR Study Hub"}
        elif mod.startswith("groq:"):
            api_key = get_groq_api_key()
            if not api_key:
                return None
            url = "https://api.groq.com/openai/v1/chat/completions"
            model_real = mod.split("groq:", 1)[1]
            extra_headers = {}
        else:
            return None

        oa_msgs = [{"role": "system", "content": sys_inst}]
        for m in mensajes_norm:
            rol = "user" if m["role"] == "user" else "assistant"
            oa_msgs.append({"role": rol, "content": m["text"]})

        req = urllib.request.Request(
            url,
            data=json.dumps({
                "model": model_real,
                "messages": oa_msgs,
                "temperature": temperatura,
            }).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
                "User-Agent": "UNIR-Study-Hub/2.0",
                **extra_headers,
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=45) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            texto = data.get("choices", [{}])[0].get("message", {}).get("content", "")
            if texto:
                self.ultimo_modelo_usado = mod
                self._modelos_agotados.discard(mod)
                return texto
        return None

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
        return bool(get_gemini_api_key() or get_openrouter_api_key() or get_groq_api_key())

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
        modelo_preferido: Optional[str] = None,
    ) -> Optional[str]:
        """Genera una respuesta completa a partir del prompt proporcionado."""
        client = self._obtener_cliente()
        sys_inst = system_instruction or SYSTEM_PROMPT_TUTOR
        modelos = self._obtener_lista_modelos(modelo_preferido)

        for mod in modelos:
            try:
                if mod.startswith(("openrouter:", "groq:")):
                    if response_mime_type == "application/json":
                        continue
                    res_oa = self._llamar_openai_compatible(
                        mod, [{"role": "user", "text": prompt}], sys_inst, temperatura
                    )
                    if res_oa:
                        return res_oa
                    continue

                if not client:
                    continue

                es_gemma = mod.startswith("gemma-")
                config_args: Dict[str, Any] = {"temperature": temperatura}
                if not es_gemma:
                    config_args["system_instruction"] = sys_inst
                if max_output_tokens:
                    config_args["max_output_tokens"] = max_output_tokens
                if response_mime_type and not es_gemma:
                    config_args["response_mime_type"] = response_mime_type

                prompt_final = f"{sys_inst}\n\n{prompt}" if es_gemma else prompt
                response = client.models.generate_content(
                    model=mod,
                    contents=prompt_final,
                    config=types.GenerateContentConfig(**config_args),
                )
                if response and response.text:
                    self.ultimo_modelo_usado = mod
                    self._modelos_agotados.discard(mod)
                    return response.text
            except Exception as e:
                self._registrar_error_modelo(mod, e)
                self._log_seguro(f"[dim]Reintentando con modelo de respaldo tras error en {mod}: {e}[/dim]")
                continue

        self._log_seguro("[bold red]❌ Error de comunicación con los modelos de IA disponibles.[/bold red]")
        return None

    def _normalizar_mensajes(self, mensajes: List[Dict[str, str]]) -> List[Dict[str, str]]:
        norm = []
        for m in mensajes:
            rol = m.get("role") or m.get("rol", "user")
            texto = m.get("text") or m.get("texto", "")
            if not texto:
                continue
            if rol not in ["user", "model"]:
                rol = "user" if str(rol).lower() in ["alumno", "human", "usuario", "user"] else "model"
            if norm and norm[-1]["role"] == rol:
                norm[-1]["text"] += "\n\n" + texto
            else:
                norm.append({"role": rol, "text": texto})
        return norm

    def _construir_contents_gemini(
        self, mensajes_norm: List[Dict[str, str]], sys_inst: str, es_gemma: bool
    ) -> List[types.Content]:
        contents = []
        for idx, m in enumerate(mensajes_norm):
            texto = m["text"]
            if es_gemma and idx == len(mensajes_norm) - 1 and m["role"] == "user":
                texto = f"[INSTRUCCIONES DE SISTEMA]:\n{sys_inst}\n\n[MENSAJE DEL ALUMNO]:\n{texto}"
            contents.append(
                types.Content(
                    role=m["role"],
                    parts=[types.Part.from_text(text=texto)],
                )
            )
        return contents

    def generar_chat(
        self,
        mensajes: List[Dict[str, str]],
        system_instruction: Optional[str] = None,
        temperatura: float = 0.4,
        modelo_preferido: Optional[str] = None,
    ) -> Optional[str]:
        """Genera una respuesta en un diálogo multi-turno manteniendo la memoria contextual exacta."""
        client = self._obtener_cliente()
        sys_inst = system_instruction or SYSTEM_PROMPT_TUTOR
        mensajes_norm = self._normalizar_mensajes(mensajes)
        if not mensajes_norm:
            return None

        modelos = self._obtener_lista_modelos(modelo_preferido)

        for mod in modelos:
            try:
                if mod.startswith(("openrouter:", "groq:")):
                    res_oa = self._llamar_openai_compatible(mod, mensajes_norm, sys_inst, temperatura)
                    if res_oa:
                        return res_oa
                    continue

                if not client:
                    continue

                es_gemma = mod.startswith("gemma-")
                config = (
                    types.GenerateContentConfig(temperature=temperatura)
                    if es_gemma
                    else types.GenerateContentConfig(system_instruction=sys_inst, temperature=temperatura)
                )
                contents = self._construir_contents_gemini(mensajes_norm, sys_inst, es_gemma)
                response = client.models.generate_content(
                    model=mod,
                    contents=contents,
                    config=config,
                )
                if response and response.text:
                    self.ultimo_modelo_usado = mod
                    self._modelos_agotados.discard(mod)
                    return response.text
            except Exception as e:
                self._registrar_error_modelo(mod, e)
                self._log_seguro(f"[dim]Reintentando chat con modelo de respaldo tras error en {mod}: {e}[/dim]")
                continue

        self._log_seguro("[bold red]❌ Error de comunicación en chat multi-turno.[/bold red]")
        return None

    def generar_chat_stream(
        self,
        mensajes: List[Dict[str, str]],
        system_instruction: Optional[str] = None,
        temperatura: float = 0.4,
        modelo_preferido: Optional[str] = None,
    ) -> Generator[str, None, None]:
        """Genera una respuesta en streaming en un diálogo multi-turno con respaldo automático de modelos."""
        client = self._obtener_cliente()
        sys_inst = system_instruction or SYSTEM_PROMPT_TUTOR
        mensajes_norm = self._normalizar_mensajes(mensajes)
        if not mensajes_norm:
            return

        modelos = self._obtener_lista_modelos(modelo_preferido)

        for mod in modelos:
            emitido = False
            try:
                if mod.startswith(("openrouter:", "groq:")):
                    res_oa = self._llamar_openai_compatible(mod, mensajes_norm, sys_inst, temperatura)
                    if res_oa:
                        self.ultimo_modelo_usado = mod
                        yield res_oa
                        return
                    continue

                if not client:
                    continue

                es_gemma = mod.startswith("gemma-")
                config = (
                    types.GenerateContentConfig(temperature=temperatura)
                    if es_gemma
                    else types.GenerateContentConfig(system_instruction=sys_inst, temperature=temperatura)
                )
                contents = self._construir_contents_gemini(mensajes_norm, sys_inst, es_gemma)
                response_stream = client.models.generate_content_stream(
                    model=mod,
                    contents=contents,
                    config=config,
                )
                for chunk in response_stream:
                    if chunk.text:
                        if not emitido:
                            emitido = True
                            self.ultimo_modelo_usado = mod
                            self._modelos_agotados.discard(mod)
                        yield chunk.text
                if emitido:
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
