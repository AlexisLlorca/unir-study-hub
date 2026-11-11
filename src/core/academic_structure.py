"""
Estructura Académica Multi-Programa Oficial para UNIR Study Hub.
Soporta múltiples itinerarios académicos independientes:
  1. FP Grado Superior DAM (1º y 2º Curso)
  2. Máster en Ciberseguridad (Módulos M1 a M7)
  3. PFU / Grado en Ingeniería del Software (UNIR)
  4. Certificaciones & Otras Formaciones Continuas
Normaliza nombres de asignaturas, organiza materiales y elimina duplicidades
para garantizar un estudio sin mezclas y de alto rendimiento.
"""

import os
import re
import shutil
from pathlib import Path
from typing import Dict, List, Any, Optional

from src.core.config import (
    console,
    BASE_DIR,
    RESPALDO_DIR,
    MATERIALES_DIR,
    CLASES_GRABADAS_DIR,
    TRANSCRIPCIONES_DIR,
    APUNTES_DIR,
    EJERCICIOS_DIR,
    EXAMENES_DIR,
    GUIAS_DIR,
)

# --- DEFINICIÓN DE PROGRAMAS Y CURSOS ---

PROGRAMAS: Dict[str, Dict[str, Any]] = {
    "dam": {
        "id": "dam",
        "nombre": "FP Grado Superior DAM",
        "titulo_corto": "FP DAM",
        "icono": "fa-code",
        "badge": "Desarrollo Multiplataforma",
        "color": "cyan",
        "descripcion": "Ciclo Formativo de Grado Superior en Desarrollo de Aplicaciones Multiplataforma (2º en curso, 1º completado).",
        "cursos": {
            "2dam": {
                "id": "2dam",
                "nombre": "2º Curso (En Curso)",
                "asignaturas": [
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
                    "Aula de Información General",
                ],
            },
            "1dam": {
                "id": "1dam",
                "nombre": "1º Curso (Completado)",
                "asignaturas": [
                    "1º DAM - Bases de Datos",
                    "1º DAM - Programación",
                    "1º DAM - Sistemas Informáticos",
                    "1º DAM - Desarrollo de Entornos",
                    "1º DAM - Lenguajes de Marcas",
                    "1º DAM - Itinerario para la Empleabilidad I",
                    "1º DAM - Inglés Técnico I",
                ],
            },
        },
    },
    "pfu": {
        "id": "pfu",
        "nombre": "PFU en Ingeniería de Software",
        "titulo_corto": "PFU Software",
        "icono": "fa-graduation-cap",
        "badge": "Grado Universitario",
        "color": "indigo",
        "descripcion": "Programa de Formación Universitaria en Ingeniería de Software (PER 16645 - Octubre 2026).",
        "cursos": {
            "pfu": {
                "id": "pfu",
                "nombre": "Plan de Formación Universitaria",
                "asignaturas": [
                    "PFU - Ingeniería del Software",
                    "PFU - Bases de Datos Avanzadas",
                ],
            }
        },
    },
    "bootcamp": {
        "id": "bootcamp",
        "nombre": "Bootcamp en Habilidades Digitales y Gestión Ágil",
        "titulo_corto": "Bootcamp Ágil",
        "icono": "fa-bolt",
        "badge": "Gestión Ágil & Digital",
        "color": "violet",
        "descripcion": "Formación en marcos ágiles (Scrum, Kanban), transformación digital y liderazgo de proyectos (PER 14915).",
        "cursos": {
            "bootcamp": {
                "id": "bootcamp",
                "nombre": "Módulos del Bootcamp",
                "asignaturas": [
                    "Bootcamp - Habilidades Digitales y Gestión Ágil",
                ],
            }
        },
    },
    "python": {
        "id": "python",
        "nombre": "Curso de Programación en Python",
        "titulo_corto": "Python",
        "icono": "fa-brands fa-python",
        "badge": "Especialización Python",
        "color": "emerald",
        "descripcion": "Especialización técnica en desarrollo con Python: sintaxis, POO, scripts y estructuras de datos (PER 13236).",
        "cursos": {
            "python": {
                "id": "python",
                "nombre": "Temario y Prácticas Python",
                "asignaturas": [
                    "Curso de Programación en Python",
                ],
            }
        },
    },
    "aws": {
        "id": "aws",
        "nombre": "Certificación AWS Cloud Practitioner",
        "titulo_corto": "AWS Cloud",
        "icono": "fa-brands fa-aws",
        "badge": "Cloud Practitioner CLF-C02",
        "color": "amber",
        "descripcion": "Preparación oficial para la certificación Amazon Web Services Cloud Practitioner CLF-C02 (PER 13239).",
        "cursos": {
            "aws": {
                "id": "aws",
                "nombre": "Preparación Examen Oficial AWS",
                "asignaturas": [
                    "Curso de Preparación Certificación AWS Cloud Practitioner",
                ],
            }
        },
    },
    "azure": {
        "id": "azure",
        "nombre": "Certificación AZ-900: Microsoft Azure",
        "titulo_corto": "Azure AZ-900",
        "icono": "fa-brands fa-microsoft",
        "badge": "Azure Fundamentals",
        "color": "sky",
        "descripcion": "Preparación oficial para la certificación Microsoft Certified: Azure Fundamentals AZ-900 (PER 13238).",
        "cursos": {
            "azure": {
                "id": "azure",
                "nombre": "Preparación Examen Oficial Azure",
                "asignaturas": [
                    "Curso de Preparación Certificación AZ-900 (Azure)",
                ],
            }
        },
    },
    "ciber": {
        "id": "ciber",
        "nombre": "Máster en Ciberseguridad",
        "titulo_corto": "Máster Ciberseguridad",
        "icono": "fa-shield-halved",
        "badge": "Especialización Avanzada",
        "color": "rose",
        "descripcion": "Máster Oficial en Ciberseguridad: Red Team, Blue Team, SOC, SIEM, Malware y Forense.",
        "cursos": {
            "master": {
                "id": "master",
                "nombre": "Módulos de Especialización (M1 - M7)",
                "asignaturas": [
                    "M1 - Ciberinteligencia y Fraude",
                    "M2 - Hacking Ético y Auditorías",
                    "M3 - Desarrollo Seguro y OWASP",
                    "M4 - Tecnologías SIEM y Operativa SOC",
                    "M5 - Ingeniería Inversa y Malware",
                    "M6 - Seguridad en Dispositivos Móviles",
                    "M7 - Análisis Forense Digital",
                ],
            }
        },
    },
}

