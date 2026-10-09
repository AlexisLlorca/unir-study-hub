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
        "descripcion": "Ciclo Formativo de Grado Superior en Desarrollo de Aplicaciones Multiplataforma.",
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
    "grado": {
        "id": "grado",
        "nombre": "PFU / Grado en Ing. del Software",
        "titulo_corto": "PFU / Grado",
        "icono": "fa-university",
        "badge": "Universidad (UNIR)",
        "color": "indigo",
        "descripcion": "Programa de Formación Universitaria y Grado en Ingeniería del Software.",
        "cursos": {
            "pfu": {
                "id": "pfu",
                "nombre": "Módulos PFU / Grado",
                "asignaturas": [
                    "PFU - Ingeniería del Software",
                    "PFU - Estructura de Datos y Algoritmos",
                    "PFU - Arquitectura de Computadores",
                    "PFU - Matemáticas y Lógica Computacional",
                ],
            }
        },
    },
    "cert": {
        "id": "cert",
        "nombre": "Certificaciones & Nuevas Formaciones",
        "titulo_corto": "Certificaciones",
        "icono": "fa-certificate",
        "badge": "Formación Continua",
        "color": "amber",
        "descripcion": "Especializaciones y certificaciones técnicas (AWS, Linux, Redes, Docker, Seguridad).",
        "cursos": {
            "cert": {
                "id": "cert",
                "nombre": "Cursos y Certificaciones",
                "asignaturas": [
                    "AWS Certified Solutions Architect",
                    "Linux Professional Institute (LPIC-1)",
                    "Docker & Kubernetes Developer",
                    "CompTIA Security+ / CEH",
                ],
            }
        },
    },
}

# Aliases para compatibilidad con código existente
ASIGNATURAS_2DAM = PROGRAMAS["dam"]["cursos"]["2dam"]["asignaturas"]
ASIGNATURAS_1DAM = PROGRAMAS["dam"]["cursos"]["1dam"]["asignaturas"]
MODULOS_CIBER = PROGRAMAS["ciber"]["cursos"]["master"]["asignaturas"]
ASIGNATURAS_PFU = PROGRAMAS["grado"]["cursos"]["pfu"]["asignaturas"]


def normalizar_nombre_asignatura(nombre_sucio: str) -> str:
    """
    Convierte cualquier nombre de carpeta, curso de Canvas o backup local
    en el nombre canónico oficial y limpio de la asignatura.
    """
    s = nombre_sucio.lower().strip()

    # Si ya es un nombre canónico oficial, no tocar
    todos_canonicos = set(ASIGNATURAS_2DAM + ASIGNATURAS_1DAM + MODULOS_CIBER + ASIGNATURAS_PFU)
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

    # 2. 1º DAM (Prioridad si indica primer curso, 1º o asignaturas exclusivas de 1º)
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
    if ("ipe" in s or "empleabilidad" in s) and ("ii" not in s and "2" not in s and "segundo" not in s):
        return "1º DAM - Itinerario para la Empleabilidad I"
    if ("inglés" in s or "ingles" in s) and ("profesional" not in s or "gs" not in s) and ("2" not in s and "segundo" not in s):
        return "1º DAM - Inglés Técnico I"

    # 3. 2º DAM
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

    # 4. PFU / Grado
    if "ingeniería del software" in s or "ingenieria del software" in s:
        return "PFU - Ingeniería del Software"
    if "estructura de datos" in s or "algoritmos" in s:
        return "PFU - Estructura de Datos y Algoritmos"
    if "arquitectura de computadores" in s:
        return "PFU - Arquitectura de Computadores"
    if "matemáticas" in s or "logica" in s or "lógica" in s:
        return "PFU - Matemáticas y Lógica Computacional"

    return nombre_sucio.strip()


