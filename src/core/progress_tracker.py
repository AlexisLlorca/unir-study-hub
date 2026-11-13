"""
Sistema de Seguimiento de Progreso y Calificaciones Académicas para UNIR Study Hub.
Registra el historial de tests y simulacros de examen realizados por el alumno,
calcula la nota media y genera el 'Semáforo de Examen' por tema y asignatura.
"""

import json
import threading
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Any, Optional

from rich.panel import Panel
from rich.table import Table

from src.core.config import console, PROCESADO_DIR, ensure_directories, escribir_json_atomico, obtener_rutas_usuario
from src.core.academic_structure import ASIGNATURAS_2DAM

PROGRESO_FILE = PROCESADO_DIR / "progreso.json"
_PROGRESO_LOCK = threading.Lock()


class GestorProgreso:
    """Gestiona el expediente y registro de notas de autoevaluación del alumno (aislado por usuario)."""

    def __init__(self, username: Optional[str] = None):
        ensure_directories()
        self.username = username or "alexking44"
        self.archivo_progreso = obtener_rutas_usuario(self.username)["progreso_file"]
        self._inicializar()

    def _inicializar(self):
        with _PROGRESO_LOCK:
            if not self.archivo_progreso.exists():
                datos_iniciales = {
                    "historial_tests": [],
                    "ultimas_calificaciones": {},
                    "preguntas_falladas": [],
                    "fecha_actualizacion": datetime.now().isoformat(),
                }
                escribir_json_atomico(self.archivo_progreso, datos_iniciales)

    def _cargar(self) -> Dict[str, Any]:
        try:
            with open(self.archivo_progreso, "r", encoding="utf-8") as f:
                data = json.load(f)
                data.setdefault("preguntas_falladas", [])
                return data
        except Exception:
            return {"historial_tests": [], "ultimas_calificaciones": {}, "preguntas_falladas": []}

    def _guardar(self, datos: Dict[str, Any]):
        datos["fecha_actualizacion"] = datetime.now().isoformat()
        escribir_json_atomico(self.archivo_progreso, datos)
        try:
            from src.core.cloud_sync import respaldar_archivo_en_hf_async
            respaldar_archivo_en_hf_async(self.archivo_progreso)
        except Exception:
            pass

    def registrar_resultado_test(
        self,
        asignatura: str,
        tema: str,
        nota: float,
        aciertos: int,
        total: int,
        preguntas_falladas: Optional[List[Dict[str, Any]]] = None,
        enunciados_acertados: Optional[List[str]] = None,
    ):
        """Registra el resultado de un examen tipo test y actualiza el banco de preguntas falladas."""
        with _PROGRESO_LOCK:
            datos = self._cargar()
            fecha_str = datetime.now().strftime("%Y-%m-%d %H:%M")

            registro = {
                "asignatura": asignatura,
                "tema": tema,
                "nota": round(nota, 2),
                "aciertos": aciertos,
                "total_preguntas": total,
                "fecha": fecha_str,
            }

            datos.setdefault("historial_tests", []).append(registro)

            # Clave única para la última calificación por tema
            clave = f"{asignatura}::{tema}"
            datos.setdefault("ultimas_calificaciones", {})[clave] = {
                "nota": round(nota, 2),
                "aciertos": aciertos,
                "total": total,
                "fecha": fecha_str,
            }

            banco_fallos: List[Dict[str, Any]] = datos.setdefault("preguntas_falladas", [])

            # 1. Si el alumno acertó preguntas que estaban en el banco de fallos, eliminarlas (¡superadas!)
            if enunciados_acertados:
                acertadas_norm = {e.strip().lower() for e in enunciados_acertados if e}
                banco_fallos = [
                    f for f in banco_fallos
                    if not (
                        f.get("asignatura") == asignatura
                        and str(f.get("enunciado", "")).strip().lower() in acertadas_norm
                    )
                ]

            # 2. Añadir o actualizar preguntas falladas en este intento
            if preguntas_falladas:
                index_por_clave = {
                    (f.get("asignatura", ""), str(f.get("enunciado", "")).strip().lower()): idx
                    for idx, f in enumerate(banco_fallos)
                }
                for pf in preguntas_falladas:
                    enun = str(pf.get("enunciado", "")).strip()
                    if not enun or not pf.get("opciones") or not pf.get("correcta"):
                        continue
                    k = (asignatura, enun.lower())
                    item_fallo = {
                        "asignatura": asignatura,
                        "tema": pf.get("tema") or tema,
                        "enunciado": enun,
                        "opciones": pf["opciones"],
                        "correcta": pf["correcta"],
                        "pista": pf.get("pista", ""),
                        "justificacion": pf.get("justificacion", ""),
                        "respuesta_erronea": pf.get("respuesta_erronea", ""),
                        "fecha": fecha_str,
                    }
                    if k in index_por_clave:
                        banco_fallos[index_por_clave[k]] = item_fallo
                    else:
                        index_por_clave[k] = len(banco_fallos)
                        banco_fallos.append(item_fallo)

            datos["preguntas_falladas"] = banco_fallos
            self._guardar(datos)

    def obtener_preguntas_falladas(self, asignatura: Optional[str] = None) -> List[Dict[str, Any]]:
        """Devuelve las preguntas pendientes en el banco de fallos, opcionalmente filtradas por asignatura."""
        datos = self._cargar()
        fallos = datos.get("preguntas_falladas", [])
        if asignatura:
            return [f for f in fallos if f.get("asignatura") == asignatura]
        return fallos

    def obtener_resumen_asignatura(self, asignatura: str) -> Dict[str, Any]:
        """Calcula estadísticas para una asignatura específica."""
        datos = self._cargar()
        calificaciones = datos.get("ultimas_calificaciones", {})
        
        notas_asig = [
            info["nota"]
            for clave, info in calificaciones.items()
            if clave.startswith(f"{asignatura}::")
        ]

        if not notas_asig:
            return {"total_evaluados": 0, "nota_media": 0.0, "estado": "Sin evaluar"}

        media = sum(notas_asig) / len(notas_asig)
        return {
            "total_evaluados": len(notas_asig),
            "nota_media": round(media, 2),
            "estado": "🟢 Dominado" if media >= 8.0 else ("🟡 Aprobado" if media >= 5.0 else "🔴 Repasar"),
        }

    def obtener_temas_debiles(self, limite: int = 5) -> List[Dict[str, Any]]:
        """
        Devuelve los temas que más conviene reforzar, combinando dos señales:
        - la última nota del test (por debajo de 6.5)
        - el número de preguntas falladas pendientes en el banco de fallos

        Así el entrenador no solo mira "qué suspendiste", sino también dónde repites errores.
        """
        datos = self._cargar()
        calificaciones = datos.get("ultimas_calificaciones", {}) or {}
        fallos = datos.get("preguntas_falladas", []) or []

        # 1. Partimos de los temas que ya tienen una nota registrada.
        mapa: Dict[tuple, Dict[str, Any]] = {}
        for clave, info in calificaciones.items():
            if not isinstance(info, dict) or "::" not in str(clave):
                continue
            asignatura, tema = str(clave).split("::", 1)
            try:
                nota = float(info.get("nota", 0) or 0)
            except (TypeError, ValueError):
                nota = 0.0
            mapa[(asignatura, tema)] = {
                "asignatura": asignatura,
                "tema": tema,
                "nota": round(nota, 2),
                "fecha": info.get("fecha", ""),
                "fallos": 0,
            }

        # 2. Sumamos las preguntas falladas que siguen pendientes.
        for fallo in fallos:
            if not isinstance(fallo, dict):
                continue
            asignatura = str(fallo.get("asignatura") or "").strip()
            tema = str(fallo.get("tema") or "").strip()
            if not asignatura or not tema:
                continue
            clave = (asignatura, tema)
            if clave not in mapa:
                mapa[clave] = {
                    "asignatura": asignatura,
                    "tema": tema,
                    "nota": None,
                    "fecha": "",
                    "fallos": 0,
                }
            mapa[clave]["fallos"] += 1

        # 3. Filtramos lo que realmente merece atención.
        debiles: List[Dict[str, Any]] = []
        for item in mapa.values():
            nota = item["nota"]
            if (nota is not None and nota < 6.5) or item["fallos"] >= 2:
                if nota is None:
                    item["estado"] = "Reforzar"
                elif nota < 5:
                    item["estado"] = "Repasar"
                else:
                    item["estado"] = "Reforzar"
                debiles.append(item)

        # 4. Primero las notas más bajas; a igualdad, más fallos acumulados.
        debiles.sort(
            key=lambda x: (
                x["nota"] if x["nota"] is not None else 5.0,
                -x["fallos"],
            )
        )
        return debiles[:limite]

    def mostrar_dashboard_progreso(self):
        """Muestra en terminal una vista analítica del expediente académico."""
        datos = self._cargar()
        calificaciones = datos.get("ultimas_calificaciones", {})
        historial = datos.get("historial_tests", [])

        console.print(
            Panel.fit(
                "[bold cyan]📊 EXPEDIENTE Y SEMÁFORO DE EXAMEN — 2º DAM[/bold cyan]\n"
                "[dim]Seguimiento de preparación continua para los exámenes oficiales.[/dim]",
                border_style="cyan",
            )
        )

        table = Table(title="📈 Estado de Preparación por Asignatura", border_style="cyan")
        table.add_column("Asignatura", style="bold white")
        table.add_column("Tests Realizados", style="cyan", justify="right")
        table.add_column("Nota Media", style="yellow", justify="right")
        table.add_column("Semáforo", style="bold")

        todas_las_notas = []

        for asig in ASIGNATURAS_2DAM:
            resumen = self.obtener_resumen_asignatura(asig)
            if resumen["total_evaluados"] > 0:
                todas_las_notas.append(resumen["nota_media"])
                nota_str = f"{resumen['nota_media']:.1f} / 10"
            else:
                nota_str = "-"

            table.add_row(
                asig,
                f"{resumen['total_evaluados']} tema(s)",
                nota_str,
                resumen["estado"],
            )

        console.print(table)

        if todas_las_notas:
            media_global = sum(todas_las_notas) / len(todas_las_notas)
            color = "green" if media_global >= 7.0 else ("yellow" if media_global >= 5.0 else "red")
            console.print(f"\n[bold]Nota Media Global Estimada:[/bold] [{color}]{media_global:.2f} / 10[/{color}] en {len(historial)} tests totales realizados.\n")
        else:
            console.print("\n[dim]Aún no has completado ningún test. Practica en la Opción 2 o dentro del Navegador.[/dim]\n")