# Aliases para compatibilidad con código existente
ASIGNATURAS_2DAM = PROGRAMAS["dam"]["cursos"]["2dam"]["asignaturas"]
ASIGNATURAS_1DAM = PROGRAMAS["dam"]["cursos"]["1dam"]["asignaturas"]
MODULOS_CIBER = PROGRAMAS["ciber"]["cursos"]["master"]["asignaturas"]
ASIGNATURAS_PFU = PROGRAMAS["pfu"]["cursos"]["pfu"]["asignaturas"]
ASIGNATURAS_BOOTCAMP = PROGRAMAS["bootcamp"]["cursos"]["bootcamp"]["asignaturas"]
ASIGNATURAS_PYTHON = PROGRAMAS["python"]["cursos"]["python"]["asignaturas"]
ASIGNATURAS_AWS = PROGRAMAS["aws"]["cursos"]["aws"]["asignaturas"]
ASIGNATURAS_AZURE = PROGRAMAS["azure"]["cursos"]["azure"]["asignaturas"]


def normalizar_nombre_asignatura(nombre_sucio: str) -> str:
    """
    Convierte cualquier nombre de carpeta, curso de Canvas, Moodle o backup local
    en el nombre canónico oficial y limpio de la asignatura.
    """
    s = nombre_sucio.lower().strip()

    # Si ya es un nombre canónico oficial, no tocar
    todos_canonicos = set(
        ASIGNATURAS_2DAM + ASIGNATURAS_1DAM + MODULOS_CIBER +
        ASIGNATURAS_PFU + ASIGNATURAS_BOOTCAMP + ASIGNATURAS_PYTHON +
        ASIGNATURAS_AWS + ASIGNATURAS_AZURE
    )
    for c in todos_canonicos:
        if s == c.lower():
            return c

    # 1. Módulos de Máster en Ciberseguridad
    if "m1" in s or "ciberinteligencia" in s:
        return "M1 - Ciberinteligencia y Fraude"
    if "m2" in s or "hacking" in s:
        return "M2 - Hacking Ético y Auditorías"
    if "m3" in s or "desarrollo seguro" in s or "owasp" in s:
        return "M3 - Desarrollo Seguro y OWASP"
    if "m4" in s or "siem" in s or "soc" in s:
        return "M4 - Tecnologías SIEM y Operativa SOC"
    if "m5" in s or "inversa" in s or "malware" in s:
        return "M5 - Ingeniería Inversa y Malware"
    if "m6" in s or "smartphones" in s or ("móviles" in s and "seguridad" in s):
        return "M6 - Seguridad en Dispositivos Móviles"
    if "m7" in s or "forense" in s:
        return "M7 - Análisis Forense Digital"

    # 2. Cursos Técnicos, Certificaciones y Bootcamps UNIR
    if "bootcamp" in s or "habilidades digitales" in s or "gestión ágil" in s or "gestion agil" in s or "14915" in s:
        return "Bootcamp - Habilidades Digitales y Gestión Ágil"
    if ("python" in s and ("13236" in s or "programación" in s or "programacion" in s)) or s == "python" or "curso de programación en python" in s:
        return "Curso de Programación en Python"
    if "aws" in s or "cloud practitioner" in s or "13239" in s:
        return "Curso de Preparación Certificación AWS Cloud Practitioner"
    if "az-900" in s or "az900" in s or "azure fundamentals" in s or "13238" in s:
        return "Curso de Preparación Certificación AZ-900 (Azure)"

    # 3. PFU / Grado
    if "aula de información general" in s or "aula de informacion" in s or "aig" in s:
        if "pfu" in s or "16645" in s or "universitaria" in s or "software" in s:
            return "Aula de Información General (PFU)"
        return "Aula de Información General"
    if "bases de datos avanzadas" in s or ("bases de datos" in s and ("16645" in s or "pfu" in s)):
        return "PFU - Bases de Datos Avanzadas"
    if "ingeniería del software" in s or "ingenieria del software" in s or "16645" in s:
        return "PFU - Ingeniería del Software"
    if "estructura de datos" in s or ("algoritmos" in s and "pfu" in s):
        return "PFU - Estructura de Datos y Algoritmos"
    if "arquitectura de computadores" in s:
        return "PFU - Arquitectura de Computadores"
    if "matemáticas" in s or "lógica computacional" in s or "logica computacional" in s:
        return "PFU - Matemáticas y Lógica Computacional"

    # 4. 1º DAM (Prioridad si indica primer curso, 1º o asignaturas exclusivas de 1º)
    if "bases de datos" in s:
        return "1º DAM - Bases de Datos"
    if "lenguajes de marcas" in s or "llmm" in s:
        return "1º DAM - Lenguajes de Marcas"
    if "entornos" in s:
        return "1º DAM - Desarrollo de Entornos"
    if "sistemas inform" in s:
        return "1º DAM - Sistemas Informáticos"
    if "programación" in s or "programacion" in s:
        if "multimedia" not in s and "servicios" not in s:
            return "1º DAM - Programación"
    if "empleabilidad" in s or "ipe" in s:
        if re.search(r'\b(ii|2º|2o|segundo)\b', s) or re.search(r'empleabilidad\s+ii\b', s) or re.search(r'ipe\s*2\b', s):
            return "Itinerario para la Empleabilidad II"
        return "1º DAM - Itinerario para la Empleabilidad I"
    if "inglés" in s or "ingles" in s:
        if ("profesional" in s and "gs" in s) or re.search(r'\b(ii|2º|2o|segundo)\b', s):
            return "Inglés Profesional"
        return "1º DAM - Inglés Técnico I"

    # 5. 2º DAM
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
    if "digitali" in s:
        return "Digitalización Aplicada"
    if "sostenib" in s:
        return "Sostenibilidad Aplicada"
    if "proyecto" in s:
        return "Proyecto Intermodular DAM"
    if "formación en empresa" in s or "formacion en empresa" in s or "ffe" in s:
        return "Formación en Empresa (FFE)"

    return nombre_sucio.strip()


