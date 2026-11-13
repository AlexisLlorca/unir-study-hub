"""
Tests del resumen semanal del entrenador personal.

Se ejecutan sin instalar nada:
    .venv/bin/python -m unittest discover -s tests -v
"""

import json
import unittest
from datetime import date, timedelta

from src.core.resumen_semanal import _a_fecha, calcular_resumen_semanal, construir_recomendacion

HOY = date(2026, 11, 13)  # viernes, fijo para que los tests no dependan del día real


def _tarjeta(ultima_revision=None):
    t = {"id": "c1", "anverso": "A", "reverso": "B"}
    if ultima_revision:
        t["ultima_revision"] = ultima_revision
    return t


class TestConversionFechas(unittest.TestCase):
    def test_acepta_los_tres_formatos(self):
        self.assertEqual(_a_fecha("2026-11-13 21:00"), HOY)
        self.assertEqual(_a_fecha("2026-11-13T20:59:12.123456+00:00"), HOY)
        self.assertEqual(_a_fecha("2026-11-13"), HOY)

    def test_basura_es_none(self):
        self.assertIsNone(_a_fecha("no es una fecha"))
        self.assertIsNone(_a_fecha(None))
        self.assertIsNone(_a_fecha(12345))


class TestRacha(unittest.TestCase):
    def test_sin_actividad_cero(self):
        r = calcular_resumen_semanal({}, {}, [], hoy=HOY)
        self.assertEqual(r["racha_dias"], 0)

    def test_estudio_hoy_cuenta(self):
        ayer = (HOY - timedelta(days=1)).isoformat() + "T10:00:00+00:00"
        hoy = HOY.isoformat() + " 09:30"
        mazos = {"dam": [_tarjeta(ayer), _tarjeta(hoy)]}
        r = calcular_resumen_semanal(mazos, {}, [], hoy=HOY)
        self.assertEqual(r["racha_dias"], 2)
        self.assertEqual(r["tarjetas_semana"], 2)

    def test_no_estudiar_hoy_no_rompe_la_racha(self):
        anteayer = (HOY - timedelta(days=2)).isoformat() + "T10:00:00+00:00"
        ayer = (HOY - timedelta(days=1)).isoformat() + "T23:00:00+00:00"
        r = calcular_resumen_semanal({"dam": [_tarjeta(anteayer), _tarjeta(ayer)]}, {}, [], hoy=HOY)
        self.assertEqual(r["racha_dias"], 2)

    def test_dos_dias_sin_estudiar_la_racha_es_cero(self):
        hace_dos = (HOY - timedelta(days=2)).isoformat() + " 10:00"
        hace_cuatro = (HOY - timedelta(days=4)).isoformat() + " 10:00"
        progreso = {"historial_tests": [
            {"nota": 5, "fecha": hace_dos},
            {"nota": 5, "fecha": hace_cuatro},
        ]}
        r = calcular_resumen_semanal({}, progreso, [], hoy=HOY)
        self.assertEqual(r["racha_dias"], 0)