def sincronizar_estudios_externos():
    """
    Escanea carpetas externas en /home/alexllorca/Respaldo_Estudios/
    (Ciberseguridad y 1º DAM) y las vincula limpiamente en data/materiales/
    mediante enlaces simbólicos directos (sin duplicar espacio en disco).
    """
    MATERIALES_DIR.mkdir(parents=True, exist_ok=True)

    # 1. Vincular Máster en Ciberseguridad (M1 a M7)
    ciber_root = Path("/home/alexllorca/Respaldo_Estudios/Temario Ciberseguridad")
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
            src_temas = ciber_root / m_folder / "Temas"
            if not src_temas.exists():
                src_temas = ciber_root / m_folder

            if src_temas.exists():
                dest_dir = MATERIALES_DIR / m_nombre
                dest_dir.mkdir(parents=True, exist_ok=True)

                for f in src_temas.glob("*"):
                    if f.is_file() and not f.name.startswith("."):
                        target_link = dest_dir / f.name
                        if not target_link.exists():
                            try:
                                os.symlink(f.resolve(), target_link)
                            except Exception:
                                pass

    # 2. Vincular todas las Asignaturas de 1º DAM desde el respaldo local
    backup_dam_root = Path("/home/alexllorca/Respaldo_Estudios/Proyectos/FP DAM unir")
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
            dir_src = backup_dam_root / folder_src
            if dir_src.exists():
                dir_dest = MATERIALES_DIR / canonico
                dir_dest.mkdir(parents=True, exist_ok=True)

                for f in dir_src.glob("**/*"):
                    if f.is_file() and f.suffix.lower() in [".pdf", ".docx", ".zip", ".psc"]:
                        dest_link = dir_dest / f.name
                        if not dest_link.exists():
                            try:
                                os.symlink(f.resolve(), dest_link)
                            except Exception:
                                pass

    # 3. Crear carpetas de PFU y Certificaciones para cuando haya material
    for asig_pfu in ASIGNATURAS_PFU:
        (MATERIALES_DIR / asig_pfu).mkdir(parents=True, exist_ok=True)


def reorganizar_biblioteca_completa():
    """
    Escanea data/materiales, data/clases_grabadas y data/procesado/transcripciones
    y agrupa todos los archivos en carpetas canónicas limpias.
    Elimina carpetas intermedias caóticas como 'Tema 1', 'Trimestre 1', 'Unit 1'.
    """
    sincronizar_estudios_externos()

    backup_root = Path("/home/alexllorca/Respaldo_Estudios/Proyectos/FP DAM unir")
    backup_index = {}
    if backup_root.exists():
        for p in backup_root.glob("**/*"):
            if p.is_file():
                backup_index[p.name] = p

    # 1. Reorganizar data/materiales/
    if MATERIALES_DIR.exists():
        todos_canonicos = set(ASIGNATURAS_2DAM + ASIGNATURAS_1DAM + MODULOS_CIBER + ASIGNATURAS_PFU)
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


def obtener_info_asignatura(asig: str) -> Dict[str, Any]:
    """Obtiene los recursos detallados de una asignatura específica."""
    dir_mat = MATERIALES_DIR / asig
    dir_clases = CLASES_GRABADAS_DIR / asig
    dir_trans = TRANSCRIPCIONES_DIR / asig
    dir_apuntes = APUNTES_DIR / asig
    dir_ejer = EJERCICIOS_DIR / asig
    dir_exam = EXAMENES_DIR / asig
    dir_guias = GUIAS_DIR / asig

    materiales = sorted([f for f in dir_mat.glob("*") if f.is_file()]) if dir_mat.exists() else []
    transcripciones = sorted([f for f in dir_trans.glob("*.md") if f.is_file()]) if dir_trans.exists() else []
    fichas_clases = sorted([f for f in dir_clases.glob("*_info.json") if f.is_file()]) if dir_clases.exists() else []
    apuntes = sorted([f for f in dir_apuntes.glob("*.md") if f.is_file()]) if dir_apuntes.exists() else []
    ejercicios = sorted([f for f in dir_ejer.glob("*.md") if f.is_file()]) if dir_ejer.exists() else []
    examenes = sorted([f for f in dir_exam.glob("*.md") if f.is_file()]) if dir_exam.exists() else []
    tests_json = sorted([f.stem for f in dir_exam.glob("*.json")]) if dir_exam.exists() else []
    guias = sorted([f.name for f in dir_guias.glob("*.md")]) if dir_guias.exists() else []

    # Determinar programa y curso
    prog_id = "dam"
    curso_nombre = "2º DAM"
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
        prog_id = "grado"
        curso_nombre = "PFU / Grado"
    else:
        prog_id = "cert"
        curso_nombre = "Certificaciones"

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


def obtener_resumen_programas() -> List[Dict[str, Any]]:
    """Devuelve un resumen con métricas clave de cada programa para el frontend."""
    resumen = []
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
                dir_exam = EXAMENES_DIR / asig

                if dir_mat.exists():
                    total_mats += len([f for f in dir_mat.glob("*") if f.is_file()])
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