EXTENSIONES_DOCUMENTOS = {".pdf", ".docx"}


def _orden_pedagogico_material(p: Path) -> tuple:
    """
    Ordena los documentos de una asignatura de forma pedagógica y numérica natural:
    1. Temas / Units / Módulos oficiales (Tema 1, Tema 2, ..., Tema 9, Tema 10)
    2. Resúmenes de temas y repasos finales
    3. Presentaciones de clase, manuales, fastbooks e introducciones
    4. Prácticas, laboratorios, ejercicios y bancos de preguntas
    5. Actividades evaluables y pruebas trimestrales (Actividad 1, 2, 3...)
    6. Documentación oficial de la asignatura (Guía del Estudiante, Programación Didáctica, Criterios de Evaluación)
    """
    name = p.name
    nl = name.lower()

    # 1. Temario principal: Tema X, Unit X, Módulo X
    m_tema = re.match(r"^(tema|unit|m[óo]dulo)\s+(\d+)\b", nl)
    if m_tema:
        return (0, int(m_tema.group(2)), nl)

    # 2. Resúmenes de temas
    m_res = re.match(r"^resumen\s*-\s*(?:tema|unit|m[óo]dulo)\s+(\d+)\b", nl)
    if m_res:
        return (1, int(m_res.group(1)), nl)
    if nl.startswith("resumen"):
        return (1, 99, nl)

    # 3. Presentaciones, introducciones, manuales y fastbooks
    if nl.startswith(("presentación", "presentacion", "introducción", "introduccion", "manual", "fastbook", "repaso")):
        nums = [int(x) for x in re.findall(r"\d+", nl)]
        return (2, nums[0] if nums else 50, nl)

    # 4. Prácticas, casos prácticos, cuadernos y bancos de test
    if nl.startswith(("práctica", "practica", "caso", "cuaderno", "banco", "simulacro", "anexo", "plan ")):
        nums = [int(x) for x in re.findall(r"\d+", nl)]
        return (3, nums[0] if nums else 50, nl)

    # 5. Actividades y pruebas trimestrales
    if nl.startswith(("actividad", "prueba", "plantilla", "anteproyecto", "compromiso")):
        nums = [int(x) for x in re.findall(r"\d+", nl)]
        return (4, nums[0] if nums else 50, nl)

    # 6. Guías, Programación Didáctica y Criterios de Evaluación al final bien agrupados
    if "guía" in nl or "guia" in nl:
        return (5, 1, nl)
    if "programación" in nl or "programacion" in nl:
        return (5, 2, nl)
    if "criterio" in nl or "rúbrica" in nl or "rubrica" in nl:
        return (5, 3, nl)

    nums = [int(x) for x in re.findall(r"\d+", nl)]
    return (3, nums[0] if nums else 99, nl)


