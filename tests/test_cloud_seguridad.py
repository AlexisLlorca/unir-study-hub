"""
Tests de las defensas de sincronización con la nube.

Comprueban que nunca se suben credenciales, que las rutas de un tar no escapan
y que una ruta del repositorio no puede salir de la carpeta del proyecto.

Se ejecutan sin instalar nada:
    .venv/bin/python -m unittest discover -s tests -v
"""

import os
import stat
import tarfile
import tempfile
import unittest
from pathlib import Path

from src.core.auth import _asegurar_permisos_privados
from src.core.cloud_sync import (
    _destino_repo_seguro,
    _es_ruta_sensible,
    _miembro_tar_seguro,
)


class TestRutasSensibles(unittest.TestCase):
    def test_bloquea_credenciales_conocidas(self):
        for ruta in (
            "data/usuarios.json",
            "data/usuarios.db",
            "data/usuarios.db-wal",
            "auth/session_state.json",
            "data/usuarios_espacios/alexking44/canvas_config.json",
            ".env",
            "data/claves.pem",
        ):
            with self.subTest(ruta=ruta):
                self.assertTrue(_es_ruta_sensible(ruta))

    def test_permite_contenido_de_estudio(self):
        for ruta in (
            "data/procesado/guias/Acceso a Datos/Tema 1.md",
            "data/procesado/flashcards/mazos.json",
            "data/usuarios_espacios/alexking44/progreso.json",
        ):
            with self.subTest(ruta=ruta):
                self.assertFalse(_es_ruta_sensible(ruta))


class TestTarSeguro(unittest.TestCase):
    def _miembro(self, nombre: str) -> tarfile.TarInfo:
        info = tarfile.TarInfo(name=nombre)
        info.size = 0
        return info

    def test_rechaza_traversal_y_absolutas(self):
        self.assertFalse(_miembro_tar_seguro(self._miembro("../../etc/passwd")))
        self.assertFalse(_miembro_tar_seguro(self._miembro("/etc/passwd")))

    def test_rechaza_enlaces(self):
        enlace = self._miembro("data/enlace")
        enlace.type = tarfile.SYMTYPE
        enlace.linkname = "/etc/passwd"
        self.assertFalse(_miembro_tar_seguro(enlace))

    def test_acepta_archivo_normal(self):
        self.assertTrue(_miembro_tar_seguro(self._miembro("data/procesado/guias/x.md")))


class TestDestinoRepo(unittest.TestCase):
    def test_rechaza_salir_de_base(self):
        self.assertIsNone(_destino_repo_seguro("data/../../etc/passwd"))
        self.assertIsNone(_destino_repo_seguro("/etc/passwd"))

    def test_acepta_ruta_normal(self):
        destino = _destino_repo_seguro("data/procesado/guias/x.md")
        self.assertIsNotNone(destino)
        self.assertTrue(str(destino).endswith("data/procesado/guias/x.md"))


class TestPermisosPrivados(unittest.TestCase):
    def test_deja_el_fichero_en_600(self):
        with tempfile.TemporaryDirectory() as tmp:
            fichero = Path(tmp) / "secreto.txt"
            fichero.write_text("x", encoding="utf-8")
            os.chmod(fichero, 0o644)
            _asegurar_permisos_privados(fichero)
            modo = stat.S_IMODE(fichero.stat().st_mode)
            self.assertEqual(modo, 0o600)


if __name__ == "__main__":
    unittest.main()
