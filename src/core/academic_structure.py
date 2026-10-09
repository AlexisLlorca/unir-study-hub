"""
Estructura Académica Oficial para 2º DAM & PFU (UNIR).
Normaliza nombres de asignaturas, organiza materiales y elimina duplicidades
para garantizar una biblioteca limpia y navegable.
"""

import re
import shutil
from pathlib import Path
from typing import Dict, List, Any, Optional

from src.core.config import (
    console,
    BASE_DIR,
    MATERIALES_DIR,
    CLASES_GRABADAS_DIR,
    TRANSCRIPCIONES_DIR,
    APUNTES_DIR,
    EJERCICIOS_DIR,
    EXAMENES_DIR,
)

# 1. Asignaturas Canónicas Oficiales de 2º DAM
ASIGNATURAS_2DAM = [
    "Acceso a Datos",
    "Desarrollo de Interfaces",
    "Programación Multimedia y Dispositivos Móviles",
    "Programación de Servicios y Procesos",
    "Sistemas de Gestión Empresarial",
    "Arquitecturas en la Nube",
    "Itinerario para la Empleabilidad II",
    "Inglés Profesional",
    "Digitalización Aplicada",
    "Sostenibilidad Aplicada",
    "Proyecto Intermodular DAM",
    "Formación en Empresa (FFE)",
]

# Asignaturas de 1er curso (para archivar sin ensuciar)
ASIGNATURAS_1DAM = [
    "1º DAM - Bases de Datos",
    "1º DAM - Desarrollo de Entornos",
    "1º DAM - Lenguajes de Marcas",
    "1º DAM - Sistemas Informáticos",
    "1º DAM - Programación",
]


def normalizar_nombre_asignatura(nombre_sucio: str) -> str:
    """
    Convierte cualquier nombre de carpeta, curso de Canvas o backup local
    en el nombre canónico oficial y limpio de la asignatura.
    """
    s = nombre_sucio.lower().strip()

    # 2º DAM
    if "acceso a datos" in s or "clasead" in s:
        return "Acceso a Datos"
    if "interfaces" in s:
        return "Desarrollo de Interfaces"
    if "multimedia" in s or "dispositivos m" in s:
        return "Programación Multimedia y Dispositivos Móviles"
    if "servicios y procesos" in s or "psp" in s:
        return "Programación de Servicios y Procesos"
    if "gestión empresarial" in s or "gestion empresarial" in s or "sge" in s:
        return "Sistemas de Gestión Empresarial"
    if "nube" in s or "cloud" in s:
        return "Arquitecturas en la Nube"
    if "empleabilidad" in s or "ipe" in s:
        return "Itinerario para la Empleabilidad II"
    if "inglés" in s or "ingles" in s:
        return "Inglés Profesional"
    if "digitali" in s:
        return "Digitalización Aplicada"
    if "sostenib" in s:
        return "Sostenibilidad Aplicada"
    if "proyecto" in s:
        return "Proyecto Intermodular DAM"
    if "formación en empresa" in s or "formacion en empresa" in s or "ffe" in s:
        return "Formación en Empresa (FFE)"

    # 1º DAM
    if "bases de datos" in s:
        return "1º DAM - Bases de Datos"
    if "lenguajes de marcas" in s or "llmm" in s:
        return "1º DAM - Lenguajes de Marcas"
    if "entornos" in s:
        return "1º DAM - Desarrollo de Entornos"
    if "sistemas inform" in s:
        return "1º DAM - Sistemas Informáticos"
    if "programación" in s or "programacion" in s:
        return "1º DAM - Programación"

    return nombre_sucio.strip()