def _orden_natural_items(item: Any) -> tuple:
    """Ordenamiento natural de menor a mayor (Tema 1, Tema 2, ..., Tema 10) para guías y tests."""
    s = str(getattr(item, "name", item)).replace("_", " ").strip()
    m = re.search(r"(?:tema|unit|m[óo]dulo)\s*[\._-]?\s*(\d+)", s, re.IGNORECASE)
    if m:
        return (0, int(m.group(1)), s.lower())
    m_num = re.search(r"\d+", s)
    if m_num:
        return (1, int(m_num.group(0)), s.lower())
    return (2, 999, s.lower())


def sincronizar_estudios_externos():
    """
    Escanea carpetas externas en RESPALDO_DIR
    (Ciberseguridad y 1º DAM) y las vincula limpiamente en data/materiales/
    únicamente si la carpeta de destino aún está vacía.
    """
    MATERIALES_DIR.mkdir(parents=True, exist_ok=True)

    # 1. Vincular Máster en Ciberseguridad (M1 a M7) solo si la carpeta aún no tiene PDFs organizados
    ciber_root = RESPALDO_DIR / "Temario Ciberseguridad"
    if ciber_root.exists():
        mapeo_ciber = {
            "M1_Ciber": "M1 - Ciberinteligencia y Fraude",
            "M2_Hacking_etico": "M2 - Hacking Ético y Auditorías",
            "M3_Desarrollo_Seguro": "M3 - Desarrollo Seguro y OWASP",
            "M4_Tecnologias_SIEM": "M4 - Tecnologías SIEM y Operativa SOC",
            "M5_Ingeniería_Inversa": "M5 - Ingeniería Inversa y Malware",
            "M6_Seguridad_Smartphones": "M6 - Seguridad en Dispositivos Móviles",
            "M7_Analisis_Forense": "M7 - Análisis Forense Digital",
        }

        for m_folder, m_nombre in mapeo_ciber.items():
            dest_dir = MATERIALES_DIR / m_nombre
            dest_dir.mkdir(parents=True, exist_ok=True)
            if any(dest_dir.glob("*.pdf")):
                continue

            src_temas = ciber_root / m_folder / "Temas"
            if not src_temas.exists():
                src_temas = ciber_root / m_folder

            if src_temas.exists():
                for f in src_temas.glob("*"):
                    if f.is_file() and not f.name.startswith(".") and f.suffix.lower() in EXTENSIONES_DOCUMENTOS:
                        target_link = dest_dir / f.name
                        if not target_link.exists():
                            try:
                                shutil.copy2(f.resolve(), target_link)
                            except Exception:
                                pass

    # 2. Vincular Asignaturas de 1º DAM desde el respaldo local solo si la carpeta aún está vacía
    backup_dam_root = RESPALDO_DIR / "Proyectos" / "FP DAM unir"
    if backup_dam_root.exists():
        mapeo_1dam = {
            "Bases de datos": "1º DAM - Bases de Datos",
            "Programación": "1º DAM - Programación",
            "Sistemas informaticos": "1º DAM - Sistemas Informáticos",
            "Entornos de desarrollo": "1º DAM - Desarrollo de Entornos",
            "Lenguajes de marcas": "1º DAM - Lenguajes de Marcas",
            "Ipe": "1º DAM - Itinerario para la Empleabilidad I",
            "Inglés": "1º DAM - Inglés Técnico I",
        }

        for folder_src, canonico in mapeo_1dam.items():
            dir_dest = MATERIALES_DIR / canonico
            dir_dest.mkdir(parents=True, exist_ok=True)
            if any(dir_dest.glob("*.pdf")):
                continue

            dir_src = backup_dam_root / folder_src
            if dir_src.exists():
                for f in dir_src.glob("**/*"):
                    if f.is_file() and not f.name.startswith(".") and f.suffix.lower() in EXTENSIONES_DOCUMENTOS:
                        dest_link = dir_dest / f.name
                        if not dest_link.exists():
                            try:
                                shutil.copy2(f.resolve(), dest_link)
                            except Exception:
                                pass

    # 3. Crear carpetas de PFU, Bootcamps y Certificaciones para cuando haya material
    for asig_list in [ASIGNATURAS_PFU, ASIGNATURAS_BOOTCAMP, ASIGNATURAS_PYTHON, ASIGNATURAS_AWS, ASIGNATURAS_AZURE]:
        for asig in asig_list:
            (MATERIALES_DIR / asig).mkdir(parents=True, exist_ok=True)


