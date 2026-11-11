"""
Extractor y gestor de Entrenamientos Oficiales y Generados con IA de UNIR (FP DAM).
Analiza los PDFs oficiales del temario y extrae los ejercicios prácticos oficiales,
y permite generar nuevos entrenamientos con IA adaptados al temario y guardarlos en el espacio del alumno.
"""

import json
import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Any, Optional

from src.core.config import MATERIALES_DIR, PROCESADO_DIR, obtener_espacio_usuario, escribir_json_atomico
from src.extractors.pdf_parser import extraer_texto_pdf

CACHE_ENTRENAMIENTOS_DIR = PROCESADO_DIR / "entrenamientos"
CACHE_ENTRENAMIENTOS_DIR.mkdir(parents=True, exist_ok=True)


def normalizar_slug(texto: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_\-]", "_", (texto or "").strip().lower())


def obtener_ruta_pdf_tema(asignatura: str, tema_num: int) -> Optional[Path]:
    """Localiza el archivo PDF oficial del tema dentro de data/materiales."""
    asig_dir = MATERIALES_DIR / asignatura
    if not asig_dir.exists():
        for d in MATERIALES_DIR.iterdir():
            if d.is_dir() and d.name.lower() == asignatura.lower():
                asig_dir = d
                break

    if not asig_dir.exists():
        return None

    patron = rf"Tema\s*0*{tema_num}\b"
    for pdf in sorted(asig_dir.glob("*.pdf")):
        if re.search(patron, pdf.name, re.IGNORECASE):
            return pdf
    return None


def extraer_entrenamientos_de_pdf(ruta_pdf: Path) -> List[Dict[str, Any]]:
    """Extrae los ejercicios de entrenamiento oficiales de un PDF de UNIR."""
    datos_pdf = extraer_texto_pdf(ruta_pdf)
    txt = datos_pdf.get("texto_completo", "")
    if not txt:
        return []

    pos = [m.start() for m in re.finditer(r"Entrenamiento\s+1\b", txt) if m.start() > 2000]
    if not pos:
        return []

    bloque = txt[pos[0]:]
    patron = r"(Entrenamiento\s+(\d+))\s*\n+(.*?)(?=\n+Entrenamiento\s+\d+|\Z)"
    matches = list(re.finditer(patron, bloque, re.DOTALL))
    resultados = []

    for m in matches:
        num = int(m.group(2))
        cuerpo = m.group(3).strip()

        split_sol = re.split(
            r"\n+(?:Desarrollo\s+paso\s+a\s+paso|Soluci[oó]n|Resoluci[oó]n)\s*\n*",
            cuerpo,
            flags=re.IGNORECASE,
        )
        enunciado = split_sol[0].strip()
        enunciado = re.sub(r"^Planteamiento\s+del\s+ejercicio\s*\n*", "", enunciado, flags=re.IGNORECASE).strip()
        solucion = split_sol[1].strip() if len(split_sol) > 1 else ""

        for reg in [
            r"---\s*\[SALTO DE PÁGINA\]\s*---",
            r"Entrenamientos\s*\n+.*?\n+Tema\s+\d+.*?\n+\d+",
            r"Material\s+de\s+estudio\s*\n+.*?\n+Tema\s+\d+.*?\n+\d+",
        ]:
            enunciado = re.sub(reg, "", enunciado)
            solucion = re.sub(reg, "", solucion)

        primera_linea = enunciado.split("\n")[0].strip()
        titulo = primera_linea.split(":")[0] if ":" in primera_linea else primera_linea[:65]
        titulo = re.sub(r"^[\s•▸\-\*]+", "", titulo).strip()
        enunciado = re.sub(r"^[\s•▸\-\*]+", "", enunciado).strip()

        resultados.append({
            "id": f"oficial_{num}",
            "numero": num,
            "titulo": titulo,
            "enunciado": enunciado,
            "solucion": solucion,
            "es_ia": False,
            "origen": "Oficial",
            "dificultad": "Oficial"
        })

    return resultados


def _ruta_ia_usuario(username: str, asignatura: str, tema_num: int) -> Path:
    espacio = obtener_espacio_usuario(username)
    dir_ia = espacio / "entrenamientos_ia"
    dir_ia.mkdir(parents=True, exist_ok=True)
    slug = normalizar_slug(asignatura)
    return dir_ia / f"{slug}_tema_{tema_num}.json"