class TestSemana(unittest.TestCase):
    def test_tests_y_nota_media_de_la_semana(self):
        esta_semana = (HOY - timedelta(days=2)).isoformat() + " 10:00"
        hace_un_mes = (HOY - timedelta(days=20)).isoformat() + " 10:00"
        progreso = {"historial_tests": [
            {"asignatura": "Acceso a Datos", "tema": "Tema 1", "nota": 4.0, "fecha": esta_semana},
            {"asignatura": "PFU", "tema": "Tema 2", "nota": 8.0, "fecha": esta_semana},
            {"asignatura": "PFU", "tema": "Tema 2", "nota": 9.0, "fecha": hace_un_mes},
        ]}
        r = calcular_resumen_semanal({}, progreso, [], hoy=HOY)
        self.assertEqual(r["tests_semana"], 2)
        self.assertEqual(r["nota_media_semana"], 6.0)

    def test_tema_flojo_es_el_peor_del_mes(self):
        hace_una_semana = (HOY - timedelta(days=7)).isoformat() + " 10:00"
        hace_dos_meses = (HOY - timedelta(days=60)).isoformat() + " 10:00"
        progreso = {"historial_tests": [
            {"asignatura": "Acceso a Datos", "tema": "Tema 1", "nota": 3.0, "fecha": hace_una_semana},
            {"asignatura": "PFU", "tema": "Tema 2", "nota": 7.0, "fecha": hace_una_semana},
            {"asignatura": "Vieja", "tema": "Tema 0", "nota": 0.0, "fecha": hace_dos_meses},
        ]}
        r = calcular_resumen_semanal({}, progreso, [], hoy=HOY)
        self.assertEqual(r["tema_flojo"]["tema"], "Tema 1")
        self.assertEqual(r["tema_flojo"]["nota"], 3.0)

    def test_entregas_proximas_sin_clases(self):
        maana = (HOY + timedelta(days=1)).isoformat()
        eventos = [
            {"titulo": "Clase diaria", "categoria": "Clase", "estado": "No vista", "fecha_dia": maana},
            {"titulo": "Actividad 1", "categoria": "Actividad", "estado": "No entregado", "fecha_dia": maana},
            {"titulo": "Ya entregada", "categoria": "Actividad", "estado": "Entregado", "fecha_dia": maana},
            {"titulo": "Muy lejana", "categoria": "Actividad", "estado": "No entregado",
             "fecha_dia": (HOY + timedelta(days=20)).isoformat()},
        ]
        r = calcular_resumen_semanal({}, {}, eventos, hoy=HOY)
        self.assertEqual(len(r["entregas_proximas"]), 1)
        self.assertEqual(r["entregas_proximas"][0]["titulo"], "Actividad 1")
        self.assertEqual(r["entregas_proximas"][0]["dias_restantes"], 1)


class TestRecomendacion(unittest.TestCase):
    def _semana(self, **cambios):
        base = {"racha_dias": 0, "tarjetas_semana": 0, "tests_semana": 0,
                "nota_media_semana": None, "tema_flojo": None, "entregas_proximas": []}
        base.update(cambios)
        return base

    def test_entrega_urgente_va_primero(self):
        semana = self._semana(entregas_proximas=[
            {"titulo": "Actividad 1", "asignatura": "Multimedia", "dias_restantes": 1},
        ])
        rec = construir_recomendacion(semana, {"vencidas": 27})
        self.assertIn("Prioridad", rec)
        self.assertIn("para mañana", rec)
        self.assertIn("Repaso", rec)

    def test_sin_urgencia_menciona_entrega_lejana(self):
        semana = self._semana(entregas_proximas=[
            {"titulo": "Actividad 1", "asignatura": "Multimedia", "dias_restantes": 4},
        ])
        rec = construir_recomendacion(semana, {"vencidas": 0})
        self.assertIn("cae en 4 días", rec)
        self.assertNotIn("Prioridad", rec)

    def test_dia_tranquilo_da_alternativa(self):
        rec = construir_recomendacion(self._semana(), {"vencidas": 0})
        self.assertIn("Día tranquilo", rec)

    def test_menciona_racha_y_tema_flojo(self):
        semana = self._semana(
            racha_dias=3,
            tema_flojo={"asignatura": "Acceso a Datos", "tema": "Tema 1. Ficheros", "nota": 2.0},
        )
        rec = construir_recomendacion(semana, {"vencidas": 5})
        self.assertIn("3 días de racha", rec)
        self.assertIn("Tema 1. Ficheros", rec)
        self.assertIn("(2.0)", rec)

    def test_titulos_largos_se_recortan(self):
        semana = self._semana(entregas_proximas=[
            {"titulo": "X" * 200, "asignatura": "Asignatura" + "Larga" * 20, "dias_restantes": 0},
        ])
        rec = construir_recomendacion(semana, {"vencidas": 0})
        self.assertLess(len(rec), 400)
        self.assertIn("…", rec)


if __name__ == "__main__":
    unittest.main()