def reorganizar_biblioteca_completa():
    """
    Escanea data/materiales, data/clases_grabadas y data/procesado/transcripciones
    y agrupa todos los archivos en carpetas canónicas limpias.
    Elimina carpetas intermedias caóticas como 'Tema 1', 'Trimestre 1', 'Unit 1'.
    """
    sincronizar_estudios_externos()

    backup_root = RESPALDO_DIR / "Proyectos" / "FP DAM unir"
    backup_index = {}
    if backup_root.exists():
        for p in backup_root.glob("**/*"):
            if p.is_file():
                backup_index[p.name] = p

    # 1. Reorganizar data/materiales/
    if MATERIALES_DIR.exists():
        todos_canonicos = set(
            ASIGNATURAS_2DAM + ASIGNATURAS_1DAM + MODULOS_CIBER +
            ASIGNATURAS_PFU + ASIGNATURAS_BOOTCAMP + ASIGNATURAS_PYTHON +
            ASIGNATURAS_AWS + ASIGNATURAS_AZURE
        )
        carpetas = [d for d in MATERIALES_DIR.iterdir() if d.is_dir()]
        for carpeta in carpetas:
            # Si la carpeta ya es un directorio canónico oficial, no tocar sus archivos
            if carpeta.name in todos_canonicos:
                continue

            asig_canonica = normalizar_nombre_asignatura(carpeta.name)

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
                        n_low = archivo.name.lower()
                        if "psp" in n_low: destino_asig = "Programación de Servicios y Procesos"
                        elif "di_" in n_low or "interfaces" in n_low: destino_asig = "Desarrollo de Interfaces"
                        elif "ad_" in n_low or "mongo" in n_low: destino_asig = "Acceso a Datos"
                        elif "sge" in n_low or "odoo" in n_low: destino_asig = "Sistemas de Gestión Empresarial"
                        elif "ipe" in n_low or "empleabilidad" in n_low:
                            if any(k in n_low for k in ["ii", "2", "inserción", "insercion", "emprendedor", "negocio"]):
                                destino_asig = "Itinerario para la Empleabilidad II"
                            else:
                                destino_asig = "1º DAM - Itinerario para la Empleabilidad I"
                        elif "unit" in n_low or "english" in n_low or "inglés" in n_low or "ingles" in n_low:
                            if any(k in n_low for k in ["profesional", "gs", "effective", "connecting"]):
                                destino_asig = "Inglés Profesional"
                            else:
                                destino_asig = "1º DAM - Inglés Técnico I"

                dir_dest = MATERIALES_DIR / destino_asig
                dir_dest.mkdir(parents=True, exist_ok=True)
                dest_file = dir_dest / archivo.name
                if archivo.resolve() != dest_file.resolve():
                    if not dest_file.exists():
                        shutil.move(str(archivo), str(dest_file))
                    else:
                        archivo.unlink()

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

    # 4. Normalizar y eliminar automáticamente todos los duplicados y nombres crípticos
    normalizar_y_limpiar_biblioteca()


