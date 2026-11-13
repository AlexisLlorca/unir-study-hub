"""
Sistema de Flashcards y Repetición Espaciada (Spaced Repetition / Anki) para UNIR Study Hub.
Permite memorizar conceptos clave de FP DAM, Ciberseguridad y Grado con tarjetas de estudio activas,
calificación de dificultad y generación automática mediante IA.
"""

import copy
import json
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Dict, Any, Optional

from src.core.config import PROCESADO_DIR, ensure_directories, escribir_json_atomico, obtener_rutas_usuario
from src.ai.tutor import TutorIA

FLASHCARDS_DIR = PROCESADO_DIR / "flashcards"
FLASHCARDS_DATA_FILE = FLASHCARDS_DIR / "mazos.json"
_FLASHCARDS_LOCK = threading.Lock()

# Caché por fecha de modificación: los mazos son grandes y se leen en cada petición.
_CACHE_MAZOS: Dict[str, tuple] = {}
_CACHE_MAZOS_LOCK = threading.Lock()


def _firma_archivo(ruta: Path):
    """Devuelve (mtime_ns, tamaño) para detectar cambios sin abrir el fichero."""
    try:
        st = ruta.stat()
        return (st.st_mtime_ns, st.st_size)
    except OSError:
        return None

# --- Motor de repetición espaciada (SM-2 simplificado) ---------------------
# Cada tarjeta guarda: facilidad, repeticiones, intervalo_dias y proximo_repaso.
# La calidad se puntúa de 1 a 5: 1 = difícil, 3 = regular, 5 = fácil.
_CALIDAD_POR_DIFICULTAD = {"dificil": 1, "medio": 3, "facil": 5}
_INTERVALO_MAXIMO_DIAS = 180


def _ahora_utc() -> datetime:
    """Devuelve la hora actual en UTC (con zona horaria) para comparar fechas sin líos."""
    return datetime.now(timezone.utc)


def _iso_utc(momento: datetime) -> str:
    """Convierte un datetime a texto ISO 8601, que es fácil de guardar en JSON."""
    return momento.astimezone(timezone.utc).isoformat(timespec="seconds")


def _parsear_fecha(valor: Any) -> Optional[datetime]:
    """Convierte el texto guardado en la tarjeta a datetime. Si no se puede, devuelve None."""
    if not valor:
        return None
    try:
        fecha = datetime.fromisoformat(str(valor).replace("Z", "+00:00"))
        if fecha.tzinfo is None:
            # Las fechas antiguas no tenían zona horaria: las tratamos como UTC.
            fecha = fecha.replace(tzinfo=timezone.utc)
        return fecha.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


