"""
Sistema de Flashcards y Repetición Espaciada (Spaced Repetition / Anki) para UNIR Study Hub.
Permite memorizar conceptos clave de FP DAM, Ciberseguridad y Grado con tarjetas de estudio activas,
calificación de dificultad y generación automática mediante IA.
"""

import json
from pathlib import Path
from typing import List, Dict, Any, Optional

from src.core.config import PROCESADO_DIR, ensure_directories
from src.ai.tutor import TutorIA

FLASHCARDS_DIR = PROCESADO_DIR / "flashcards"
FLASHCARDS_DATA_FILE = FLASHCARDS_DIR / "mazos.json"


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
    """Gestiona el almacenamiento, generación y estado de repetición espaciada de las flashcards."""

    def __init__(self):
        ensure_directories()
        FLASHCARDS_DIR.mkdir(parents=True, exist_ok=True)
        self.mazos = self._cargar_o_inicializar()

    def _cargar_o_inicializar(self) -> Dict[str, List[Dict[str, Any]]]:
        if FLASHCARDS_DATA_FILE.exists():
            try:
                return json.loads(FLASHCARDS_DATA_FILE.read_text(encoding="utf-8"))
            except Exception:
                pass
        self._guardar(MAZOS_PREDETERMINADOS)
        return MAZOS_PREDETERMINADOS

    def _guardar(self, data: Dict[str, List[Dict[str, Any]]]):
        FLASHCARDS_DATA_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    def obtener_tarjetas(self, programa_id: str = "dam", asignatura: Optional[str] = None) -> List[Dict[str, Any]]:
        tarjetas = self.mazos.get(programa_id, [])
        if asignatura:
            tarjetas = [t for t in tarjetas if t.get("asignatura") == asignatura]
        return tarjetas

    def calificar_tarjeta(self, programa_id: str, tarjeta_id: str, dificultad: str) -> Dict[str, Any]:
        """
        Registra la calificación del usuario:
        - facil: aprendida (+10 puntos)
        - medio: en repaso (+5 puntos)
        - dificil: requiere refuerzo (0 puntos, repetir hoy)
        """
        for t in self.mazos.get(programa_id, []):
            if t["id"] == tarjeta_id:
                t["estado"] = dificultad
                t["repasos"] = t.get("repasos", 0) + 1
                self._guardar(self.mazos)
                return t
        return {}

    def generar_flashcards_con_ia(self, texto_tema: str, asignatura: str, programa_id: str = "dam", cantidad: int = 4) -> List[Dict[str, Any]]:
        """Genera tarjetas de estudio a partir del texto de un tema mediante Gemini."""
        tutor = TutorIA()
        prompt = (
            f"A partir del siguiente contenido académico de '{asignatura}':\n\n"
            f"{texto_tema[:18000]}\n\n"
            f"Genera exactamente {cantidad} Flashcards de alta calidad para repasar conceptos clave de examen.\n"
            "Devuelve ÚNICAMENTE un array JSON válido con la siguiente estructura por elemento:\n"
            "[\n"
            "  {\n"
            "    \"anverso\": \"¿Pregunta o concepto clave?\",\n"
            "    \"reverso\": \"Respuesta concisa, precisa y con el término técnico destacado en negrita.\",\n"
            "    \"nivel\": \"básico\" | \"intermedio\" | \"avanzado\"\n"
            "  }\n"
            "]"
        )
        try:
            resp = tutor.client.generar_contenido(prompt, temperatura=0.3)
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
                            "repasos": 0
                        }
                        creadas.append(item)
                    if programa_id not in self.mazos:
                        self.mazos[programa_id] = []
                    self.mazos[programa_id].extend(creadas)
                    self._guardar(self.mazos)
                    return creadas
        except Exception:
            pass
        return []