def normalizar_y_limpiar_biblioteca():
    """
    Motor integral de normalización y deduplicación.
    Se ejecuta automáticamente en local y en la nube tras cualquier sincronización:
    - Normaliza MATERIALES_DIR y espacios privados de usuarios
    - Elimina duplicados *_esl-ES.pdf y *_esl2-ES.pdf
    - Elimina 'Tema X.pdf' genéricos si existe la versión con título completo (o Módulo/Unit)
    - Normaliza nombres de actividades (tisupdamXX_act... -> Actividad X - Enunciado/Plantilla)
    - Unifica Guías, Programaciones y Criterios de Evaluación sin duplicados
    - Normaliza prácticas, presentaciones y títulos de módulos
    - Corrige dobles extensiones (.pdf.pdf) y caracteres erróneos
    - Elimina archivos no documentales (.mp4, .zip, .psc, .pptx, etc.)
    """
    if not MATERIALES_DIR.exists():
        return

    directorios_base = [MATERIALES_DIR]
    espacios_dir = BASE_DIR / "data" / "usuarios_espacios"
    if espacios_dir.exists():
        for u in espacios_dir.iterdir():
            if u.is_dir() and (u / "materiales").is_dir():
                directorios_base.append(u / "materiales")

    def safe_rename(folder: Path, old_name: str, new_name: str):
        old_p = folder / old_name
        new_p = folder / new_name
        if not old_p.exists() or old_p.resolve() == new_p.resolve():
            return
        if new_p.exists():
            if old_p.stat().st_size > new_p.stat().st_size:
                new_p.unlink(missing_ok=True)
                old_p.rename(new_p)
            else:
                old_p.unlink(missing_ok=True)
        else:
            old_p.rename(new_p)

    # 1. Eliminar archivos no documentales, reparar dobles extensiones (.pdf.pdf) y trailing underscores
    for dir_base in directorios_base:
        for folder in dir_base.iterdir():
            if not folder.is_dir():
                continue
            for f in list(folder.iterdir()):
                if not f.is_file():
                    continue
                if f.name.endswith(".pdf.pdf"):
                    safe_rename(folder, f.name, f.name[:-4])
                    continue
                if f.name.endswith(".docx.docx"):
                    safe_rename(folder, f.name, f.name[:-5])
                    continue
                if f.name.endswith("_.pdf"):
                    safe_rename(folder, f.name, f.name[:-5] + ".pdf")
                    continue
                if f.name.startswith(("._", "~$")) or f.suffix.lower() not in EXTENSIONES_DOCUMENTOS:
                    f.unlink(missing_ok=True)

    # 2. Eliminar duplicados *_esl-ES.pdf y *_esl2-ES.pdf
    for dir_base in directorios_base:
        for folder in dir_base.iterdir():
            if not folder.is_dir():
                continue
            for f in list(folder.iterdir()):
                if f.is_file() and re.search(r"_esl2?-ES\.pdf$", f.name, re.I):
                    f.unlink(missing_ok=True)

    # 3. Eliminar temas/módulos genéricos (ej: Tema 1.pdf) si ya existe el descriptivo (Tema 1. Ficheros.pdf o Módulo 1...)
    for dir_base in directorios_base:
        for folder in dir_base.iterdir():
            if not folder.is_dir():
                continue
            for f in list(folder.iterdir()):
                if not f.is_file():
                    continue
                m = re.match(r"^(Tema|Unit|M[óo]dulo|Modulo)\s+0?(\d+)\.pdf$", f.name, re.I)
                if m:
                    num = int(m.group(2))
                    has_desc = any(
                        other != f and re.match(r"^(?:tema|unit|m[óo]dulo|modulo|unidad)\s+0?" + str(num) + r"[\.\s_-]+[a-záéíóúüñ0-9].+\.pdf$", other.name, re.I)
                        for other in folder.iterdir() if other.is_file()
                    )
                    if has_desc:
                        f.unlink(missing_ok=True)

    # 4. Normalizar "Tema 0X..." -> "Tema X..."
    for dir_base in directorios_base:
        for folder in dir_base.iterdir():
            if not folder.is_dir():
                continue
            for f in list(folder.iterdir()):
                if not f.is_file():
                    continue
                m = re.match(r"^(Tema|Unit|M[óo]dulo)\s+0(\d)\.\s+(.+\.pdf)$", f.name, re.I)
                if m:
                    safe_rename(folder, f.name, f"{m.group(1)} {m.group(2)}. {m.group(3)}")

    # 5. Normalizar enunciados y plantillas de actividades
    for dir_base in directorios_base:
        for folder in dir_base.iterdir():
            if not folder.is_dir():
                continue
            for f in list(folder.iterdir()):
                if not f.is_file():
                    continue
                nl = f.name.lower()
                if re.match(r"^(tisupdam\d+|consetesutralo\d+|tecsupasir\d+)_act1.*\.(docx|pdf)$", nl):
                    if "(1)" in nl:
                        f.unlink(missing_ok=True)
                    else:
                        safe_rename(folder, f.name, f"Actividad 1 - Enunciado{f.suffix.lower()}")
                elif re.match(r"^(tisupdam\d+|consetesutralo\d+|tecsupasir\d+)_act2.*\.(docx|pdf)$", nl):
                    safe_rename(folder, f.name, f"Actividad 2 - Enunciado{f.suffix.lower()}")
                elif re.match(r"^(tisupdam\d+|consetesutralo\d+|tecsupasir\d+)_act3.*\.(docx|pdf)$", nl):
                    safe_rename(folder, f.name, f"Actividad 3 - Enunciado{f.suffix.lower()}")
                elif nl == "unir_fp_plantilla_actividad_1_ed.docx":
                    safe_rename(folder, f.name, "Actividad 1 - Plantilla.docx")
                elif nl == "unir_fp_plantilla_actividad_2_ed.docx":
                    safe_rename(folder, f.name, "Actividad 2 - Plantilla.docx")

    # 6. Unificar Guías, Programación Didáctica y Criterios de Evaluación sin duplicados
    for dir_base in directorios_base:
        for folder in dir_base.iterdir():
            if not folder.is_dir() or folder.name in ["Secretaría 26-27", "Aula de Información General", "Formación en Empresa (FFE)", "Proyecto Intermodular DAM"]:
                continue
            for f in list(folder.iterdir()):
                if not f.is_file() or f.suffix.lower() != ".pdf":
                    continue
                nl = f.name.lower()
                if any(k in nl for k in ["glosario", "comandos", "introducción y objetivos", "interpretación", "az-900", "coaching", "java", "principios que guían"]):
                    continue
                if nl.startswith("tema") or nl.startswith("unit") or nl.startswith("módulo"):
                    continue
                if any(k in nl for k in ["guia", "guía", "guía"]):
                    safe_rename(folder, f.name, "Guía del Estudiante.pdf")
                elif any(k in nl for k in ["programacion", "programación", "programación"]) and not nl.startswith("resumen"):
                    safe_rename(folder, f.name, "Programación Didáctica.pdf")
                elif "criterio" in nl:
                    safe_rename(folder, f.name, "Criterios de Evaluación.pdf")

    # 7. Correcciones específicas por asignatura
    for dir_base in directorios_base:
        ad = dir_base / "Acceso a Datos"
        if ad.exists():
            safe_rename(ad, "ejercicio mongo.pdf", "Práctica 1 - Ejercicio MongoDB.pdf")
            safe_rename(ad, "Ejercicio MongoDB 1.pdf", "Práctica 1 - Ejercicio MongoDB.pdf")
            safe_rename(ad, "ejercicio mongo java.pdf", "Práctica 2 - Ejercicio MongoDB con Java.pdf")
            safe_rename(ad, "Ejercicio MongoDB Java 2.pdf", "Práctica 2 - Ejercicio MongoDB con Java.pdf")
            safe_rename(ad, "Preguntas_test_AD.pdf", "Banco de Preguntas Tipo Test.pdf")
            safe_rename(ad, "Preguntas tipo test Acceso a Datos.pdf", "Banco de Preguntas Tipo Test.pdf")

        psp = dir_base / "Programación de Servicios y Procesos"
        if psp.exists():
            (psp / "Documento A4 Portada Memoria Proyecto Foto Corporativo Crema y Negro.pdf").unlink(missing_ok=True)
            safe_rename(psp, "prepaso PSP Java_basico.pdf", "Repaso Java Básico para PSP.pdf")
            safe_rename(psp, "Repaso PSP Java básico.pdf", "Repaso Java Básico para PSP.pdf")
            safe_rename(psp, "23-9-2026 - TEMA1 - Programas, ejecutables, procesos y servicios.pdf", "Presentación Clase - Tema 1 (Procesos y Servicios).pdf")
            safe_rename(psp, "Memoria_Actividad1.pdf", "Actividad 1 - Memoria Entregada.pdf")

        an = dir_base / "Arquitecturas en la Nube"
        if an.exists():
            safe_rename(an, "AN_Practica0.pdf", "Práctica 0 - Configuración AWS.pdf")
            safe_rename(an, "AN_Practica2.pdf", "Práctica 2 - Laboratorio AWS.pdf")
            safe_rename(an, "AN_Clase05_diapositivas.pdf", "Presentación Clase 5 - Arquitecturas Cloud.pdf")
            safe_rename(an, "AN_Hoja_comandos_AWS_CLI.pdf", "Guía Rápida - Comandos AWS CLI.pdf")

        di = dir_base / "Desarrollo de Interfaces"
        if di.exists():
            safe_rename(di, "Conceptos sobre el desarrollo de interfaces_.pdf", "Introducción - Conceptos sobre Desarrollo de Interfaces.pdf")
            safe_rename(di, "DI_Tema1_Reto_Entrenamiento_6.pdf", "Práctica - Reto de Entrenamiento 6 (Tema 1).pdf")
            safe_rename(di, "DI_Presentacion_TEMA3.pdf", "Presentación Clase - Tema 3.pdf")
            safe_rename(di, "Tema4_DI_presentacion.pdf", "Presentación Clase - Tema 4.pdf")
            safe_rename(di, "Guia_glosario_tutorial_JavaScript_interfaces.pdf", "Guía y Glosario - Tutorial JavaScript para Interfaces.pdf")

        dig = dir_base / "Digitalización Aplicada"
        if dig.exists():
            safe_rename(dig, "SESIÓNBIENVENIDADAM.pdf", "Presentación - Sesión de Bienvenida.pdf")
            safe_rename(dig, "SESIÓN1DIGDAMT1.pdf", "Presentación Clase 1 - Tema 1.pdf")
            safe_rename(dig, "SESIÓN1DIGDAMT2.pdf", "Presentación Clase 2 - Tema 2.pdf")

        proy = dir_base / "Proyecto Intermodular DAM"
        if proy.exists():
            safe_rename(proy, "Anteproyecto_Final_de_Curso.pdf", "Anteproyecto Final de Curso.pdf")
            safe_rename(proy, "compromiso_trabajos_grupales.pdf", "Compromiso de Trabajos Grupales.pdf")
            safe_rename(proy, "guia.pdf", "Guía del Proyecto Intermodular.pdf")
            safe_rename(proy, "rubrica.pdf", "Rúbrica de Evaluación del Proyecto.pdf")
            safe_rename(proy, "plantilla_proyecto.docx", "Plantilla - Memoria del Proyecto.docx")