# Barajas iniciales con conceptos clave de alta frecuencia de examen
MAZOS_PREDETERMINADOS = {
    "dam": [
        {
            "id": "dam_1",
            "asignatura": "Programación de Servicios y Procesos",
            "anverso": "¿Qué diferencia existe entre un Proceso y un Hilo (Thread)?",
            "reverso": "Un **proceso** es un programa en ejecución con su propio espacio de memoria aislado y recursos independientes. Un **hilo** es una unidad de ejecución ligera dentro de un proceso que comparte memoria y recursos con otros hilos del mismo proceso.",
            "nivel": "básico",
            "estado": "nuevo"
        },
        {
            "id": "dam_2",
            "asignatura": "Programación de Servicios y Procesos",
            "anverso": "¿Qué ocurre si no sincronizas una variable compartida modificada por múltiples hilos?",
            "reverso": "Se produce una **condición de carrera (Race Condition)** e inconsistencia de datos, ya que las operaciones de lectura/escritura no son atómicas. Se soluciona usando `synchronized`, `ReentrantLock` o clases atómicas (`AtomicInteger`).",
            "nivel": "intermedio",
            "estado": "nuevo"
        },
        {
            "id": "dam_3",
            "asignatura": "Acceso a Datos",
            "anverso": "¿Qué es la anotación `@Entity` y `@Id` en JPA / Hibernate?",
            "reverso": "`@Entity` indica que la clase Java se mapea a una tabla de la base de datos relacional. `@Id` define la clave primaria (Primary Key) de dicha entidad.",
            "nivel": "básico",
            "estado": "nuevo"
        },
        {
            "id": "dam_4",
            "asignatura": "Acceso a Datos",
            "anverso": "¿Cuál es la diferencia entre `FetchType.LAZY` y `FetchType.EAGER`?",
            "reverso": "**LAZY:** Los datos relacionados solo se cargan de la BD cuando se accede a ellos explícitamente (ahorra memoria). **EAGER:** Se cargan inmediatamente junto con la entidad principal mediante un JOIN (riesgo de problemas de rendimiento N+1).",
            "nivel": "avanzado",
            "estado": "nuevo"
        },
        {
            "id": "dam_5",
            "asignatura": "Programación Multimedia y Dispositivos Móviles",
            "anverso": "¿Cuál es el ciclo de vida de un `Activity` en Android desde que se abre hasta que pasa a segundo plano?",
            "reverso": "`onCreate()` ➔ `onStart()` ➔ `onResume()` (activa). Si pasa a segundo plano: `onPause()` ➔ `onStop()`. Si el sistema la destruye por memoria: `onDestroy()`.",
            "nivel": "intermedio",
            "estado": "nuevo"
        },
        {
            "id": "dam_6",
            "asignatura": "Proyecto Intermodular DAM",
            "anverso": "¿Cuáles son las fases y entregas oficiales del Proyecto Intermodular?",
            "reverso": "1. **Anteproyecto** (propuesta y equipo).\n2. **3 Entregas mensuales** de avance y código.\n3. **Depósito final** (70% de la nota, rúbrica oficial).\n4. **Defensa oral ante tribunal** (30% de la nota).",
            "nivel": "básico",
            "estado": "nuevo"
        }
    ],
    "ciber": [
        {
            "id": "ciber_1",
            "asignatura": "M2 - Hacking Ético y Auditorías",
            "anverso": "¿En qué consiste un ataque de Inyección SQL (SQLi) y cómo se previene al 100%?",
            "reverso": "Consiste en insertar sentencias SQL no autorizadas en campos de entrada del usuario para alterar la consulta enviada a la BD. Se previene usando **consultas preparadas (Prepared Statements / Parámetros)** y ORMs seguros.",
            "nivel": "básico",
            "estado": "nuevo"
        },
        {
            "id": "ciber_2",
            "asignatura": "M4 - Tecnologías SIEM y Operativa SOC",
            "anverso": "¿Qué es la regla de correlación en un SIEM (ej. Splunk, Elastic, QRadar)?",
            "reverso": "Es una regla lógica que analiza flujos heterogéneos de eventos y logs en tiempo real para disparar una alerta cuando se cumplen patrones de amenaza (ej: 10 intentos fallidos de SSH seguidos de un login root exitoso).",
            "nivel": "intermedio",
            "estado": "nuevo"
        },
        {
            "id": "ciber_3",
            "asignatura": "M5 - Ingeniería Inversa y Malware",
            "anverso": "¿Qué diferencia hay entre el análisis estático y el análisis dinámico de malware?",
            "reverso": "**Estático:** Se examina el binario sin ejecutarlo (strings, hashes, desensamblado con Ghidra/IDA, headers PE). **Dinámico:** Se ejecuta en un entorno aislado (Sandbox/Cuckoo) para observar comportamiento en red, memoria y modificaciones en el registro.",
            "nivel": "intermedio",
            "estado": "nuevo"
        }
    ]
}


