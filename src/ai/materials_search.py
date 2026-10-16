"""
Motor de Búsqueda RAG en Materiales Oficiales (PDFs, DOCX, Guías) de UNIR Study Hub.
Permite a NOVA consultar temarios oficiales, enunciados de actividades y pautas docentes.
"""

import logging
import re
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Optional, List, Dict, Any

import pypdf

logging.getLogger("pypdf").setLevel(logging.ERROR)

from src.core.config import MATERIALES_DIR


def extraer_texto_docx(ruta_docx: Path) -> str:
    """Extrae texto plano de un documento .docx mediante XML interno."""
    try:
        with zipfile.ZipFile(ruta_docx) as z:
            xml_content = z.read("word/document.xml")
            tree = ET.fromstring(xml_content)
            parrafos = []
            for p_node in tree.iter("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}p"):
                textos = [t.text for t in p_node.iter("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t") if t.text]
                if textos:
                    parrafos.append("".join(textos))
            return "\n".join(parrafos)
    except Exception:
        return ""


def extraer_texto_pdf(ruta_pdf: Path, max_paginas: int = 15) -> List[Dict[str, Any]]:
    """Extrae páginas de un PDF con su número de página."""
    paginas = []
    try:
        reader = pypdf.PdfReader(str(ruta_pdf))
        num_pags = min(len(reader.pages), max_paginas)
        for i in range(num_pags):
            texto = reader.pages[i].extract_text() or ""
            if texto.strip():
                paginas.append({"pagina": i + 1, "texto": texto.strip()})
    except Exception:
        pass
    return paginas


def buscar_en_materiales(query: str, asignatura: Optional[str] = None, max_chars: int = 4000) -> str:
    """
    Busca información relevante en los temarios, guías y actividades de MATERIALES_DIR.
    Devuelve un bloque de contexto formateado para el prompt de NOVA.
    """
    if not MATERIALES_DIR.exists():
        return ""

    q_lower = query.lower()
    terminos = [w for w in re.findall(r'\b[a-zA-ZáéíóúÁÉÍÓÚñÑ0-9]{3,}\b', q_lower) if len(w) > 3]
    if not terminos:
        return ""

    carpetas = []
    if asignatura:
        asig_lower = asignatura.lower()
        for d in MATERIALES_DIR.iterdir():
            if d.is_dir() and (asig_lower in d.name.lower() or d.name.lower() in asig_lower):
                carpetas.append(d)
    if not carpetas:
        carpetas = [d for d in MATERIALES_DIR.iterdir() if d.is_dir()]

    fragmentos_encontrados = []

    for d in carpetas:
        asig_nombre = d.name
        # Buscar en archivos DOCX (actividades, tareas)
        for f_docx in d.glob("*.docx"):
            if f_docx.name.startswith("~$") or "plantilla" in f_docx.name.lower():
                continue
            nombre_f = f_docx.name.lower()
            coincide_nombre = any(t in nombre_f for t in terminos) or any(k in q_lower for k in ["actividad", "tarea", "entrega", "practica"])
            if coincide_nombre or any(t in nombre_f for t in ["act", "tisup"]):
                texto_doc = extraer_texto_docx(f_docx)
                if texto_doc:
                    # Comprobar relevancia
                    score = sum(1 for t in terminos if t in texto_doc.lower())
                    if score > 0 or coincide_nombre:
                        fragmentos_encontrados.append({
                            "fuente": f_docx.name,
                            "tipo": "Enunciado de Actividad / Tarea",
                            "asignatura": asig_nombre,
                            "texto": texto_doc[:2500],
                            "score": score + 20 if coincide_nombre else score
                        })

        # Buscar en archivos PDF (temarios, guías)
        for f_pdf in d.glob("*.pdf"):
            nombre_f = f_pdf.name.lower()
            # Priorizar si el nombre del archivo contiene palabras clave de la query (ej. 'tema 1', 'ficheros', etc.)
            relevancia_archivo = any(t in nombre_f for t in terminos) or ("tema" in q_lower and "tema" in nombre_f)
            if relevancia_archivo or any(k in q_lower for k in ["temario", "examen", "guia", "criterio"]):
                pags = extraer_texto_pdf(f_pdf, max_paginas=20)
                for p in pags:
                    txt_p = p["texto"].lower()
                    score = sum(1 for t in terminos if t in txt_p)
                    if score > 0:
                        fragmentos_encontrados.append({
                            "fuente": f"{f_pdf.name} (Pág. {p['pagina']})",
                            "tipo": "Temario Oficial UNIR",
                            "asignatura": asig_nombre,
                            "texto": p["texto"][:1200],
                            "score": score + (10 if relevancia_archivo else 0)
                        })

    if not fragmentos_encontrados:
        return ""

    fragmentos_encontrados.sort(key=lambda x: x["score"], reverse=True)
    mejores = fragmentos_encontrados[:3]

    salida = ["\n[EVIDENCIAS Y CONTENIDO OFICIAL DEL TEMARIO / ACTIVIDADES UNIR]:"]
    total_len = 0
    for f in mejores:
        bloque = f"📄 Fuente: {f['fuente']} [{f['asignatura']} - {f['tipo']}]\n{f['texto'].strip()}\n---"
        if total_len + len(bloque) > max_chars:
            break
        salida.append(bloque)
        total_len += len(bloque)

    return "\n".join(salida) if total_len > 0 else ""