def obtener_info_asignatura(asig: str) -> Dict[str, Any]:
    """Obtiene los recursos detallados de una asignatura específica."""
    dir_mat = MATERIALES_DIR / asig
    dir_clases = CLASES_GRABADAS_DIR / asig
    dir_trans = TRANSCRIPCIONES_DIR / asig
    dir_apuntes = APUNTES_DIR / asig
    dir_ejer = EJERCICIOS_DIR / asig
    dir_exam = EXAMENES_DIR / asig
    dir_guias = GUIAS_DIR / asig

    materiales = sorted(
        [
            f for f in dir_mat.glob("*")
            if f.is_file()
            and not f.name.startswith((".", "~$"))
            and f.suffix.lower() in EXTENSIONES_DOCUMENTOS
        ],
        key=_orden_pedagogico_material,
    ) if dir_mat.exists() else []
    transcripciones = sorted([f for f in dir_trans.glob("*.md") if f.is_file()]) if dir_trans.exists() else []
    fichas_clases = sorted([f for f in dir_clases.glob("*_info.json") if f.is_file()]) if dir_clases.exists() else []
    apuntes = sorted([f for f in dir_apuntes.glob("*.md") if f.is_file()]) if dir_apuntes.exists() else []
    ejercicios = sorted([f for f in dir_ejer.glob("*.md") if f.is_file()]) if dir_ejer.exists() else []
    examenes = sorted([f for f in dir_exam.glob("*.md") if f.is_file()]) if dir_exam.exists() else []
    tests_json = sorted([f.stem for f in dir_exam.glob("*.json")], key=_orden_natural_items) if dir_exam.exists() else []
    guias = sorted([f.name for f in dir_guias.glob("*.md")], key=_orden_natural_items) if dir_guias.exists() else []

    # Determinar programa y curso
    if asig in ASIGNATURAS_2DAM:
        prog_id = "dam"
        curso_nombre = "2º Curso"
    elif asig in ASIGNATURAS_1DAM:
        prog_id = "dam"
        curso_nombre = "1º Curso"
    elif asig in MODULOS_CIBER:
        prog_id = "ciber"
        curso_nombre = "Máster Ciberseguridad"
    elif asig in ASIGNATURAS_PFU:
        prog_id = "pfu"
        curso_nombre = "PFU Software"
    elif asig in ASIGNATURAS_BOOTCAMP:
        prog_id = "bootcamp"
        curso_nombre = "Bootcamp Ágil"
    elif asig in ASIGNATURAS_PYTHON:
        prog_id = "python"
        curso_nombre = "Python"
    elif asig in ASIGNATURAS_AWS:
        prog_id = "aws"
        curso_nombre = "AWS Cloud"
    elif asig in ASIGNATURAS_AZURE:
        prog_id = "azure"
        curso_nombre = "Azure AZ-900"
    else:
        prog_id = "dam"
        curso_nombre = "General"

    return {
        "nombre": asig,
        "programa_id": prog_id,
        "curso": curso_nombre,
        "materiales": materiales,
        "total_materiales": len(materiales),
        "transcripciones": transcripciones,
        "total_clases": len(transcripciones),
        "clases_grabadas": fichas_clases,
        "apuntes": apuntes,
        "ejercicios": ejercicios,
        "examenes": examenes,
        "tests": tests_json,
        "guias": guias,
    }