class GestorFlashcards:
    """Gestiona el almacenamiento, generación y estado de repetición espaciada de las flashcards (aislado por usuario)."""

    def __init__(self, username: Optional[str] = None):
        ensure_directories()
        self.username = username or "alexking44"
        self.archivo_datos = obtener_rutas_usuario(self.username)["flashcards_file"]
        with _FLASHCARDS_LOCK:
            self.mazos = self._cargar_o_inicializar()

    def _normalizar_tarjeta(self, tarjeta: Dict[str, Any]) -> bool:
        """Añade los campos de repetición espaciada a tarjetas antiguas. Devuelve True si cambió algo."""
        cambiada = False
        valores_por_defecto = {
            "facilidad": 2.5,
            "repeticiones": 0,
            "intervalo_dias": 0,
            "proximo_repaso": _iso_utc(_ahora_utc()),
            "ultima_revision": None,
        }
        for campo, valor in valores_por_defecto.items():
            if campo not in tarjeta:
                tarjeta[campo] = valor
                cambiada = True
        return cambiada

    def _normalizar_mazos(self, mazos: Dict[str, List[Dict[str, Any]]]) -> bool:
        """Normaliza todas las tarjetas de todos los mazos."""
        cambiado = False
        for tarjetas in mazos.values():
            if not isinstance(tarjetas, list):
                continue
            for tarjeta in tarjetas:
                if isinstance(tarjeta, dict) and self._normalizar_tarjeta(tarjeta):
                    cambiado = True
        return cambiado

    def _guardar_en_cache(self, mazos: Dict[str, List[Dict[str, Any]]]) -> None:
        """Guarda una copia en caché junto a la firma del fichero recién escrito."""
        firma = _firma_archivo(self.archivo_datos)
        if firma is None:
            return
        with _CACHE_MAZOS_LOCK:
            _CACHE_MAZOS[str(self.archivo_datos)] = (
                firma[0],
                firma[1],
                copy.deepcopy(mazos),
            )

    def _cargar_o_inicializar(self) -> Dict[str, List[Dict[str, Any]]]:
        clave = str(self.archivo_datos)
        firma = _firma_archivo(self.archivo_datos)
        if firma is not None:
            with _CACHE_MAZOS_LOCK:
                cache = _CACHE_MAZOS.get(clave)
                if cache and cache[0] == firma[0] and cache[1] == firma[1]:
                    # Copia para que nadie modifique la caché compartida por accidente.
                    return copy.deepcopy(cache[2])
        if self.archivo_datos.exists():
            try:
                mazos = json.loads(self.archivo_datos.read_text(encoding="utf-8"))
                # Migración transparente: las tarjetas antiguas pasan a tener SRS.
                if self._normalizar_mazos(mazos):
                    self._guardar(mazos)
                else:
                    self._guardar_en_cache(mazos)
                return mazos
            except Exception:
                pass
        inicial = copy.deepcopy(MAZOS_PREDETERMINADOS)
        self._normalizar_mazos(inicial)
        self._guardar(inicial)
        return inicial

    def _guardar(self, data: Dict[str, List[Dict[str, Any]]]):
        escribir_json_atomico(self.archivo_datos, data)
        self._guardar_en_cache(data)
        try:
            from src.core.cloud_sync import respaldar_archivo_en_hf_async
            respaldar_archivo_en_hf_async(self.archivo_datos)
        except Exception:
            pass

    def obtener_tarjetas(self, programa_id: str = "dam", asignatura: Optional[str] = None) -> List[Dict[str, Any]]:
        tarjetas = self.mazos.get(programa_id, [])
        if asignatura:
            tarjetas = [t for t in tarjetas if t.get("asignatura") == asignatura]
        return tarjetas

    def revisar_tarjeta(self, programa_id: str, tarjeta_id: str, dificultad: str) -> Dict[str, Any]:
        """
        Aplica el algoritmo SM-2 simplificado y calcula cuándo hay que volver a ver la tarjeta.

        - dificil: la tarjeta vuelve en 10 minutos y se reinicia la racha.
        - medio:   primer repaso al día siguiente, luego a los 6 días.
        - facil:   los intervalos crecen multiplicando por la facilidad de la tarjeta.
        """
        calidad = _CALIDAD_POR_DIFICULTAD.get((dificultad or "").strip().lower(), 3)
        ahora = _ahora_utc()

        with _FLASHCARDS_LOCK:
            for t in self.mazos.get(programa_id, []):
                if t.get("id") != tarjeta_id:
                    continue

                self._normalizar_tarjeta(t)
                facilidad = float(t.get("facilidad", 2.5))
                repeticiones = int(t.get("repeticiones", 0))
                intervalo = int(t.get("intervalo_dias", 0))

                if calidad < 3:
                    # Fallo: se reinicia la racha y se repite pronto (10 minutos).
                    repeticiones = 0
                    intervalo = 0
                    proximo = ahora + timedelta(minutes=10)
                    t["estado"] = "dificil"
                else:
                    repeticiones += 1
                    if repeticiones == 1:
                        intervalo = 1
                    elif repeticiones == 2:
                        intervalo = 6
                    else:
                        intervalo = max(1, round(intervalo * facilidad))
                    intervalo = min(intervalo, _INTERVALO_MAXIMO_DIAS)
                    proximo = ahora + timedelta(days=intervalo)
                    t["estado"] = "facil" if intervalo >= 21 else "medio"

                # Fórmula clásica de SM-2 para ajustar la facilidad de la tarjeta.
                facilidad = max(
                    1.3,
                    facilidad + (0.1 - (5 - calidad) * (0.08 + (5 - calidad) * 0.02)),
                )

                t["facilidad"] = round(facilidad, 2)
                t["repeticiones"] = repeticiones
                t["intervalo_dias"] = intervalo
                t["proximo_repaso"] = _iso_utc(proximo)
                t["ultima_revision"] = _iso_utc(ahora)
                t["ultima_dificultad"] = dificultad
                t["repasos"] = int(t.get("repasos", 0)) + 1

                self._guardar(self.mazos)
                return dict(t)
        return {}

    def calificar_tarjeta(self, programa_id: str, tarjeta_id: str, dificultad: str) -> Dict[str, Any]:
        """Se mantiene el nombre antiguo para no romper el frontend; ahora delega en el motor SRS."""
        return self.revisar_tarjeta(programa_id, tarjeta_id, dificultad)

    def tarjetas_vencidas(self, programa_id: str = "dam", limite: int = 50) -> List[Dict[str, Any]]:
        """Devuelve las tarjetas cuyo próximo repaso ya ha llegado (incluye las nuevas)."""
        ahora = _ahora_utc()
        vencidas = []
        for t in self.mazos.get(programa_id, []):
            if not isinstance(t, dict):
                continue
            proximo = _parsear_fecha(t.get("proximo_repaso")) or ahora
            if proximo <= ahora:
                vencidas.append(t)

        vencidas.sort(key=lambda t: _parsear_fecha(t.get("proximo_repaso")) or ahora)
        return vencidas[:limite]

    def estadisticas(self, programa_id: str = "dam") -> Dict[str, int]:
        """Resumen para el entrenador: vencidas, nuevas, dominadas y repasos de los próximos 7 días."""
        tarjetas = [t for t in self.mazos.get(programa_id, []) if isinstance(t, dict)]
        ahora = _ahora_utc()
        dentro_de_7_dias = ahora + timedelta(days=7)

        vencidas = 0
        nuevas = 0
        dominadas = 0
        proximas_7_dias = 0

        for t in tarjetas:
            self._normalizar_tarjeta(t)
            proximo = _parsear_fecha(t.get("proximo_repaso")) or ahora
            if not t.get("ultima_revision"):
                nuevas += 1
            if proximo <= ahora:
                vencidas += 1
            elif proximo <= dentro_de_7_dias:
                proximas_7_dias += 1
            if int(t.get("intervalo_dias", 0)) >= 21:
                dominadas += 1

        return {
            "total": len(tarjetas),
            "vencidas": vencidas,
            "nuevas": nuevas,
            "dominadas": dominadas,
            "proximas_7_dias": proximas_7_dias,
        }

    def generar_flashcards_con_ia(
        self,
        texto_tema: str,
        asignatura: str,
        programa_id: str = "dam",
        cantidad: int = 4,
        api_key_override: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Genera tarjetas de estudio a partir del texto de un tema mediante Gemini."""
        tutor = TutorIA()
        cuerpo = f"Tema o concepto clave a reforzar: '{texto_tema}' de la asignatura '{asignatura}'." if len(texto_tema.strip()) < 150 else f"A partir del siguiente contenido académico de '{asignatura}':\n\n{texto_tema[:20000]}"
        prompt = (
            f"Eres NOVA, mentora académica de élite en '{asignatura}' para FP DAM y titulaciones superiores.\n"
            f"{cuerpo}\n\n"
            f"REGLA DE ORO DE VERACIDAD: Basa todas las preguntas y respuestas ESTRICTAMENTE en el contenido oficial indicado arriba. PROHIBIDO inventar conceptos o terminología ajena al temario.\n\n"
            f"Genera exactamente {cantidad} Flashcards de alta calidad pedagógica orientadas a preparar exámenes oficiales.\n"
            "Cada tarjeta debe tener:\n"
            "- 'anverso': Una pregunta clara, caso práctico o concepto que suele caer en examen.\n"
            "- 'reverso': Respuesta concisa y técnicamente rigurosa con los términos clave destacados en **negrita**.\n"
            "- 'nivel': 'básico' | 'intermedio' | 'avanzado'.\n\n"
            "Devuelve ÚNICAMENTE un array JSON válido con la siguiente estructura por elemento:\n"
            "[\n"
            "  {\n"
            "    \"anverso\": \"¿Pregunta o concepto clave?\",\n"
            "    \"reverso\": \"Explicación concisa con término en **negrita**.\",\n"
            "    \"nivel\": \"básico\" | \"intermedio\" | \"avanzado\"\n"
            "  }\n"
            "]"
        )
        try:
            resp = tutor.client.generar_contenido(
                prompt,
                temperatura=0.3,
                response_mime_type="application/json",
                api_key_override=api_key_override,
            )
            if resp:
                limpio = resp.strip()
                if limpio.startswith("```"):
                    limpio = limpio.split("```")[1]
                    if limpio.startswith("json"):
                        limpio = limpio[4:]
                nuevas = json.loads(limpio.strip())
                if isinstance(nuevas, list):
                    import uuid
                    creadas = []
                    for n in nuevas:
                        item = {
                            "id": f"card_{uuid.uuid4().hex[:8]}",
                            "asignatura": asignatura,
                            "anverso": n.get("anverso", ""),
                            "reverso": n.get("reverso", ""),
                            "nivel": n.get("nivel", "intermedio"),
                            "estado": "nuevo",
                            "repasos": 0,
                            # Campos de repetición espaciada: una tarjeta nueva vence ahora mismo.
                            "facilidad": 2.5,
                            "repeticiones": 0,
                            "intervalo_dias": 0,
                            "proximo_repaso": _iso_utc(_ahora_utc()),
                            "ultima_revision": None,
                        }
                        creadas.append(item)
                    with _FLASHCARDS_LOCK:
                        if programa_id not in self.mazos:
                            self.mazos[programa_id] = []
                        self.mazos[programa_id].extend(creadas)
                        self._guardar(self.mazos)
                    return creadas
        except Exception:
            pass
        return []
