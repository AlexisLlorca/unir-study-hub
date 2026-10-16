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

from src.core.config import console, PROCESADO_DIR, ensure_directories, escribir_json_atomico
from src.core.academic_structure import ASIGNATURAS_2DAM

PROGRESO_FILE = PROCESADO_DIR / "progreso.json"
_PROGRESO_LOCK = threading.Lock()


class GestorProgreso:
    """Gestiona el expediente y registro de notas de autoevaluación del alumno."""

    def __init__(self):
        ensure_directories()
        self.archivo_progreso = PROGRESO_FILE
        self._inicializar()

    def _inicializar(self):
        with _PROGRESO_LOCK:
            if not self.archivo_progreso.exists():
                datos_iniciales = {
                    "historial_tests": [],
                    "ultimas_calificaciones": {},
                    "fecha_actualizacion": datetime.now().isoformat(),
                }
                escribir_json_atomico(self.archivo_progreso, datos_iniciales)

    def _cargar(self) -> Dict[str, Any]:
        try:
            with open(self.archivo_progreso, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {"historial_tests": [], "ultimas_calificaciones": {}}

    def _guardar(self, datos: Dict[str, Any]):
        datos["fecha_actualizacion"] = datetime.now().isoformat()
        escribir_json_atomico(self.archivo_progreso, datos)

    def registrar_resultado_test(
        self,
        asignatura: str,
        tema: str,
        nota: float,
        aciertos: int,
        total: int,
    ):
        """Registra el resultado de un examen tipo test realizado en el sistema."""
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

            self._guardar(datos)

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

    def mostrar_dashboard_progreso(self):
        """Muestra en terminal una vista analítica del expediente académico."""
        datos = self._cargar()
        calificaciones = datos.get("ultimas_calificaciones", {})
        historial = datos.get("historial_tests", [])

        console.print(
            Panel.fit(
                "[bold cyan]📊 EXPEDIENTE Y SEMÁFORO DE EXAMEN — 2º DAM (UNIR)[/bold cyan]\n"
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
