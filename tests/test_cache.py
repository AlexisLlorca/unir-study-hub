"""
Tests de las cachés por fecha de modificación (mtime).

Comprueban que:
1. La segunda lectura no vuelve a parsear el fichero (usa la caché).
2. Si el fichero cambia, la caché se invalida y se lee lo nuevo.
3. Lo que devuelve la caché es una copia, no el objeto compartido.

Se ejecutan sin instalar nada:
    .venv/bin/python -m unittest discover -s tests -v
"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.ai.flashcards import GestorFlashcards
from src.core.progress_tracker import GestorProgreso


class TestCacheProgreso(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.directorio = Path(self._tmp.name)
        self.archivo = self.directorio / "progreso.json"
        self.archivo.write_text(
            json.dumps({"historial_tests": [], "ultimas_calificaciones": {}, "preguntas_falladas": []}),
            encoding="utf-8",
        )
        self.gestor = object.__new__(GestorProgreso)
        self.gestor.username = "test_cache"
        self.gestor.archivo_progreso = self.archivo

    def tearDown(self):
        self._tmp.cleanup()

    def test_segunda_lectura_no_relee_el_disco(self):
        with patch("src.core.progress_tracker.json.load", wraps=json.load) as cargas:
            self.gestor._cargar()
            self.gestor._cargar()
        self.assertEqual(cargas.call_count, 1)

    def test_cambio_de_fichero_invalida_la_cache(self):
        self.gestor._cargar()
        nuevo = {
            "historial_tests": [{"nota": 9}],
            "ultimas_calificaciones": {},
            "preguntas_falladas": [],
        }
        self.archivo.write_text(json.dumps(nuevo), encoding="utf-8")
        self.assertEqual(self.gestor._cargar()["historial_tests"], [{"nota": 9}])

    def test_devuelve_una_copia(self):
        datos = self.gestor._cargar()
        datos["historial_tests"].append({"nota": 0})
        self.assertEqual(self.gestor._cargar()["historial_tests"], [])


class TestCacheFlashcards(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.directorio = Path(self._tmp.name)
        self.archivo = self.directorio / "mazos.json"
        self.mazos = {"dam": [{"id": "c1", "anverso": "A", "reverso": "B"}]}
        self.archivo.write_text(json.dumps(self.mazos), encoding="utf-8")
        self.gestor = object.__new__(GestorFlashcards)
        self.gestor.username = "test_cache"
        self.gestor.archivo_datos = self.archivo
        self.gestor.mazos = {}

    def tearDown(self):
        self._tmp.cleanup()

    def test_segunda_lectura_no_relee_el_disco(self):
        with patch("src.ai.flashcards.json.loads", wraps=json.loads) as cargas:
            self.gestor._cargar_o_inicializar()
            self.gestor._cargar_o_inicializar()
        self.assertEqual(cargas.call_count, 1)

    def test_cambio_de_fichero_invalida_la_cache(self):
        self.gestor._cargar_o_inicializar()
        nuevos = {"dam": [{"id": "c2", "anverso": "Nuevo", "reverso": "Contenido"}]}
        self.archivo.write_text(json.dumps(nuevos), encoding="utf-8")
        self.assertEqual(self.gestor._cargar_o_inicializar()["dam"][0]["id"], "c2")

    def test_devuelve_una_copia(self):
        mazos = self.gestor._cargar_o_inicializar()
        mazos["dam"].append({"id": "intruso"})
        self.assertEqual(len(self.gestor._cargar_o_inicializar()["dam"]), 1)


if __name__ == "__main__":
    unittest.main()