def reorganizar_biblioteca_completa():
    """
    Escanea data/materiales, data/clases_grabadas y data/procesado/transcripciones
    y agrupa todos los archivos en carpetas canónicas limpias.
    Elimina carpetas intermedias caóticas como 'Tema 1', 'Trimestre 1', 'Unit 1'.
    """
    backup_root = Path("/home/alexllorca/Respaldo_Estudios/Proyectos/FP DAM unir")
    backup_index = {}
    if backup_root.exists():
        for p in backup_root.glob("**/*"):
            if p.is_file():
                backup_index[p.name] = p

    # 1. Reorganizar data/materiales/
    if MATERIALES_DIR.exists():
        carpetas = [d for d in MATERIALES_DIR.iterdir() if d.is_dir()]
        for carpeta in carpetas:
            asig_canonica = normalizar_nombre_asignatura(carpeta.name)

            # Si es una carpeta "huérfana" como 'Tema 1', 'Trimestre 1', buscar el origen real
            archivos = list(carpeta.glob("*"))
            for archivo in archivos:
                if archivo.is_dir():
                    continue

                destino_asig = asig_canonica
                # Si el nombre de la carpeta era genérico (Tema 1, Unit 1, etc.)
                if asig_canonica in [carpeta.name, "General"] or carpeta.name.startswith(("Tema", "Unit", "Trimestre", "1er", "Prueba", "Actividad", "T2", "Exam", "Llorca", "Trabajo")):
                    if archivo.name in backup_index:
                        orig = backup_index[archivo.name]
                        top_folder = orig.relative_to(backup_root).parts[0]
                        destino_asig = normalizar_nombre_asignatura(top_folder)
                    else:
                        # Deducir por prefijo de archivo
                        n_low = archivo.name.lower()
                        if "psp" in n_low: destino_asig = "Programación de Servicios y Procesos"
                        elif "di_" in n_low or "interfaces" in n_low: destino_asig = "Desarrollo de Interfaces"
                        elif "ad_" in n_low or "mongo" in n_low: destino_asig = "Acceso a Datos"
                        elif "sge" in n_low or "odoo" in n_low: destino_asig = "Sistemas de Gestión Empresarial"
                        elif "ipe" in n_low: destino_asig = "Itinerario para la Empleabilidad II"
                        elif "unit" in n_low or "english" in n_low: destino_asig = "Inglés Profesional"

                dir_dest = MATERIALES_DIR / destino_asig
                dir_dest.mkdir(parents=True, exist_ok=True)
                dest_file = dir_dest / archivo.name
                if archivo.resolve() != dest_file.resolve():
                    if not dest_file.exists():
                        shutil.move(str(archivo), str(dest_file))
                    else:
                        archivo.unlink()

            # Si la carpeta quedó vacía o era distinta del nombre canónico, borrarla
            if carpeta.exists() and len(list(carpeta.iterdir())) == 0:
                carpeta.rmdir()

    # 2. Reorganizar data/clases_grabadas/
    if CLASES_GRABADAS_DIR.exists():
        for carpeta in list(CLASES_GRABADAS_DIR.iterdir()):
            if carpeta.is_dir():
                asig_canonica = normalizar_nombre_asignatura(carpeta.name)
                dir_dest = CLASES_GRABADAS_DIR / asig_canonica
                dir_dest.mkdir(parents=True, exist_ok=True)
                for f in list(carpeta.glob("*")):
                    dest_file = dir_dest / f.name
                    if f.resolve() != dest_file.resolve():
                        if not dest_file.exists():
                            shutil.move(str(f), str(dest_file))
                        else:
                            f.unlink()
                if carpeta.exists() and carpeta.resolve() != dir_dest.resolve() and len(list(carpeta.iterdir())) == 0:
                    carpeta.rmdir()

    # 3. Reorganizar data/procesado/transcripciones/
    if TRANSCRIPCIONES_DIR.exists():
        for carpeta in list(TRANSCRIPCIONES_DIR.iterdir()):
            if carpeta.is_dir():
                asig_canonica = normalizar_nombre_asignatura(carpeta.name)
                dir_dest = TRANSCRIPCIONES_DIR / asig_canonica
                dir_dest.mkdir(parents=True, exist_ok=True)
                for f in list(carpeta.glob("*")):
                    dest_file = dir_dest / f.name
                    if f.resolve() != dest_file.resolve():
                        if not dest_file.exists():
                            shutil.move(str(f), str(dest_file))
                        else:
                            f.unlink()
                if carpeta.exists() and carpeta.resolve() != dir_dest.resolve() and len(list(carpeta.iterdir())) == 0:
                    carpeta.rmdir()


def obtener_catalogo_academico() -> Dict[str, Dict[str, Any]]:
    """
    Retorna el inventario completo, consolidado y ordenado por asignatura.
    """
    catalogo = {}

    todas_las_asignaturas = ASIGNATURAS_2DAM + ASIGNATURAS_1DAM

    for asig in todas_las_asignaturas:
        dir_mat = MATERIALES_DIR / asig
        dir_clases = CLASES_GRABADAS_DIR / asig
        dir_trans = TRANSCRIPCIONES_DIR / asig
        dir_apuntes = APUNTES_DIR / asig
        dir_ejer = EJERCICIOS_DIR / asig
        dir_exam = EXAMENES_DIR / asig

        materiales = sorted([f for f in dir_mat.glob("*") if f.is_file()]) if dir_mat.exists() else []
        transcripciones = sorted([f for f in dir_trans.glob("*.md") if f.is_file()]) if dir_trans.exists() else []
        fichas_clases = sorted([f for f in dir_clases.glob("*_info.json") if f.is_file()]) if dir_clases.exists() else []
        apuntes = sorted([f for f in dir_apuntes.glob("*.md") if f.is_file()]) if dir_apuntes.exists() else []
        ejercicios = sorted([f for f in dir_ejer.glob("*.md") if f.is_file()]) if dir_ejer.exists() else []
        examenes = sorted([f for f in dir_exam.glob("*.md") if f.is_file()]) if dir_exam.exists() else []

        # Solo incluir asignaturas que tengan material o sean de 2º DAM
        if materiales or transcripciones or apuntes or asig in ASIGNATURAS_2DAM:
            catalogo[asig] = {
                "curso": "2º DAM" if asig in ASIGNATURAS_2DAM else "1º DAM (Histórico)",
                "materiales": materiales,
                "transcripciones": transcripciones,
                "clases_grabadas": fichas_clases,
                "apuntes": apuntes,
                "ejercicios": ejercicios,
                "examenes": examenes,
            }

    return catalogo