def obtener_entrenamientos_ia_usuario(username: str, asignatura: str, tema_num: int) -> List[Dict[str, Any]]:
    """Devuelve la lista de entrenamientos generados con IA por el alumno para este tema."""
    if not username:
        return []
    f_path = _ruta_ia_usuario(username, asignatura, tema_num)
    if f_path.exists():
        try:
            with open(f_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    return data
        except Exception:
            pass
    return []


def guardar_entrenamiento_ia_usuario(username: str, asignatura: str, tema_num: int, item: Dict[str, Any]) -> Dict[str, Any]:
    """Guarda un nuevo entrenamiento generado con IA en el espacio personal del alumno."""
    items = obtener_entrenamientos_ia_usuario(username, asignatura, tema_num)
    items.append(item)
    f_path = _ruta_ia_usuario(username, asignatura, tema_num)
    escribir_json_atomico(f_path, items)
    return item


def eliminar_entrenamiento_ia_usuario(username: str, asignatura: str, tema_num: int, reto_id: str) -> bool:
    """Elimina un entrenamiento de IA del espacio personal del alumno."""
    items = obtener_entrenamientos_ia_usuario(username, asignatura, tema_num)
    nuevos = [it for it in items if it.get("id") != reto_id]
    f_path = _ruta_ia_usuario(username, asignatura, tema_num)
    escribir_json_atomico(f_path, nuevos)
    return len(nuevos) < len(items)


def obtener_entrenamientos_tema(asignatura: str, tema_num: int, username: Optional[str] = None) -> Dict[str, Any]:
    """Obtiene los entrenamientos de un tema (oficiales + generados con IA para el usuario actual)."""
    slug_asig = normalizar_slug(asignatura)
    cache_file = CACHE_ENTRENAMIENTOS_DIR / f"{slug_asig}_tema_{tema_num}.json"

    oficiales_datos = None
    if cache_file.exists():
        try:
            with open(cache_file, "r", encoding="utf-8") as f:
                oficiales_datos = json.load(f)
        except Exception:
            pass

    if not oficiales_datos or not oficiales_datos.get("entrenamientos"):
        ruta_pdf = obtener_ruta_pdf_tema(asignatura, tema_num)
        if not ruta_pdf:
            oficiales = []
            pdf_nombre = ""
        else:
            oficiales = extraer_entrenamientos_de_pdf(ruta_pdf)
            pdf_nombre = ruta_pdf.name

        oficiales_datos = {
            "status": "ok",
            "asignatura": asignatura,
            "tema_num": tema_num,
            "pdf_nombre": pdf_nombre,
            "total": len(oficiales),
            "entrenamientos": oficiales,
        }
        if oficiales:
            try:
                with open(cache_file, "w", encoding="utf-8") as f:
                    json.dump(oficiales_datos, f, ensure_ascii=False, indent=2)
            except Exception:
                pass

    oficiales = oficiales_datos.get("entrenamientos", [])
    # Asegurar campos clave
    for idx, ofi in enumerate(oficiales):
        if "id" not in ofi:
            ofi["id"] = f"oficial_{ofi.get('numero', idx + 1)}"
        if "es_ia" not in ofi:
            ofi["es_ia"] = False
        if "origen" not in ofi:
            ofi["origen"] = "Oficial"

    # Cargar retos IA del usuario si existe sesión
    retos_ia = obtener_entrenamientos_ia_usuario(username or "", asignatura, tema_num) if username else []

    # Ajustar numeración secuencial limpia
    todos = list(oficiales)
    offset = len(oficiales)
    for i, ia_r in enumerate(retos_ia):
        copia = dict(ia_r)
        copia["numero_relativo"] = offset + i + 1
        todos.append(copia)

    return {
        "status": "ok",
        "asignatura": asignatura,
        "tema_num": tema_num,
        "pdf_nombre": oficiales_datos.get("pdf_nombre", ""),
        "total_oficiales": len(oficiales),
        "total_ia": len(retos_ia),
        "total": len(todos),
        "entrenamientos": todos,
    }


def generar_entrenamiento_con_ia(
    username: str,
    asignatura: str,
    tema_num: int,
    tema_titulo: str,
    dificultad: str = "examen",
    enfoque: str = "",
    api_key_override: Optional[str] = None
) -> Dict[str, Any]:
    """Genera un nuevo reto práctico con IA pedagógicamente adaptado al temario de UNIR."""
    from src.ai.llm_client import LLMClient

    # Obtener títulos de los ejercicios ya existentes para no repetirlos
    existentes = obtener_entrenamientos_tema(asignatura, tema_num, username)
    titulos_existentes = [e.get("titulo", "") for e in existentes.get("entrenamientos", []) if e.get("titulo")]
    texto_existentes = "\n".join(f"- {t}" for t in titulos_existentes) if titulos_existentes else "- Ninguno registrado aún."

    # Intentar obtener fragmento o contexto de la guía de estudio si existe
    contexto_guia = ""
    guia_dir = PROCESADO_DIR / "guias" / asignatura
    if guia_dir.exists():
        for g in guia_dir.glob(f"*Tema*{tema_num}*.md"):
            try:
                with open(g, "r", encoding="utf-8") as f:
                    txt = f.read(4000)
                    contexto_guia = f"\nResumen del temario de la unidad:\n{txt[:2500]}\n"
                    break
            except Exception:
                pass

    dif_nombre = {
        "facil": "Práctica Básica / Afianzamiento",
        "medio": "Nivel Intermedio",
        "examen": "Nivel Examen Oficial (Práctico)",
        "avanzado": "Nivel Avanzado / Caso Real de Empresa"
    }.get((dificultad or "").lower(), "Nivel Examen Oficial (Práctico)")

    enfoque_texto = f"Enfoque o requerimiento específico del alumno: {enfoque}\n" if enfoque.strip() else ""

    prompt = f"""Eres el Diseñador Pedagógico y Profesor Titular de FP Grado Superior DAM - Desarrollo de Aplicaciones Multiplataforma para la materia "{asignatura}".
Unidad de estudio: Tema {tema_num} - {tema_titulo}
{contexto_guia}
Ejercicios y retos que el alumno ya tiene disponibles en este tema (NO repitas exactamente ninguno de ellos):
{texto_existentes}

{enfoque_texto}Nivel de dificultad requerido: {dif_nombre}

Tu tarea es diseñar un NUEVO RETO PRÁCTICO DE PROGRAMACIÓN (Entrenamiento de Código) para que el alumno lo implemente por sí mismo en su IDE (IntelliJ, Android Studio, VS Code, etc.).

Reglas indispensables:
1. Lenguaje oficial: Utiliza el lenguaje correspondiente de la materia (Java para materias de DAM como Acceso a Datos, PSP, etc.; Kotlin/Java para PMDM; Python para Python; SQL si aplica).
2. El planteamiento del reto debe incluir:
   - Objetivo del reto
   - Requisitos técnicos paso a paso (clases a crear, métodos, atributos, control de excepciones, tipos de datos)
   - Datos de prueba o ejemplo de ejecución en consola.
3. La pista ("pista") debe ser una guía conceptual clara que oriente al estudiante sin desvelar la solución de golpe.
4. La solución ("solucion") debe incluir:
   - Explicación breve de la arquitectura de la solución
   - Código fuente completo, limpio, moderno, documentado y listo para copiar y compilar sin errores.

Devuelve EXCLUSIVAMENTE un objeto JSON válido con la siguiente estructura:
{{
  "titulo": "Título conciso y descriptivo del reto (máx. 6-8 palabras)",
  "dificultad": "{dif_nombre}",
  "enunciado": "Planteamiento completo y detallado del reto con sus requisitos técnicos y ejemplos...",
  "pista": "Pista conceptual para orientar al alumno si se atasca...",
  "solucion": "Explicación del desarrollo y código fuente Java/Kotlin completo..."
}}"""

    client = LLMClient()
    resp = client.generar_contenido(
        prompt=prompt,
        temperatura=0.35,
        response_mime_type="application/json",
        api_key_override=api_key_override
    )

    if not resp or not resp.strip():
        raise ValueError("El motor de IA no devolvió respuesta. Comprueba tu clave de API.")

    limpio = resp.strip()
    if limpio.startswith("```"):
        limpio = limpio.split("```")[1]
        if limpio.startswith("json"):
            limpio = limpio[4:]
    limpio = limpio.strip()

    datos_json = json.loads(limpio)
    reto_id = f"ia_{uuid.uuid4().hex[:8]}"
    item_nuevo = {
        "id": reto_id,
        "titulo": datos_json.get("titulo", f"Reto IA: {tema_titulo[:30]}"),
        "dificultad": datos_json.get("dificultad", dif_nombre),
        "enunciado": datos_json.get("enunciado", ""),
        "pista": datos_json.get("pista", ""),
        "solucion": datos_json.get("solucion", ""),
        "es_ia": True,
        "origen": "Generado con IA",
        "fecha_creacion": datetime.now().isoformat(),
        "enfoque": enfoque
    }

    guardar_entrenamiento_ia_usuario(username, asignatura, tema_num, item_nuevo)
    return item_nuevo