def obtener_catalogo_academico(programa_id: Optional[str] = None, curso_id: Optional[str] = None) -> Dict[str, Dict[str, Any]]:
    """
    Retorna el inventario consolidado filtrado opcionalmente por programa y curso.
    Si no se especifica filtro, devuelve todas las asignaturas activas.
    """
    catalogo = {}

    # Aliases de compatibilidad
    if programa_id == "grado":
        programa_id = "pfu"
    elif programa_id == "cert":
        programa_id = "aws"

    # Recorrer programas
    programas_a_escanear = [programa_id] if programa_id and programa_id in PROGRAMAS else list(PROGRAMAS.keys())

    for p_id in programas_a_escanear:
        p_info = PROGRAMAS[p_id]
        cursos_dict = p_info["cursos"]
        cursos_a_escanear = [curso_id] if curso_id and curso_id in cursos_dict else list(cursos_dict.keys())

        for c_id in cursos_a_escanear:
            c_info = cursos_dict[c_id]
            for asig in c_info["asignaturas"]:
                info = obtener_info_asignatura(asig)
                # Incluir siempre si pertenece al curso solicitado o si tiene materiales
                catalogo[asig] = info

    return catalogo


def obtener_resumen_programas(examenes_dir: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Devuelve un resumen con métricas clave de cada programa para el frontend."""
    resumen = []
    base_exam_dir = examenes_dir or EXAMENES_DIR
    for p_id, p_info in PROGRAMAS.items():
        total_asigs = 0
        total_mats = 0
        total_clases = 0
        total_tests = 0

        for c_id, c_info in p_info["cursos"].items():
            for asig in c_info["asignaturas"]:
                total_asigs += 1
                dir_mat = MATERIALES_DIR / asig
                dir_trans = TRANSCRIPCIONES_DIR / asig
                dir_exam = base_exam_dir / asig

                if dir_mat.exists():
                    total_mats += len([
                        f for f in dir_mat.glob("*")
                        if f.is_file()
                        and not f.name.startswith((".", "~$"))
                        and f.suffix.lower() in EXTENSIONES_DOCUMENTOS
                    ])
                if dir_trans.exists():
                    total_clases += len(list(dir_trans.glob("*.md")))
                if dir_exam.exists():
                    total_tests += len(list(dir_exam.glob("*.json")))

        resumen.append({
            "id": p_id,
            "nombre": p_info["nombre"],
            "titulo_corto": p_info["titulo_corto"],
            "icono": p_info["icono"],
            "badge": p_info["badge"],
            "color": p_info["color"],
            "descripcion": p_info["descripcion"],
            "total_asignaturas": total_asigs,
            "total_materiales": total_mats,
            "total_clases": total_clases,
            "total_tests": total_tests,
            "cursos": [
                {
                    "id": c_id,
                    "nombre": c["nombre"],
                    "asignaturas": c["asignaturas"],
                }
                for c_id, c in p_info["cursos"].items()
            ],
        })
    return resumen
