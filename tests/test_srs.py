"""
Tests del motor de repetición espaciada y del cálculo de temas flojos.

Se ejecutan con la biblioteca estándar, sin instalar nada:
    .venv/bin/python -m unittest discover -s tests -v
"""

import json
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

from src.ai.flashcards import GestorFlashcards, _ahora_utc, _iso_utc, _parsear_fecha
from src.core.progress_tracker import GestorProgreso


def _crear_gestor_de_prueba(directorio: Path) -> GestorFlashcards:
    """Crea un gestor sin tocar los datos reales del usuario."""
    gestor = object.__new__(GestorFlashcards)
    gestor.username = "test_srs"
    gestor.archivo_datos = directorio / "mazos.json"
    gestor.mazos = {
        "dam": [
            {
                "id": "card_test",
                "asignatura": "Acceso a Datos",
                "anverso": "¿Qué es JDBC?",
                "reverso": "La API de Java para conectarse a bases de datos.",
                "estado": "nuevo",
                "repasos": 0,
            }
        ]
    }
    return gestor


class TestRepeticionEspaciada(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.directorio = Path(self._tmp.name)
        self.gestor = _crear_gestor_de_prueba(self.directorio)

    def tearDown(self):
        self._tmp.cleanup()

    def test_tarjeta_nueva_empieza_vencida(self):
        self.gestor._normalizar_tarjeta(self.gestor.mazos["dam"][0])
        vencidas = self.gestor.tarjetas_vencidas("dam")
        self.assertEqual(len(vencidas), 1)
        self.assertIsNone(vencidas[0]["ultima_revision"])

    def test_facil_aumenta_el_intervalo(self):
        tarjeta = self.gestor.revisar_tarjeta("dam", "card_test", "facil")
        self.assertEqual(tarjeta["repeticiones"], 1)
        self.assertEqual(tarjeta["intervalo_dias"], 1)

        tarjeta = self.gestor.revisar_tarjeta("dam", "card_test", "facil")
        self.assertEqual(tarjeta["repeticiones"], 2)
        self.assertEqual(tarjeta["intervalo_dias"], 6)

        tarjeta = self.gestor.revisar_tarjeta("dam", "card_test", "facil")
        self.assertGreater(tarjeta["intervalo_dias"], 6)

        proximo = _parsear_fecha(tarjeta["proximo_repaso"])
        self.assertGreater(proximo, _ahora_utc())

    def test_dificil_reinicia_la_racha_y_repite_pronto(self):
        self.gestor.revisar_tarjeta("dam", "card_test", "facil")
        tarjeta = self.gestor.revisar_tarjeta("dam", "card_test", "dificil")

        self.assertEqual(tarjeta["repeticiones"], 0)
        self.assertEqual(tarjeta["intervalo_dias"], 0)
        self.assertEqual(tarjeta["estado"], "dificil")

        proximo = _parsear_fecha(tarjeta["proximo_repaso"])
        diferencia = proximo - _ahora_utc()
        self.assertLessEqual(diferencia, timedelta(minutes=11))

    def test_estadisticas_cuentan_vencidas_y_dominadas(self):
        self.gestor.revisar_tarjeta("dam", "card_test", "facil")
        self.gestor.mazos["dam"][0]["proximo_repaso"] = _iso_utc(_ahora_utc() - timedelta(days=1))
        self.gestor.mazos["dam"][0]["intervalo_dias"] = 30

        stats = self.gestor.estadisticas("dam")
        self.assertEqual(stats["total"], 1)
        self.assertEqual(stats["vencidas"], 1)
        self.assertEqual(stats["dominadas"], 1)

    def test_migracion_de_tarjeta_antigua(self):
        tarjeta = {"id": "vieja", "anverso": "a", "reverso": "b", "estado": "nuevo"}
        self.gestor._normalizar_tarjeta(tarjeta)
        self.assertIn("proximo_repaso", tarjeta)
        self.assertIn("facilidad", tarjeta)
        self.assertIn("intervalo_dias", tarjeta)


class TestTemasDebiles(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.directorio = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _crear_tracker(self, datos: dict) -> GestorProgreso:
        tracker = object.__new__(GestorProgreso)
        tracker.username = "test_srs"
        tracker.archivo_progreso = self.directorio / "progreso.json"
        tracker.archivo_progreso.write_text(json.dumps(datos), encoding="utf-8")
        return tracker

    def test_detecta_nota_baja(self):
        tracker = self._crear_tracker({
            "ultimas_calificaciones": {
                "Acceso a Datos::Tema 1": {"nota": 4.2, "fecha": "2026-11-01"},
                "Acceso a Datos::Tema 2": {"nota": 8.5, "fecha": "2026-11-02"},
            },
            "preguntas_falladas": [],
        })
        debiles = tracker.obtener_temas_debiles()
        self.assertEqual(len(debiles), 1)
        self.assertEqual(debiles[0]["tema"], "Tema 1")
        self.assertEqual(debiles[0]["estado"], "Repasar")

    def test_detecta_fallos_repetidos_sin_nota(self):
        tracker = self._crear_tracker({
            "ultimas_calificaciones": {},
            "preguntas_falladas": [
                {"asignatura": "PSP", "tema": "Tema 3", "enunciado": "a"},
                {"asignatura": "PSP", "tema": "Tema 3", "enunciado": "b"},
            ],
        })
        debiles = tracker.obtener_temas_debiles()
        self.assertEqual(len(debiles), 1)
        self.assertEqual(debiles[0]["fallos"], 2)
        self.assertEqual(debiles[0]["estado"], "Reforzar")


if __name__ == "__main__":
    unittest.main()
