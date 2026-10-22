"""
Servidor Web y Dashboard Gráfico Interactivo para UNIR Study Hub.
Plataforma Académica Multi-Titulación 100% Web:
  - FP Grado Superior DAM (1º y 2º Curso)
  - Máster en Ciberseguridad (Módulos Oficiales M1 a M7)
  - PFU / Grado en Ingeniería del Software (UNIR)
  - Certificaciones Técnicas & Formación Continua
Permite seleccionar programas, explorar materias, leer temarios y transcripciones,
generar guías y tests con IA y realizar simulacros tipo test directamente desde la web.
"""

import os
import re
import json
import socket
import asyncio
from pathlib import Path
from typing import Dict, Any, Optional, List

import uvicorn
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from src.core.config import (
    console,
    BASE_DIR,
    MATERIALES_DIR,
    TRANSCRIPCIONES_DIR,
    CLASES_GRABADAS_DIR,
    GUIAS_DIR,
    EXAMENES_DIR,
    EJERCICIOS_DIR,
)
from src.core.academic_structure import (
    PROGRAMAS,
    ASIGNATURAS_2DAM,
    ASIGNATURAS_1DAM,
    MODULOS_CIBER,
    ASIGNATURAS_PFU,
    obtener_catalogo_academico,
    obtener_resumen_programas,
    reorganizar_biblioteca_completa,
    sincronizar_estudios_externos,
    obtener_info_asignatura,
)
from src.core.progress_tracker import GestorProgreso
from src.core.config import RESPALDO_DIR, get_hub_access_token
from src.ai.tutor import TutorIA
from src.generators.study_guide_generator import GeneradorGuiaEstudio
from src.ui.security import (
    AccesoSeguroMiddleware,
    nombre_seguro,
    obtener_ip_red_local,
    ruta_dentro_de,
    ruta_segura_lectura,
)

app = FastAPI(title="UNIR Study Hub — Hub Multi-Titulación", version="3.3.0")

# Sin CORS: el frontend se sirve desde este mismo servidor, así que ninguna web externa
# necesita (ni debe poder) leer las respuestas de la API.
HUB_ACCESS_TOKEN = get_hub_access_token()
IP_LOCAL = obtener_ip_red_local()
app.add_middleware(AccesoSeguroMiddleware, token=HUB_ACCESS_TOKEN, ip_local=IP_LOCAL)

tracker = GestorProgreso()
tutor_ia = TutorIA()
generador = GeneradorGuiaEstudio(tutor_ia)


# --- API ENDPOINTS ---

@app.get("/api/programas")
def api_programas():
    """Retorna la lista de programas académicos con todas sus métricas y cursos."""
    return obtener_resumen_programas()


@app.get("/api/catalogo")
def api_catalogo(programa: Optional[str] = None, curso: Optional[str] = None):
    """Retorna el catálogo consolidado de asignaturas filtrado por programa y curso."""
    catalogo = obtener_catalogo_academico(programa_id=programa, curso_id=curso)
    resultado = []

    for asig, info in catalogo.items():
        dir_guias = GUIAS_DIR / asig
        dir_examenes = EXAMENES_DIR / asig

        guias = [f.name for f in dir_guias.glob("*.md")] if dir_guias.exists() else []
        tests = [f.stem for f in dir_examenes.glob("*.json")] if dir_examenes.exists() else []

        resumen_progreso = tracker.obtener_resumen_asignatura(asig)

        resultado.append({
            "nombre": asig,
            "programa_id": info["programa_id"],
            "curso": info["curso"],
            "total_materiales": len(info["materiales"]),
            "materiales": [f.name for f in info["materiales"]],
            "total_clases": len(info["transcripciones"]),
            "clases": [
                {
                    "nombre": t.stem,
                    "fecha": t.stem[:10] if len(t.stem) >= 10 and t.stem[:4].isdigit() else "Reciente",
                    "titulo": t.stem[11:] if len(t.stem) >= 11 and t.stem[:4].isdigit() else t.stem,
                    "archivo": str(t.relative_to(BASE_DIR)),
                }
                for t in info["transcripciones"]
            ],
            "guias": guias,
            "tests": tests,
            "progreso": resumen_progreso,
        })

    return resultado


@app.get("/api/progreso")
def api_progreso():
    """Retorna el estado del expediente, banco de fallos y estadísticas globales."""
    datos = tracker._cargar()
    historial = datos.get("historial_tests", [])
    falladas = datos.get("preguntas_falladas", [])
    notas = [r["nota"] for r in historial]
    media = round(sum(notas) / len(notas), 2) if notas else 0.0

    return {
        "media_global": media,
        "tests_realizados": len(historial),
        "ultimas_calificaciones": datos.get("ultimas_calificaciones", {}),
        "historial": historial[-15:],
        "preguntas_falladas": falladas,
        "total_fallos_pendientes": len(falladas),
    }


@app.get("/api/preguntas-falladas")
def api_preguntas_falladas(asig: Optional[str] = None):
    """Devuelve las preguntas pendientes en el banco de fallos para el Modo Repaso de Fallos."""
    asig_limpia = nombre_seguro(asig, "asignatura") if asig else None
    return tracker.obtener_preguntas_falladas(asignatura=asig_limpia)


@app.get("/api/acceso-movil")
def api_acceso_movil(request: Request):
    """Devuelve el enlace directo con token para abrir el Hub en el móvil dentro de la misma red Wi-Fi."""
    puerto = request.url.port or 8080
    url_movil = f"http://{IP_LOCAL}:{puerto}/?token={HUB_ACCESS_TOKEN}"
    return {
        "ip_local": IP_LOCAL,
        "puerto": puerto,
        "token": HUB_ACCESS_TOKEN,
        "url_movil": url_movil,
    }


@app.get("/api/contenido")
def api_contenido(ruta: str):
    """Devuelve el contenido en texto de cualquier guía, transcripción o documento .docx dentro de data/."""
    archivo = ruta_segura_lectura(ruta, extensiones={".md", ".txt", ".json", ".docx"})
    if archivo.suffix.lower() == ".docx":
        from src.ai.materials_search import extraer_texto_docx
        parrafos = [p.strip() for p in extraer_texto_docx(archivo).splitlines() if p.strip()]
        contenido = f"# 📄 {archivo.name}\n\n" + "\n\n".join(parrafos) if parrafos else "*(Documento Word sin texto extraíble)*"
        return {"contenido": contenido, "nombre": archivo.name}
    with open(archivo, "r", encoding="utf-8") as f:
        return {"contenido": f.read(), "nombre": archivo.name}


@app.get("/api/archivo-raw")
def api_archivo_raw(ruta: str):
    """Sirve archivos directamente (como PDFs, DOCX o imágenes) para visores integrados, validando la ruta."""
    archivo = ruta_segura_lectura(
        ruta,
        extensiones={".pdf", ".docx", ".png", ".jpg", ".jpeg", ".webp", ".svg", ".md", ".txt", ".mp3", ".mp4", ".wav", ".m4a"},
    )
    media_type = "application/pdf" if archivo.suffix.lower() == ".pdf" else None
    return FileResponse(archivo, media_type=media_type)


@app.get("/api/test/{asignatura}/{test_name}")
def api_obtener_test(asignatura: str, test_name: str):
    """Devuelve las preguntas de un test en JSON."""
    asig_limpia = nombre_seguro(asignatura, "asignatura")
    stem = nombre_seguro(test_name.replace(".json", ""), "test")
    archivo = ruta_dentro_de(EXAMENES_DIR, asig_limpia, f"{stem}.json")
    if not archivo.exists() or not archivo.is_file():
        raise HTTPException(status_code=404, detail="Test no encontrado")

    with open(archivo, "r", encoding="utf-8") as f:
        return json.load(f)


class TestSubmission(BaseModel):
    asignatura: str
    tema: str
    nota: float
    aciertos: int
    total: int
    preguntas_falladas: Optional[List[Dict[str, Any]]] = None
    enunciados_acertados: Optional[List[str]] = None


@app.post("/api/test/guardar")
def api_guardar_test(sub: TestSubmission):
    """Registra la calificación de un test realizado en la web y sincroniza el banco de fallos."""
    tracker.registrar_resultado_test(
        asignatura=nombre_seguro(sub.asignatura, "asignatura"),
        tema=sub.tema.strip()[:200],
        nota=sub.nota,
        aciertos=sub.aciertos,
        total=sub.total,
        preguntas_falladas=sub.preguntas_falladas,
        enunciados_acertados=sub.enunciados_acertados,
    )
    return {"status": "ok"}


@app.delete("/api/guia/{asignatura}/{guia_name}")
def api_eliminar_guia(asignatura: str, guia_name: str):
    """Elimina una guía de estudio generada."""
    asig_limpia = nombre_seguro(asignatura, "asignatura")
    guia_limpia = nombre_seguro(guia_name, "guía")
    guia_path = ruta_dentro_de(GUIAS_DIR, asig_limpia, guia_limpia)
    if not guia_path.exists() or not guia_path.is_file():
        raise HTTPException(status_code=404, detail="Guía no encontrada")

    try:
        guia_path.unlink()
        return {"ok": True, "mensaje": f"Guía {guia_limpia} eliminada correctamente"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error al eliminar la guía: {str(e)}")


@app.delete("/api/test/{asignatura}/{test_name}")
def api_eliminar_test(asignatura: str, test_name: str):
    """Elimina un simulacro de test (JSON y Markdown acompañante si existe)."""
    asig_limpia = nombre_seguro(asignatura, "asignatura")
    stem = nombre_seguro(test_name.replace(".json", "").replace(".md", ""), "test")
    f_json = ruta_dentro_de(EXAMENES_DIR, asig_limpia, f"{stem}.json")
    f_md = ruta_dentro_de(EXAMENES_DIR, asig_limpia, f"{stem}.md")

    eliminado = False
    try:
        if f_json.exists() and f_json.is_file():
            f_json.unlink()
            eliminado = True
        if f_md.exists() and f_md.is_file():
            f_md.unlink()
            eliminado = True
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error al eliminar el test: {str(e)}")

    if not eliminado:
        raise HTTPException(status_code=404, detail="Test no encontrado")

    return {"ok": True, "mensaje": f"Test {stem} eliminado correctamente"}


class GenerarGuiaRequest(BaseModel):
    asignatura: str
    nombre_tema: str
    archivo_pdf: Optional[str] = None
    profundidad: Optional[str] = "media"
    num_preguntas: Optional[int] = 10


def _buscar_pdf_relevante(dir_mats: Path, nombre_tema: str) -> Optional[Path]:
    """
    Busca el PDF más relevante para un tema sin caer en falsos positivos de fechas (ej. 2026-10-01)
    ni confundir 'Tema 1' con 'Tema 10'.
    """
    if not dir_mats.exists():
        return None
    pdfs = sorted(dir_mats.glob("*.pdf"))
    if not pdfs:
        return None

    # 1. Coincidencia explícita por número de tema/unidad con límite de palabra (evita Tema 1 -> Tema 10)
    num_match = re.search(r"(?:tema|ud|unidad|bloque|modulo|módulo)\s*0*(\d+)\b|\b0*(\d+)\b", nombre_tema.lower())
    if num_match:
        num = num_match.group(1) or num_match.group(2)
        patron_tema = re.compile(
            rf"(?:^|[_\s\-.])(?:tema|ud|unidad|bloque|m[oó]dulo|t)[\s_\-.]*0*{num}(?!\d)(?:\b|[_\s\-.])|^0*{num}(?!\d)[_\s\-.]",
            re.IGNORECASE,
        )
        for f in pdfs:
            if patron_tema.search(f.stem):
                return f

    # 2. Coincidencia por título normalizado (cuando no lleva número o busca por palabras)
    q_norm = re.sub(r"[^\w]", "", nombre_tema.lower())
    for f in pdfs:
        f_norm = re.sub(r"[^\w]", "", f.stem.lower())
        if q_norm and len(q_norm) >= 4 and not re.search(r"\d+$", q_norm) and (q_norm in f_norm or f_norm in q_norm):
            return f

    # 3. Solo si la asignatura tiene un único PDF en total lo usamos por defecto
    if len(pdfs) == 1:
        return pdfs[0]
    return None


def _buscar_transcripcion_relevante(dir_trans: Path, nombre_tema: str) -> Optional[Path]:
    """
    Busca únicamente una transcripción que corresponda al tema pedido.
    Si ninguna coincide, devuelve None (nunca una clase arbitraria de otro tema).
    """
    if not dir_trans.exists():
        return None
    trans = sorted(dir_trans.glob("*.md"))
    if not trans:
        return None

    num_match = re.search(r"(?:tema|ud|unidad|sesi[oó]n|clase)\s*0*(\d+)\b", nombre_tema.lower())
    if num_match:
        num = num_match.group(1)
        patron = re.compile(rf"(?:tema|ud|unidad|sesi[oó]n|clase)[\s_\-.]*0*{num}(?!\d)\b", re.IGNORECASE)
        for t in trans:
            if patron.search(t.stem):
                return t

    q_norm = re.sub(r"[^\w]", "", nombre_tema.lower())
    for t in trans:
        t_norm = re.sub(r"[^\w]", "", t.stem.lower())
        if q_norm and len(q_norm) >= 5 and not re.search(r"\d+$", q_norm) and (q_norm in t_norm or t_norm in q_norm):
            return t

    return None


@app.post("/api/generar-guia")
def api_generar_guia(req: GenerarGuiaRequest):
    """Genera una guía de estudio y test interactivo con IA desde la web."""
    asig_limpia = nombre_seguro(req.asignatura, "asignatura")
    ruta_pdf = None
    if req.archivo_pdf:
        pdf_limpio = nombre_seguro(req.archivo_pdf, "archivo_pdf")
        p = ruta_dentro_de(MATERIALES_DIR, asig_limpia, pdf_limpio)
        if p.exists() and p.is_file():
            ruta_pdf = p

    if not ruta_pdf:
        ruta_pdf = _buscar_pdf_relevante(MATERIALES_DIR / asig_limpia, req.nombre_tema)

    ruta_trans = _buscar_transcripcion_relevante(TRANSCRIPCIONES_DIR / asig_limpia, req.nombre_tema)

    if not ruta_pdf and not ruta_trans:
        raise HTTPException(
            status_code=400,
            detail="No se encontró un PDF ni videoclase que coincida con ese nombre de tema. Selecciona el documento PDF en el desplegable.",
        )

    res = generador.generar_paquete_tema(
        asignatura=asig_limpia,
        nombre_tema=req.nombre_tema,
        ruta_pdf=ruta_pdf,
        ruta_transcripcion=ruta_trans,
        profundidad=req.profundidad or "media",
        num_preguntas=req.num_preguntas,
    )

    tiene_guia = bool(res.get("guia"))
    tiene_test = bool(res.get("test_json"))

    if not tiene_guia and not tiene_test:
        raise HTTPException(
            status_code=500,
            detail="No se pudo generar ni la guía ni el test. El servicio de IA está temporalmente saturado, por favor intenta en unos momentos."
        )

    from src.core.cloud_sync import respaldar_archivo_en_hf_async
    for k in ("guia", "test_json", "test_md"):
        if res.get(k):
            respaldar_archivo_en_hf_async(Path(res[k]))

    return {
        "status": "ok",
        "resultado": {
            "guia": str(res.get("guia")) if tiene_guia else None,
            "test_json": str(res.get("test_json")) if tiene_test else None,
            "test_md": str(res.get("test_md")) if res.get("test_md") else None,
        },
        "tiene_guia": tiene_guia,
        "tiene_test": tiene_test,
    }


class GuardarGuiaChatRequest(BaseModel):
    asignatura: str
    titulo: str
    contenido: str


@app.post("/api/guardar-guia-chat")
def api_guardar_guia_chat(req: GuardarGuiaChatRequest):
    """Guarda una respuesta o guía generada en el chat de NOVA directamente como guía de estudio oficial."""
    if not req.asignatura or not req.contenido.strip():
        raise HTTPException(status_code=400, detail="Faltan datos de asignatura o contenido")

    slug_asig = nombre_seguro(req.asignatura, "asignatura")
    slug_titulo = re.sub(r'[\\/*?:"<>|.]', "", req.titulo).strip().replace(" ", "_")
    if not slug_titulo:
        slug_titulo = "Guia_Clases_NOVA"
    if not slug_titulo.lower().endswith("_guia"):
        slug_titulo = f"{slug_titulo}_guia"
    slug_titulo = nombre_seguro(slug_titulo, "título")

    dir_guia = ruta_dentro_de(GUIAS_DIR, slug_asig)
    dir_guia.mkdir(parents=True, exist_ok=True)
    ruta_archivo = ruta_dentro_de(dir_guia, f"{slug_titulo}.md")

    try:
        with open(ruta_archivo, "w", encoding="utf-8") as f:
            f.write(req.contenido)
        from src.core.cloud_sync import respaldar_archivo_en_hf_async
        respaldar_archivo_en_hf_async(ruta_archivo)
        return {
            "status": "ok",
            "mensaje": f"Guía guardada correctamente en {slug_asig}",
            "ruta": str(ruta_archivo.relative_to(BASE_DIR)),
            "nombre": ruta_archivo.name,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error guardando la guía: {str(e)}")


@app.post("/api/sincronizar")
def api_sincronizar():
    """Resincroniza carpetas externas de estudio."""
    reorganizar_biblioteca_completa()
    return {"status": "ok"}


@app.post("/api/sincronizar-respaldo")
@app.post("/api/escanear-primer-ano")
def api_sincronizar_respaldo():
    """Escanea el respaldo local configurado en RESPALDO_DIR e importa temarios de DAM y Ciberseguridad."""
    from src.crawler.campus_scraper import CampusScraper
    scraper = CampusScraper()
    importados_dam = scraper.sincronizar_desde_respaldo_local()
    sincronizar_estudios_externos()
    reorganizar_biblioteca_completa()
    return {
        "status": "ok",
        "mensaje": "Respaldo local sincronizado con éxito (temarios de 1º DAM y Máster en Ciberseguridad importados al Hub)."
    }


@app.post("/api/descargar-campus")
def api_descargar_campus():
    """Conecta con Canvas UNIR y sincroniza todo: materiales locales, Canvas LMS y clases grabadas."""
    from src.crawler.campus_scraper import CampusScraper
    scraper = CampusScraper()
    nuevos = scraper.ejecutar_sincronizacion_completa()
    reorganizar_biblioteca_completa()
    return {
        "status": "ok",
        "nuevos_archivos": nuevos,
        "mensaje": f"Sincronización 360° completada con éxito. Se han incorporado {nuevos} recursos (temarios, actividades o clases)."
    }


def buscar_contexto_transcripciones(query: str, asignatura: Optional[str] = None) -> str:
    """Busca en transcripciones y fichas de clases de forma rápida para contextualizar el prompt de NOVA."""
    if not TRANSCRIPCIONES_DIR.exists():
        return ""

    asigs_a_buscar = []
    if asignatura:
        for d in TRANSCRIPCIONES_DIR.iterdir():
            if d.is_dir() and (asignatura.lower() in d.name.lower() or d.name.lower() in asignatura.lower()):
                asigs_a_buscar.append(d.name)
    if not asigs_a_buscar:
        asigs_a_buscar = [d.name for d in TRANSCRIPCIONES_DIR.iterdir() if d.is_dir()]

    terminos = [w.lower() for w in re.findall(r'\b[a-zA-ZáéíóúÁÉÍÓÚñÑ0-9]{3,}\b', query)]
    stopwords = {'que', 'por', 'para', 'con', 'del', 'las', 'los', 'una', 'uno', 'unos', 'unas', 'este', 'esta', 'como', 'sobre', 'aqui', 'hay', 'ver', 'los', 'dijo', 'dicho', 'profe', 'profesor', 'profesora', 'hola', 'buenas', 'tardes'}
    terminos_clave = [t for t in terminos if t not in stopwords] or terminos

    coincidencias = []
    for asig in asigs_a_buscar:
        dir_t = TRANSCRIPCIONES_DIR / asig
        dir_i = CLASES_GRABADAS_DIR / asig
        if not dir_t.exists():
            continue

        for f_md in dir_t.glob('*.md'):
            try:
                texto = f_md.read_text(encoding='utf-8')
            except Exception:
                continue

            info_file = dir_i / (f_md.stem + '_info.json')
            summary = ''
            session_name = f_md.stem
            if info_file.exists():
                try:
                    data = json.loads(info_file.read_text(encoding='utf-8'))
                    summary = data.get('summary') or ''
                    session_name = data.get('sessionName') or f_md.stem
                except Exception:
                    pass

            puntuacion = 0
            texto_lower = texto.lower()
            summary_lower = summary.lower()

            for t in terminos_clave:
                puntuacion += summary_lower.count(t) * 15
                puntuacion += texto_lower.count(t) * 2

            if puntuacion > 0 or not terminos_clave:
                fragmentos = []
                for t in terminos_clave:
                    pos = 0
                    while True:
                        pos = texto_lower.find(t, pos)
                        if pos == -1:
                            break
                        inicio = max(0, pos - 150)
                        fin = min(len(texto), pos + 250)
                        frag = texto[inicio:fin].replace('\n\n', ' ').strip()
                        fragmentos.append(f'...{frag}...')
                        pos += len(t) + 120
                        if len(fragmentos) >= 2:
                            break
                    if len(fragmentos) >= 2:
                        break

                coincidencias.append({
                    'asig': asig,
                    'clase': session_name,
                    'fecha': f_md.stem[:10],
                    'puntuacion': puntuacion,
                    'summary': summary,
                    'fragmentos': fragmentos[:2]
                })

    coincidencias.sort(key=lambda x: x['puntuacion'], reverse=True)
    if not coincidencias:
        return ""

    top = coincidencias[:4]
    contexto_str = "\n[ACCESO A VIDEOCLASES Y TRANSCRIPCIONES OFICIALES DISPONIBLES EN UNIR STUDY HUB]:\n"

    # Incluir índice completo de fechas disponibles para evitar alucinaciones
    for asig in asigs_a_buscar:
        dir_t = TRANSCRIPCIONES_DIR / asig
        if dir_t.exists():
            fechas_disp = sorted([f.stem[:10] for f in dir_t.glob("*.md") if f.stem[:4].isdigit()], reverse=True)
            if fechas_disp:
                contexto_str += f"📋 Clases indexadas para {asig}: {', '.join(fechas_disp)}\n"

    for c in top:
        contexto_str += f"\n📌 ASIGNATURA: {c['asig']} | SESIÓN: {c['clase']} (Fecha: {c['fecha']})\n"
        if c['summary']:
            contexto_str += f"• RESUMEN OFICIAL: {c['summary']}\n"
        if c['fragmentos']:
            contexto_str += f"• FRAGMENTOS DIRECTOS DE LA CLASE:\n"
            for f in c['fragmentos']:
                contexto_str += f"   \"{f}\"\n"
    return contexto_str


def extraer_patron_fecha_query(q: str) -> Optional[str]:
    """Extrae un patrón de fecha normalizado (ej. '-10-08' o '2026-10-08') a partir de expresiones humanas."""
    q_l = q.lower()

    # 1. Formato YYYY-MM-DD
    m_iso = re.search(r'\b(202[0-9]-[0-1][0-9]-[0-3][0-9])\b', q_l)
    if m_iso:
        return m_iso.group(1)

    # 2. Formato DD/MM o DD-MM (ej. 08/10, 8/10, 08-10, 8-10)
    m_slash = re.search(r'\b([0-3]?[0-9])[/-]([0-1]?[0-9])\b', q_l)
    if m_slash:
        d = m_slash.group(1).zfill(2)
        m = m_slash.group(2).zfill(2)
        return f"-{m}-{d}"

    # 3. Formato textual (ej. '8 de octubre', '08 de octubre', '1 de octubre')
    meses = {
        'enero': '01', 'febrero': '02', 'marzo': '03', 'abril': '04',
        'mayo': '05', 'junio': '06', 'julio': '07', 'agosto': '08',
        'septiembre': '09', 'setiembre': '09', 'octubre': '10',
        'noviembre': '11', 'diciembre': '12'
    }
    for mes_nom, mes_num in meses.items():
        m_txt = re.search(rf'\b([0-3]?[0-9])\s+(?:de\s+)?{mes_nom}\b', q_l)
        if m_txt:
            d = m_txt.group(1).zfill(2)
            return f"-{mes_num}-{d}"

    return None


def obtener_contexto_inteligente_clases(
    query: str,
    asignatura: Optional[str] = None,
    historial: Optional[List[Any]] = None
) -> str:
    """
    Detecta si el alumno pregunta por una fecha concreta (ej. 08/10), por la última clase o por un resumen.
    Inyecta la transcripción íntegra (hasta 55.000 caracteres) de la clase magistral correspondiente.
    """
    if not TRANSCRIPCIONES_DIR.exists():
        return ""

    q_lower = query.lower()
    patron_fecha = extraer_patron_fecha_query(query)

    # Si no hay fecha en la pregunta actual pero hay historial previo, rastrear si veníamos hablando de una clase concreta
    if not patron_fecha and historial:
        for item in reversed(historial[-4:]):
            txt = getattr(item, 'texto', '') if hasattr(item, 'texto') else (item.get('texto', '') if isinstance(item, dict) else '')
            pf = extraer_patron_fecha_query(txt)
            if pf:
                patron_fecha = pf
                break

    es_peticion_ultima_clase = any(kw in q_lower for kw in [
        "última clase", "ultima clase", "última sesión", "ultima sesion", "ayer",
        "último día", "ultimo dia", "última lección", "ultima leccion", "última videoclase", "ultima videoclase"
    ])
    es_peticion_clase = any(kw in q_lower for kw in [
        "resumen de la clase", "resumen de clase", "que explicó", "que explico",
        "que vimos en clase", "que se dio en clase", "que hizo el profesor",
        "que dijo el profesor", "resumen de la sesión", "resumen de la sesion", "resumir la clase",
        "en la sesión", "en la sesion", "en la clase", "de la clase", "clase del", "clase de",
        "se vio", "se hizo", "que codigo", "que código", "hicimos", "puso", "ejemplo de clase"
    ]) or bool(patron_fecha)

    # Si no es pregunta orientada a una clase ni hay fecha, usamos la búsqueda tradicional de fragmentos
    if not (es_peticion_ultima_clase or es_peticion_clase or patron_fecha):
        return buscar_contexto_transcripciones(query, asignatura)

    # Determinar asignaturas candidatas
    asigs_candidatas = []
    if asignatura:
        for d in TRANSCRIPCIONES_DIR.iterdir():
            if d.is_dir() and (asignatura.lower() in d.name.lower() or d.name.lower() in asignatura.lower()):
                asigs_candidatas.append(d.name)

    if not asigs_candidatas:
        # Inferir asignatura si el alumno la nombró en el mensaje
        for d in TRANSCRIPCIONES_DIR.iterdir():
            if d.is_dir():
                nom_limpio = d.name.lower().replace("1º dam - ", "").replace("2º dam - ", "")
                palabras_nom = [p for p in nom_limpio.split() if len(p) > 3]
                if nom_limpio in q_lower or any(p in q_lower for p in palabras_nom):
                    asigs_candidatas.append(d.name)

    if not asigs_candidatas:
        asigs_candidatas = [d.name for d in TRANSCRIPCIONES_DIR.iterdir() if d.is_dir()]

    mejor_clase = None
    mejor_fecha = ""
    mejor_asig = ""

    # 1. Si el usuario especificó una fecha (ej. 08/10 -> '-10-08'), buscar coincidencia EXACTA
    if patron_fecha:
        for asig_nom in asigs_candidatas:
            dir_t = TRANSCRIPCIONES_DIR / asig_nom
            if not dir_t.exists():
                continue
            for f_md in dir_t.glob("*.md"):
                if patron_fecha in f_md.stem:
                    mejor_clase = f_md
                    mejor_asig = asig_nom
                    mejor_fecha = f_md.stem[:10]
                    break
            if mejor_clase:
                break

    # 2. Si no hubo coincidencia por fecha, buscar la más reciente
    if not mejor_clase:
        for asig_nom in asigs_candidatas:
            dir_t = TRANSCRIPCIONES_DIR / asig_nom
            if not dir_t.exists():
                continue
            for f_md in dir_t.glob("*.md"):
                fecha_str = f_md.stem[:10] if len(f_md.stem) >= 10 and f_md.stem[:4].isdigit() else "2000-01-01"
                if fecha_str > mejor_fecha:
                    mejor_fecha = fecha_str
                    mejor_clase = f_md
                    mejor_asig = asig_nom

    if mejor_clase and mejor_clase.exists():
        try:
            texto_trans = mejor_clase.read_text(encoding="utf-8")
            stem = mejor_clase.stem
            dir_i = CLASES_GRABADAS_DIR / mejor_asig
            info_file = dir_i / f"{stem}_info.json"
            session_name = stem
            summary = ""
            rec_url = ""
            if info_file.exists():
                try:
                    idata = json.loads(info_file.read_text(encoding="utf-8"))
                    session_name = idata.get("sessionName") or idata.get("name") or stem
                    summary = idata.get("summary") or ""
                    rec_url = idata.get("recordingUrl") or ""
                except Exception:
                    pass

            from src.core.config import RESUMENES_CLASES_DIR
            resumen_guardado = ""
            cache_res = RESUMENES_CLASES_DIR / mejor_asig / f"{stem}_resumen.md"
            if cache_res.exists():
                try:
                    resumen_guardado = cache_res.read_text(encoding="utf-8")
                except Exception:
                    pass

            bloque = [
                f"\n================================================================================",
                f"🎓 [DOCUMENTO COMPLETO: TRANSCRIPCIÓN OFICIAL LITERAL DE LA CLASE MAGISTRAL]",
                f"📌 Asignatura: {mejor_asig} | Sesión: \"{session_name}\" | Fecha: {mejor_fecha}",
            ]
            if rec_url:
                bloque.append(f"🔗 Enlace oficial a grabación: {rec_url}")
            if summary:
                bloque.append(f"📝 Síntesis oficial del campus: {summary}")
            if resumen_guardado:
                bloque.append(f"\n💡 [INFORME SINTÉTICO PREVIO DE ESTA CLASE]:\n{resumen_guardado[:3500]}\n")

            bloque.append(f"\n--- TRANSCRIPCIÓN LITERAL ÍNTEGRA DE LA SESIÓN (HASTA 55.000 CARACTERES) ---")
            bloque.append(texto_trans[:55000])
            bloque.append(f"--- FIN DE LA TRANSCRIPCIÓN LITERAL ---\n")
            bloque.append(
                "🎯 DIRECTRICES PEDAGÓGICAS DE MÁXIMA PRIORIDAD PARA NOVA:\n"
                "El alumno te ha preguntado qué se hizo en esta clase o por un resumen de la misma.\n"
                "NO DIGAS JAMÁS QUE LA SESIÓN NO ESTÁ DISPONIBLE, ya que la tienes completamente transcrita arriba.\n"
                "Detalla con total precisión y estructura:\n"
                "1. 📢 AVISOS DE EXAMEN Y FECHAS: ¿Qué dijo el profesor que entraría en examen? ¿Qué PACs o advertencias dio?\n"
                "2. 🧠 TEMAS Y CONCEPTOS: Explicación paso a paso de la materia tratada en la clase.\n"
                "3. 💻 CÓDIGO Y PRÁCTICA: ¿Qué código programó el profesor en directo? Nombres de clases, métodos, archivos y buenas prácticas explicadas.\n"
                "4. ⏱️ Citas literales y marcas de tiempo reales [MM:SS] de los momentos clave."
            )
            return "\n".join(bloque)
        except Exception as e:
            console.print(f"[yellow]Error cargando transcripción completa de clase:[/yellow] {e}")

    return buscar_contexto_transcripciones(query, asignatura)


@app.get("/api/videoclases")
def api_videoclases(programa: Optional[str] = None):
    """Devuelve el catálogo de todas las clases grabadas con sus metadatos y resúmenes oficiales."""
    if not TRANSCRIPCIONES_DIR.exists():
        return []

    clases = []
    for dir_asig in sorted(TRANSCRIPCIONES_DIR.iterdir()):
        if not dir_asig.is_dir():
            continue
        asig = dir_asig.name
        dir_i = CLASES_GRABADAS_DIR / asig

        for f_md in sorted(dir_asig.glob("*.md"), reverse=True):
            info_file = dir_i / (f_md.stem + "_info.json")
            summary = ""
            titulo = f_md.stem[11:] if len(f_md.stem) >= 11 and f_md.stem[:4].isdigit() else f_md.stem
            fecha = f_md.stem[:10] if len(f_md.stem) >= 10 and f_md.stem[:4].isdigit() else "Reciente"
            rec_url = ""
            duration = 0

            if info_file.exists():
                try:
                    data = json.loads(info_file.read_text(encoding="utf-8"))
                    summary = data.get("summary") or ""
                    if data.get("sessionName"):
                        titulo = data.get("sessionName")
                    rec_url = data.get("recordingUrl") or ""
                    duration = data.get("duration") or 0
                except Exception:
                    pass

            cues_file = dir_asig / f"{f_md.stem}_cues.json"
            from src.core.config import RESUMENES_CLASES_DIR
            resumen_file = RESUMENES_CLASES_DIR / asig / f"{f_md.stem}_resumen.md"

            clases.append({
                "asignatura": asig,
                "fecha": fecha,
                "titulo": titulo,
                "summary": summary,
                "archivo": str(f_md.relative_to(BASE_DIR)),
                "nombre_archivo": f_md.name,
                "recording_url": rec_url,
                "has_cues": cues_file.exists(),
                "has_resumen": resumen_file.exists(),
                "duration": duration,
            })
    return clases


@app.get("/api/buscar-en-clases")
def api_buscar_en_clases(q: str, asig: Optional[str] = None):
    """Búsqueda rápida en intervenciones de clases con minutaje exacto."""
    from src.ai.transcription_search import buscar_en_transcripciones
    return buscar_en_transcripciones(q, asignatura=asig, max_results=8)


@app.get("/api/clase-cues")
def api_clase_cues(asig: str, nombre_archivo: str):
    """Devuelve los cues estructurados de una clase para el visor interactivo."""
    asig_limpia = nombre_seguro(asig, "asignatura")
    stem = nombre_seguro(Path(nombre_archivo).stem.replace("_cues", ""), "nombre_archivo")
    f_cues = ruta_dentro_de(TRANSCRIPCIONES_DIR, asig_limpia, f"{stem}_cues.json")
    if f_cues.exists():
        try:
            return json.loads(f_cues.read_text(encoding="utf-8"))
        except Exception:
            pass
    f_md = ruta_dentro_de(TRANSCRIPCIONES_DIR, asig_limpia, f"{stem}.md")
    if f_md.exists():
        return [{"start_str": "00:00", "speaker": "Clase", "text": f_md.read_text(encoding="utf-8")[:4000]}]
    return []


class ResumenClaseRequest(BaseModel):
    asignatura: str
    nombre_archivo: str
    forzar: Optional[bool] = False


@app.post("/api/resumen-clase")
def api_resumen_clase(req: ResumenClaseRequest):
    """Genera o recupera de caché un informe exhaustivo de una videoclase oficial."""
    from src.core.config import RESUMENES_CLASES_DIR
    asig_limpia = nombre_seguro(req.asignatura, "asignatura")
    stem = nombre_seguro(
        Path(req.nombre_archivo).stem.replace("_cues", "").replace("_info", "").replace("_resumen", ""),
        "nombre_archivo",
    )

    asig_dir_cache = ruta_dentro_de(RESUMENES_CLASES_DIR, asig_limpia)
    asig_dir_cache.mkdir(parents=True, exist_ok=True)
    cache_file = ruta_dentro_de(asig_dir_cache, f"{stem}_resumen.md")

    if cache_file.exists() and not req.forzar:
        try:
            return {
                "resumen": cache_file.read_text(encoding="utf-8"),
                "cached": True,
                "archivo": str(cache_file.relative_to(BASE_DIR))
            }
        except Exception:
            pass

    f_md = ruta_dentro_de(TRANSCRIPCIONES_DIR, asig_limpia, f"{stem}.md")
    if not f_md.exists():
        encontrados = [
            p for p in TRANSCRIPCIONES_DIR.glob(f"*/{stem}.md")
            if p.resolve().is_relative_to(TRANSCRIPCIONES_DIR.resolve())
        ]
        if encontrados:
            f_md = encontrados[0]
        else:
            raise HTTPException(status_code=404, detail="Transcripción de la clase no encontrada.")

    texto_transcripcion = f_md.read_text(encoding="utf-8")

    info_file = CLASES_GRABADAS_DIR / req.asignatura / f"{stem}_info.json"
    nombre_clase = stem
    if info_file.exists():
        try:
            idata = json.loads(info_file.read_text(encoding="utf-8"))
            nombre_clase = idata.get("sessionName") or idata.get("name") or stem
        except Exception:
            pass

    resumen = tutor_ia.generar_resumen_clase_magistral(
        texto_transcripcion=texto_transcripcion,
        asignatura=req.asignatura,
        nombre_clase=nombre_clase
    )

    if not resumen:
        raise HTTPException(status_code=500, detail="No se pudo generar el resumen con la IA.")

    cache_file.write_text(resumen, encoding="utf-8")

    return {
        "resumen": resumen,
        "cached": False,
        "archivo": str(cache_file.relative_to(BASE_DIR))
    }


@app.get("/api/agenda")
def api_agenda():
    """Devuelve las próximas entregas, PACs y eventos académicos de Canvas."""
    from src.crawler.agenda_sync import cargar_agenda_cache
    return cargar_agenda_cache()


@app.post("/api/sincronizar-agenda")
def api_sincronizar_agenda():
    """Sincroniza en directo la agenda y PACs con Canvas LMS."""
    from src.crawler.agenda_sync import sincronizar_agenda_canvas
    eventos = sincronizar_agenda_canvas()
    return {"status": "ok", "total": len(eventos), "eventos": eventos}


@app.get("/api/flashcards")
def api_flashcards(programa: str = "dam", asig: Optional[str] = None):
    """Devuelve las barajas de flashcards para memorización y repaso activo."""
    from src.ai.flashcards import GestorFlashcards
    gestor = GestorFlashcards()
    return gestor.obtener_tarjetas(programa, asignatura=asig)


class CalificarCardReq(BaseModel):
    programa: str
    tarjeta_id: str
    dificultad: str


@app.post("/api/flashcards/calificar")
def api_calificar_flashcard(req: CalificarCardReq):
    from src.ai.flashcards import GestorFlashcards
    gestor = GestorFlashcards()
    return gestor.calificar_tarjeta(req.programa, req.tarjeta_id, req.dificultad)


class GenerarCardsReq(BaseModel):
    asignatura: str
    programa: str
    texto: str
    cantidad: Optional[int] = 4


@app.post("/api/flashcards/generar")
def api_generar_flashcards(req: GenerarCardsReq):
    from src.ai.flashcards import GestorFlashcards
    gestor = GestorFlashcards()
    nuevas = gestor.generar_flashcards_con_ia(
        texto_tema=req.texto,
        asignatura=req.asignatura,
        programa_id=req.programa,
        cantidad=req.cantidad or 4
    )
    return {"status": "ok", "creadas": len(nuevas), "tarjetas": nuevas}


class EvaluarTestReq(BaseModel):
    preguntas: List[Dict[str, Any]]
    respuestas: Dict[str, str]


@app.post("/api/evaluar-test-unir")
def api_evaluar_test_unir(req: EvaluarTestReq):
    """
    Evalúa el test con el baremo oficial de examen de UNIR:
    +1.00 por acierto, -0.33 por fallo, 0 en blanco.
    Calcula nota final de 0 a 10.
    """
    total = len(req.preguntas)
    if total == 0:
        return {"nota": 0.0, "aciertos": 0, "fallos": 0, "blancos": 0, "puntos_netos": 0.0, "aprobado": False}

    aciertos = 0
    fallos = 0
    blancos = 0

    for idx, p in enumerate(req.preguntas):
        resp_user = req.respuestas.get(str(idx))
        correcta = p.get("correcta")
        if not resp_user:
            blancos += 1
        elif resp_user == correcta:
            aciertos += 1
        else:
            fallos += 1

    puntos_netos = (aciertos * 1.0) - (fallos * 0.33)
    puntos_netos = max(0.0, puntos_netos)
    nota_sobre_10 = min(10.0, (puntos_netos / total) * 10.0)

    return {
        "nota": round(nota_sobre_10, 2),
        "aciertos": aciertos,
        "fallos": fallos,
        "blancos": blancos,
        "puntos_netos": round(puntos_netos, 2),
        "aprobado": nota_sobre_10 >= 5.0,
        "baremo": "+1.0 acierto, -0.33 fallo, 0 en blanco"
    }


class ChatHistorialItem(BaseModel):
    rol: str
    texto: str


class ChatMessage(BaseModel):
    mensaje: str
    asignatura: Optional[str] = None
    programa: Optional[str] = "dam"
    historial: Optional[List[ChatHistorialItem]] = None
    modelo: Optional[str] = "auto"


class ConfigurarProveedoresReq(BaseModel):
    openrouter_key: Optional[str] = None
    groq_key: Optional[str] = None
    gemini_key: Optional[str] = None


@app.get("/api/modelos-ia")
def api_modelos_ia():
    """Devuelve el catálogo de modelos gratuitos (Google AI Studio, Gemma 4, OpenRouter Free y Groq Free)."""
    return tutor_ia.client.obtener_estado_modelos()


@app.post("/api/proveedores-ia")
def api_configurar_proveedores_ia(req: ConfigurarProveedoresReq):
    """Guarda claves gratuitas adicionales (OpenRouter / Groq / Gemini) en .env."""
    from src.core.config import save_openrouter_api_key, save_groq_api_key, save_gemini_api_key
    if req.openrouter_key is not None and req.openrouter_key.strip():
        save_openrouter_api_key(req.openrouter_key.strip())
    if req.groq_key is not None and req.groq_key.strip():
        save_groq_api_key(req.groq_key.strip())
    if req.gemini_key is not None and req.gemini_key.strip():
        save_gemini_api_key(req.gemini_key.strip())
        tutor_ia.client._client = None
    return tutor_ia.client.obtener_estado_modelos()


def _preparar_contexto_chat(msg: ChatMessage):
    """Prepara las instrucciones de sistema especializadas y el historial con inyección RAG local."""
    from src.ai.transcription_search import formatear_contexto_para_tutor
    from src.ai.materials_search import buscar_en_materiales

    contexto_rol = ""
    if msg.programa == "ciber":
        contexto_rol = (
            "ROL: Eres NOVA, mentora y consultora senior en Ciberseguridad, Red Team, SOC, SIEM y Forense. "
            "Responde con rigor técnico citando estándares oficiales (OWASP, MITRE ATT&CK, NIST, RFC, ISO 27001). "
        )
    elif msg.programa in ["pfu", "grado"]:
        contexto_rol = (
            "ROL: Eres NOVA, profesora de Grado en Ingeniería del Software y PFU en UNIR. "
            "Enfócate en metodología de software, estructuras de datos formales, complejidad O(n) y arquitectura limpia. "
        )
    elif msg.programa == "bootcamp":
        contexto_rol = (
            "ROL: Eres NOVA, consultora y mentora en Metodologías Ágiles (Scrum, Kanban), Gestión de Proyectos Digitales y Liderazgo Ágil. "
        )
    elif msg.programa == "python":
        contexto_rol = (
            "ROL: Eres NOVA, mentora de programación experta en Python (ESIT UNIR). "
            "Explicas con ejemplos de código limpios, PEP8, buenas prácticas y casos de uso prácticos. "
        )
    elif msg.programa == "aws":
        contexto_rol = (
            "ROL: Eres NOVA, instructora certificada en Amazon Web Services (AWS Cloud Practitioner CLF-C02). "
            "Explicas conceptos de nube, arquitectura, IAM, S3, EC2 y modelos de precios oficiales de AWS. "
        )
    elif msg.programa == "azure":
        contexto_rol = (
            "ROL: Eres NOVA, instructora certificada en Microsoft Azure (AZ-900 Azure Fundamentals). "
            "Explicas conceptos de cloud computing, SLA, seguridad, gobernanza y servicios core de Azure. "
        )
    else:
        contexto_rol = (
            "ROL: Eres NOVA, mentora de élite y tutora senior de FP DAM (Desarrollo de Aplicaciones Multiplataforma) en UNIR. "
            "Dominas Java, Kotlin, Android, SQL, servicios REST, Spring Boot, JDBC, XML/DOM y patrones de arquitectura limpia. "
        )

    # Inyección RAG con minutaje exacto de videoclases, transcripciones completas y temarios/actividades
    contexto_citas = formatear_contexto_para_tutor(msg.mensaje, msg.asignatura)
    contexto_trans_general = obtener_contexto_inteligente_clases(msg.mensaje, msg.asignatura, historial=msg.historial)
    contexto_materiales = buscar_en_materiales(msg.mensaje, msg.asignatura)
    contexto_asig = f"\n[Asignatura / Módulo activo: {msg.asignatura}]" if msg.asignatura else ""

    instrucciones_base = (
        "\nINSTRUCCIONES CLAVE DE MÁXIMA ROBUSTEZ PARA NOVA:\n"
        "1. Eres NOVA, la mentora de élite y copiloto académica personal del alumno Alex en UNIR Study Hub.\n"
        "2. ROBUSTEZ Y RIGOR TÉCNICO: Desarrolla respuestas profundas, completas y fundamentadas en ingeniería de software y arquitectura. Explica el porqué de cada concepto bajo el capó (JVM, memoria, hilos, SO, red, base de datos). Prohibidas las respuestas telegráficas o superficiales.\n"
        "3. CÓDIGO DE PRODUCCIÓN: Cuando aportes ejemplos de código, proporciona implementaciones completas, tipadas, compilables y modernas (Java 17/21, Kotlin actual, SQL estándar), con manejo riguroso de excepciones y comentarios técnicos en puntos críticos.\n"
        "4. TRAMPAS DE EXAMEN UNIR: Advierte proactivamente sobre preguntas capciosas de examen tipo test, errores conceptuales habituales, casos límite y excepciones típicas del temario.\n"
        "5. ACCESO LOCAL COMPLETO: Tienes ACCESO TOTAL Y LOCAL a la biblioteca oficial de clases grabadas transcritas con minutaje exacto y a todos los temarios y actividades. NUNCA digas que no tienes acceso al campus o a los materiales.\n"
        "6. CITAS EXACTAS: Si el alumno te pregunta qué dijo un profesor o sobre lo tratado en una clase, menciona siempre el minuto exacto [MM:SS] o [HH:MM:SS], el profesor que habló y citas textuales relevantes.\n"
        "7. MEMORIA CONVERSACIONAL: Mantén siempre en cuenta los turnos anteriores para dar continuidad fluida a dudas complejas o refactorizaciones de código.\n"
        "8. FORMATO: Emplea Markdown enriquecido con títulos jerárquicos, negritas para conceptos clave, listas didácticas y tablas comparativas para conceptos confusos.\n"
    )

    system_instruction_completo = f"{contexto_rol}\n{instrucciones_base}\n{contexto_asig}".strip()

    bloques_contexto = []
    if contexto_citas:
        bloques_contexto.append(contexto_citas)
    if contexto_trans_general:
        bloques_contexto.append(contexto_trans_general)
    if contexto_materiales:
        bloques_contexto.append(contexto_materiales)

    contexto_inyectado = "\n\n".join(bloques_contexto)
    if contexto_inyectado:
        mensaje_actual_con_contexto = f"{contexto_inyectado}\n\nPregunta / Mensaje del alumno: {msg.mensaje}"
    else:
        mensaje_actual_con_contexto = msg.mensaje

    mensajes_chat = []
    if msg.historial:
        for item in msg.historial[-8:]:
            rol = "user" if item.rol in ["user", "alumno", "usuario"] else "model"
            if item.texto and item.texto.strip():
                mensajes_chat.append({"role": rol, "text": item.texto.strip()})

    mensajes_chat.append({"role": "user", "text": mensaje_actual_con_contexto})
    return system_instruction_completo, mensajes_chat, mensaje_actual_con_contexto


@app.post("/api/chat")
def api_chat(msg: ChatMessage):
    """Responde preguntas mediante NOVA con rol especializado, memoria multi-turno e inyección RAG completa."""
    system_instruction_completo, mensajes_chat, mensaje_actual_con_contexto = _preparar_contexto_chat(msg)

    respuesta = tutor_ia.client.generar_chat(
        mensajes=mensajes_chat,
        system_instruction=system_instruction_completo,
        temperatura=0.4,
        modelo_preferido=msg.modelo,
    )

    if not respuesta:
        prompt_fallback = f"{system_instruction_completo}\n\n{mensaje_actual_con_contexto}\n\nNOVA:"
        respuesta = tutor_ia.client.generar_contenido(
            prompt_fallback, temperatura=0.4, modelo_preferido=msg.modelo
        )

    mod_usado = tutor_ia.client.ultimo_modelo_usado
    return {
        "respuesta": respuesta or "No se pudo generar respuesta en este momento. Inténtalo de nuevo.",
        "modelo_usado": mod_usado,
        "modelo_nombre": tutor_ia.client.obtener_nombre_legible(mod_usado),
    }


@app.post("/api/chat-stream")
def api_chat_stream(msg: ChatMessage):
    """Emite la respuesta de NOVA en tiempo real mediante Server-Sent Events (SSE)."""
    system_instruction_completo, mensajes_chat, mensaje_actual_con_contexto = _preparar_contexto_chat(msg)

    def event_generator():
        emitido = False
        try:
            for chunk in tutor_ia.client.generar_chat_stream(
                mensajes=mensajes_chat,
                system_instruction=system_instruction_completo,
                temperatura=0.4,
                modelo_preferido=msg.modelo,
            ):
                if chunk:
                    emitido = True
                    mod_usado = tutor_ia.client.ultimo_modelo_usado
                    yield f"data: {json.dumps({'chunk': chunk, 'modelo_usado': mod_usado, 'modelo_nombre': tutor_ia.client.obtener_nombre_legible(mod_usado)}, ensure_ascii=False)}\n\n"
        except Exception:
            pass

        if not emitido:
            try:
                prompt_fallback = f"{system_instruction_completo}\n\n{mensaje_actual_con_contexto}\n\nNOVA:"
                res = tutor_ia.client.generar_contenido(
                    prompt_fallback, temperatura=0.4, modelo_preferido=msg.modelo
                )
                texto_final = res or "No se pudo generar respuesta en este momento. Inténtalo de nuevo."
            except Exception:
                texto_final = "No se pudo generar respuesta en este momento. Inténtalo de nuevo."
            mod_usado = tutor_ia.client.ultimo_modelo_usado
            yield f"data: {json.dumps({'chunk': texto_final, 'modelo_usado': mod_usado, 'modelo_nombre': tutor_ia.client.obtener_nombre_legible(mod_usado)}, ensure_ascii=False)}\n\n"

        mod_usado = tutor_ia.client.ultimo_modelo_usado
        yield f"data: {json.dumps({'done': True, 'modelo_usado': mod_usado, 'modelo_nombre': tutor_ia.client.obtener_nombre_legible(mod_usado)}, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# --- FRONTEND HTML SPA CON SIDEBAR DE SECCIONES ---

@app.get("/", response_class=HTMLResponse)
def dashboard_html():
    return """<!DOCTYPE html>
<html lang="es" class="dark">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>UNIR Study Hub — Hub Multi-Titulación</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <script defer src="https://cdn.jsdelivr.net/npm/alpinejs@3.x.x/dist/cdn.min.js"></script>
  <script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
  <script src="https://cdn.jsdelivr.net/npm/dompurify@3.1.7/dist/purify.min.js"></script>
  <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
  <style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&family=JetBrains+Mono:wght@400;500&display=swap');
    body { font-family: 'Inter', sans-serif; background-color: #0b0f19; color: #f3f4f6; }
    code, pre { font-family: 'JetBrains Mono', monospace; }
    .glass { background: rgba(17, 24, 39, 0.85); backdrop-filter: blur(14px); border: 1px solid rgba(255, 255, 255, 0.08); }
    .glass-card { background: rgba(31, 41, 55, 0.5); backdrop-filter: blur(8px); border: 1px solid rgba(255, 255, 255, 0.05); }

    /* Ajuste responsivo de texto para evitar cortes y barras horizontales */
    .markdown-content {
      word-break: break-word;
      overflow-wrap: anywhere;
      white-space: normal;
      line-height: 1.7;
    }
    .markdown-content h1 { font-size: 1.45rem; font-weight: 800; color: #38bdf8; line-height: 1.4; margin-top: 1.4rem; margin-bottom: 0.8rem; word-break: break-word; }
    .markdown-content h2 { font-size: 1.25rem; font-weight: 700; color: #818cf8; line-height: 1.4; margin-top: 1.2rem; margin-bottom: 0.6rem; word-break: break-word; }
    .markdown-content h3 { font-size: 1.1rem; font-weight: 600; color: #a5b4fc; line-height: 1.35; margin-top: 1rem; margin-bottom: 0.5rem; word-break: break-word; }
    .markdown-content p { margin-bottom: 0.9rem; line-height: 1.75; color: #e2e8f0; word-break: break-word; overflow-wrap: anywhere; }
    .markdown-content ul, .markdown-content ol { margin-bottom: 1rem; padding-left: 1.5rem; line-height: 1.65; word-break: break-word; }
    .markdown-content li { margin-bottom: 0.35rem; word-break: break-word; }
    
    /* Bloques de código y citas: auto-wrap para no forzar scroll horizontal */
    .markdown-content pre {
      background: #111827;
      padding: 1rem;
      border-radius: 0.5rem;
      margin-bottom: 1rem;
      border: 1px solid #374151;
      line-height: 1.55;
      white-space: pre-wrap !important;
      word-break: break-word !important;
      overflow-wrap: anywhere !important;
      overflow-x: hidden !important;
    }
    .markdown-content code {
      background: #1f2937;
      padding: 0.15rem 0.35rem;
      border-radius: 0.25rem;
      font-size: 0.875rem;
      color: #38bdf8;
      white-space: pre-wrap !important;
      word-break: break-word !important;
      overflow-wrap: anywhere !important;
    }
    .markdown-content blockquote {
      border-left: 3px solid #38bdf8;
      padding-left: 1rem;
      color: #94a3b8;
      margin-bottom: 1rem;
      font-style: italic;
      word-break: break-word;
      overflow-wrap: anywhere;
    }
    .markdown-content table {
      width: 100%;
      border-collapse: collapse;
      margin-bottom: 1rem;
      word-break: break-word;
      table-layout: auto;
    }
    .markdown-content th, .markdown-content td { border: 1px solid #374151; padding: 0.5rem 0.75rem; text-align: left; word-break: break-word; }
    .markdown-content th { background: #1f2937; color: #e5e7eb; }
    ::-webkit-scrollbar { width: 6px; height: 6px; }
    ::-webkit-scrollbar-track { background: #0b0f19; }
    ::-webkit-scrollbar-thumb { background: #374151; border-radius: 4px; }
    ::-webkit-scrollbar-thumb:hover { background: #4b5563; }
  </style>
</head>
<body x-data="studyHubApp()" x-init="init()" class="min-h-screen flex bg-[#0b0f19] text-gray-100 overflow-x-hidden">

  <!-- ==================== SIDEBAR PRINCIPAL DE SECCIONES ==================== -->
  <aside x-show="sidebarVisible"
         x-transition:enter="transition-all ease-out duration-300"
         x-transition:enter-start="-translate-x-full opacity-0"
         x-transition:enter-end="translate-x-0 opacity-100"
         x-transition:leave="transition-all ease-in duration-200"
         x-transition:leave-start="translate-x-0 opacity-100"
         x-transition:leave-end="-translate-x-full opacity-0"
         class="w-72 bg-[#0f172a]/95 border-r border-gray-800 flex flex-col shrink-0 min-h-screen sticky top-0 z-40 select-none shadow-xl">
    
    <!-- BRAND / LOGO -->
    <div class="p-5 border-b border-gray-800 flex items-center justify-between">
      <div class="flex items-center space-x-3">
        <div class="h-10 w-10 rounded-xl bg-gradient-to-tr from-cyan-500 to-indigo-600 flex items-center justify-center text-white shadow-lg shadow-cyan-500/20 shrink-0">
          <i class="fa-solid fa-graduation-cap text-lg"></i>
        </div>
        <div>
          <h1 class="text-base font-bold bg-clip-text text-transparent bg-gradient-to-r from-cyan-400 to-indigo-400">UNIR Study Hub</h1>
          <p class="text-[11px] text-gray-400 font-medium">Plataforma Multi-Titulación</p>
        </div>
      </div>
      <button @click="toggleSidebar()" class="p-1.5 text-gray-400 hover:text-white rounded-lg hover:bg-gray-800 transition" title="Ocultar barra lateral">
        <i class="fa-solid fa-angles-left text-sm"></i>
      </button>
    </div>

    <!-- NAVEGACIÓN DE PROGRAMAS / TITULACIONES -->
    <div class="flex-1 p-4 space-y-6 overflow-y-auto">
      
      <div>
        <p class="text-[10px] uppercase tracking-wider text-gray-400 font-bold px-3 mb-2 flex items-center justify-between">
          <span>Titulaciones y Secciones</span>
          <span class="text-[9px] bg-cyan-500/10 text-cyan-400 px-1.5 py-0.5 rounded font-mono" x-text="programas.length + ' activos'"></span>
        </p>

        <div class="space-y-1">
          <template x-for="prog in programas" :key="prog.id">
            <div>
              <!-- BOTÓN PRINCIPAL DE SECCIÓN -->
              <button @click="seleccionarPrograma(prog.id)"
                      :class="programaActivoId === prog.id ? 'bg-gray-800/90 text-white font-semibold border-gray-700 shadow-md' : 'text-gray-400 hover:text-gray-200 hover:bg-gray-800/40 border-transparent'"
                      class="w-full text-left px-3 py-2.5 rounded-xl text-xs transition border flex items-center justify-between group">
                <div class="flex items-center space-x-2.5 truncate">
                  <span class="w-7 h-7 rounded-lg flex items-center justify-center text-xs shrink-0"
                        :style="'background: ' + obtenerFondoHex(prog.color) + '; color: ' + obtenerColorHex(prog.color)">
                    <i :class="'fa-solid ' + prog.icono"></i>
                  </span>
                  <div class="truncate">
                    <p class="truncate" x-text="prog.nombre"></p>
                    <p class="text-[10px] text-gray-400 truncate" x-text="prog.badge"></p>
                  </div>
                </div>
                <span class="text-[10px] px-1.5 py-0.5 rounded-full bg-gray-800 text-gray-400 border border-gray-700" x-text="prog.total_asignaturas"></span>
              </button>

              <!-- SUB-CURSOS SI EL PROGRAMA TIENE VARIOS (EJ: DAM 1º Y 2º) -->
              <div x-show="programaActivoId === prog.id && prog.cursos.length > 1" class="ml-9 mt-1 space-y-0.5 border-l border-gray-800 pl-2">
                <button @click="filtrarCurso('todos')"
                        :class="cursoFiltro === 'todos' ? 'text-cyan-400 font-bold bg-cyan-500/10' : 'text-gray-400 hover:text-gray-300'"
                        class="w-full text-left px-2 py-1 rounded-md text-[11px] transition flex items-center justify-between">
                  <span>Todo el Ciclo</span>
                  <span class="text-[9px] text-gray-400" x-text="prog.total_asignaturas"></span>
                </button>
                <template x-for="c in prog.cursos" :key="c.id">
                  <button @click="filtrarCurso(c.id)"
                          :class="cursoFiltro === c.id ? 'text-cyan-400 font-bold bg-cyan-500/10' : 'text-gray-400 hover:text-gray-300'"
                          class="w-full text-left px-2 py-1 rounded-md text-[11px] transition flex items-center justify-between">
                    <span x-text="c.nombre"></span>
                    <span class="text-[9px] text-gray-400" x-text="c.asignaturas.length"></span>
                  </button>
                </template>
              </div>
            </div>
          </template>
        </div>
      </div>

      <!-- VISTAS Y HERRAMIENTAS GLOBALES -->
      <div>
        <p class="text-[10px] uppercase tracking-wider text-gray-400 font-bold px-3 mb-2">Herramientas</p>
        <div class="space-y-1">
          <div class="flex items-center group/item">
            <button @click="irASeccion('dashboard')"
                    :class="tab === 'dashboard' && !asigSeleccionada ? 'bg-cyan-500/10 text-cyan-400 border-cyan-500/30' : 'text-gray-400 hover:text-gray-200 hover:bg-gray-800/40 border-transparent'"
                    class="flex-1 text-left px-3 py-2 rounded-xl text-xs transition border flex items-center space-x-2.5">
              <i class="fa-solid fa-chart-pie w-4 text-center"></i>
              <span>Dashboard y Métricas</span>
            </button>
            <a href="/?tab=dashboard" target="_blank" title="Abrir en pestaña nueva del navegador" class="p-1.5 text-gray-500 hover:text-cyan-400 opacity-0 group-hover/item:opacity-100 transition rounded-lg hover:bg-gray-800 ml-1">
              <i class="fa-solid fa-arrow-up-right-from-square text-[10px]"></i>
            </a>
          </div>

          <div class="flex items-center group/item">
            <button @click="irASeccion('asignaturas')"
                    :class="tab === 'asignaturas' ? 'bg-cyan-500/10 text-cyan-400 border-cyan-500/30' : 'text-gray-400 hover:text-gray-200 hover:bg-gray-800/40 border-transparent'"
                    class="flex-1 text-left px-3 py-2 rounded-xl text-xs transition border flex items-center space-x-2.5">
              <i class="fa-solid fa-book-bookmark w-4 text-center"></i>
              <span>Temarios y Asignaturas</span>
            </button>
            <a href="/?tab=asignaturas" target="_blank" title="Abrir en pestaña nueva del navegador" class="p-1.5 text-gray-500 hover:text-cyan-400 opacity-0 group-hover/item:opacity-100 transition rounded-lg hover:bg-gray-800 ml-1">
              <i class="fa-solid fa-arrow-up-right-from-square text-[10px]"></i>
            </a>
          </div>

          <div class="flex items-center group/item">
            <button @click="irASeccion('clases')"
                    :class="tab === 'clases' ? 'bg-amber-500/10 text-amber-400 border-amber-500/30' : 'text-gray-400 hover:text-gray-200 hover:bg-gray-800/40 border-transparent'"
                    class="flex-1 text-left px-3 py-2 rounded-xl text-xs transition border flex items-center space-x-2.5">
              <i class="fa-solid fa-video w-4 text-center text-amber-400"></i>
              <span>Videoclases y Transcripciones</span>
            </button>
            <a href="/?tab=clases" target="_blank" title="Abrir en pestaña nueva del navegador" class="p-1.5 text-gray-500 hover:text-amber-400 opacity-0 group-hover/item:opacity-100 transition rounded-lg hover:bg-gray-800 ml-1">
              <i class="fa-solid fa-arrow-up-right-from-square text-[10px]"></i>
            </a>
          </div>

          <div class="flex items-center group/item">
            <button @click="irASeccion('tutor')"
                    :class="tab === 'tutor' ? 'bg-cyan-500/10 text-cyan-400 border-cyan-500/30' : 'text-gray-400 hover:text-gray-200 hover:bg-gray-800/40 border-transparent'"
                    class="flex-1 text-left px-3 py-2 rounded-xl text-xs transition border flex items-center space-x-2.5">
              <i class="fa-solid fa-robot w-4 text-center text-cyan-400"></i>
              <span>Tutor Inteligente NOVA</span>
            </button>
            <a href="/?tab=tutor" target="_blank" title="Abrir en pestaña nueva del navegador" class="p-1.5 text-gray-500 hover:text-cyan-400 opacity-0 group-hover/item:opacity-100 transition rounded-lg hover:bg-gray-800 ml-1">
              <i class="fa-solid fa-arrow-up-right-from-square text-[10px]"></i>
            </a>
          </div>

          <div class="flex items-center group/item">
            <button @click="irASeccion('agenda')"
                    :class="tab === 'agenda' ? 'bg-rose-500/10 text-rose-400 border-rose-500/30' : 'text-gray-400 hover:text-gray-200 hover:bg-gray-800/40 border-transparent'"
                    class="flex-1 text-left px-3 py-2 rounded-xl text-xs transition border flex items-center justify-between">
              <div class="flex items-center space-x-2.5">
                <i class="fa-solid fa-calendar-check w-4 text-center text-rose-400"></i>
                <span>Agenda y PACs Canvas</span>
              </div>
              <span x-show="agendaEventos.length > 0" class="text-[9px] bg-rose-500/20 text-rose-300 px-1.5 py-0.5 rounded-full font-bold" x-text="agendaEventos.length"></span>
            </button>
            <a href="/?tab=agenda" target="_blank" title="Abrir en pestaña nueva del navegador" class="p-1.5 text-gray-500 hover:text-rose-400 opacity-0 group-hover/item:opacity-100 transition rounded-lg hover:bg-gray-800 ml-1">
              <i class="fa-solid fa-arrow-up-right-from-square text-[10px]"></i>
            </a>
          </div>

          <div class="flex items-center group/item">
            <button @click="irASeccion('flashcards')"
                    :class="tab === 'flashcards' ? 'bg-purple-500/10 text-purple-400 border-purple-500/30' : 'text-gray-400 hover:text-gray-200 hover:bg-gray-800/40 border-transparent'"
                    class="flex-1 text-left px-3 py-2 rounded-xl text-xs transition border flex items-center space-x-2.5">
              <i class="fa-solid fa-clone w-4 text-center text-purple-400"></i>
              <span>Flashcards (Repaso Activo)</span>
            </button>
            <a href="/?tab=flashcards" target="_blank" title="Abrir en pestaña nueva del navegador" class="p-1.5 text-gray-500 hover:text-purple-400 opacity-0 group-hover/item:opacity-100 transition rounded-lg hover:bg-gray-800 ml-1">
              <i class="fa-solid fa-arrow-up-right-from-square text-[10px]"></i>
            </a>
          </div>

          <div class="flex items-center group/item">
            <button @click="irASeccion('progreso')"
                    :class="tab === 'progreso' ? 'bg-cyan-500/10 text-cyan-400 border-cyan-500/30' : 'text-gray-400 hover:text-gray-200 hover:bg-gray-800/40 border-transparent'"
                    class="flex-1 text-left px-3 py-2 rounded-xl text-xs transition border flex items-center space-x-2.5">
              <i class="fa-solid fa-trophy w-4 text-center text-amber-400"></i>
              <span>Expediente y Calificaciones</span>
            </button>
            <a href="/?tab=progreso" target="_blank" title="Abrir en pestaña nueva del navegador" class="p-1.5 text-gray-500 hover:text-amber-400 opacity-0 group-hover/item:opacity-100 transition rounded-lg hover:bg-gray-800 ml-1">
              <i class="fa-solid fa-arrow-up-right-from-square text-[10px]"></i>
            </a>
          </div>
        </div>
      </div>

    </div>

    <!-- FOOTER SIDEBAR -->
    <div class="p-4 border-t border-gray-800 bg-gray-900/40 flex items-center justify-between text-xs">
      <div class="flex items-center space-x-2">
        <span class="w-2 h-2 rounded-full bg-emerald-400 animate-pulse"></span>
        <span class="text-gray-400 text-[11px]">Sistema Operativo</span>
      </div>
      <button @click="sincronizarMateriales()" :disabled="sincronizando" title="Refrescar y buscar archivos" class="text-gray-400 hover:text-cyan-400 p-1.5 rounded-lg hover:bg-gray-800 transition">
        <i :class="sincronizando ? 'fa-solid fa-rotate fa-spin text-cyan-400' : 'fa-solid fa-rotate'"></i>
      </button>
    </div>
  </aside>

  <!-- ==================== CONTENIDO PRINCIPAL ==================== -->
  <div class="flex-1 flex flex-col min-h-screen max-w-full overflow-hidden">
    
    <!-- TOPBAR -->
    <header class="glass sticky top-0 z-30 px-6 py-3.5 flex items-center justify-between border-b border-gray-800/80">
      <div class="flex items-center space-x-3">
        <!-- BOTÓN TOGGLE SIDEBAR -->
        <button @click="toggleSidebar()" 
                class="p-2 text-gray-400 hover:text-cyan-400 hover:bg-gray-800/80 rounded-xl transition flex items-center space-x-1.5 text-xs font-semibold border border-transparent hover:border-gray-700"
                :title="sidebarVisible ? 'Ocultar barra lateral' : 'Mostrar barra lateral'">
          <i :class="sidebarVisible ? 'fa-solid fa-bars-staggered' : 'fa-solid fa-bars text-cyan-400 text-sm'"></i>
          <span x-show="!sidebarVisible" class="text-xs text-gray-300 ml-1 font-semibold">Menú</span>
        </button>

        <span class="text-xs font-bold px-3 py-1 rounded-full uppercase tracking-wider"
              :style="'background: ' + obtenerFondoHex(programaActual?.color) + '; color: ' + obtenerColorHex(programaActual?.color)"
              x-text="programaActual?.nombre"></span>
        <span class="text-xs text-gray-400 hidden sm:inline" x-text="cursoFiltro === 'todos' ? 'Todo el programa' : (cursoFiltro === '2dam' ? '2º Curso' : '1º Curso')"></span>
      </div>

      <!-- BUSCADOR RÁPIDO -->
      <div class="flex items-center space-x-4">
        <div class="relative w-64">
          <i class="fa-solid fa-magnifying-glass absolute left-3 top-2.5 text-xs text-gray-500"></i>
          <input type="text" x-model="busquedaTexto" placeholder="Buscar materia, tema o pdf..." class="w-full bg-gray-900/80 text-white placeholder-gray-500 text-xs pl-8 pr-3 py-2 rounded-xl border border-gray-800 focus:outline-none focus:border-cyan-500">
        </div>
        <button @click="abrirChatConAsignatura('')" class="px-3.5 py-2 bg-gradient-to-r from-cyan-500 to-indigo-600 rounded-xl text-xs font-semibold hover:shadow-lg hover:shadow-cyan-500/20 transition flex items-center space-x-2">
          <i class="fa-solid fa-robot"></i><span>Consultar NOVA</span>
        </button>
      </div>
    </header>

    <!-- ==================== BARRA DE PESTAÑAS MULTI-TAREA ==================== -->
    <nav class="bg-[#0c1222]/95 border-b border-gray-800/80 px-4 py-2 flex items-center justify-between gap-3 overflow-x-auto select-none shrink-0 sticky top-[57px] z-20 backdrop-blur-md">
      <div class="flex items-center space-x-1.5 overflow-x-auto py-0.5 max-w-full">
        <template x-for="(p, index) in pestanasAbiertas" :key="p.id">
          <div class="group flex items-center rounded-xl text-xs transition border cursor-pointer shrink-0 pl-3 pr-2 py-1.5"
               :class="pestanaActivaId === p.id 
                       ? 'bg-gray-800/95 text-white border-cyan-500/40 shadow-lg shadow-cyan-500/10 font-medium ring-1 ring-cyan-500/20' 
                       : 'bg-gray-900/60 text-gray-400 hover:text-gray-200 hover:bg-gray-800/40 border-gray-800/80'"
               @click="activarPestana(p.id)">
            <!-- ICONO -->
            <i :class="'fa-solid ' + p.icono + ' mr-2 text-[11px] ' + (pestanaActivaId === p.id ? 'text-cyan-400' : 'text-gray-500')"></i>
            
            <!-- TÍTULO -->
            <span class="max-w-[130px] sm:max-w-[180px] truncate" x-text="p.titulo"></span>
            
            <!-- BOTÓN ABRIR EN PESTAÑA NUEVA DEL NAVEGADOR -->
            <a :href="p.url" target="_blank" @click.stop="" title="Abrir en pestaña nueva del navegador" 
               class="ml-2 p-1 text-gray-500 hover:text-cyan-300 hover:bg-cyan-500/10 rounded-md transition opacity-0 group-hover:opacity-100">
              <i class="fa-solid fa-arrow-up-right-from-square text-[9px]"></i>
            </a>

            <!-- BOTÓN CERRAR PESTAÑA -->
            <button x-show="pestanasAbiertas.length > 1" @click.stop="cerrarPestana(p.id)" 
                    class="ml-1 p-1 text-gray-500 hover:text-rose-400 hover:bg-rose-500/10 rounded-md transition" 
                    title="Cerrar pestaña">
              <i class="fa-solid fa-xmark text-[10px]"></i>
            </button>
          </div>
        </template>
      </div>

      <!-- BOTONES ACCIONES RÁPIDAS DE PESTAÑAS -->
      <div class="flex items-center space-x-1.5 shrink-0 text-xs">
        <button x-show="pestanasAbiertas.length > 1" @click="cerrarOtrasPestanas()" title="Cerrar las demás pestañas" 
                class="px-2 py-1 text-[11px] text-gray-400 hover:text-gray-200 bg-gray-900/60 hover:bg-gray-800 rounded-lg border border-gray-800 transition hidden sm:inline-flex items-center space-x-1">
          <i class="fa-solid fa-compress text-[10px]"></i>
          <span>Cerrar otras</span>
        </button>
      </div>
    </nav>

    <!-- CUERPO DE LA APLICACIÓN -->
    <main class="flex-1 p-8 space-y-6 overflow-y-auto max-w-7xl w-full mx-auto">

      <!-- ==================== 1. VISTA DASHBOARD ==================== -->
      <div x-show="tab === 'dashboard' && !asigSeleccionada" x-transition class="space-y-6">
        
        <!-- HERO BANNER DEL PROGRAMA ACTIVO -->
        <div class="glass p-6 rounded-3xl relative overflow-hidden border border-gray-800">
          <div class="flex flex-wrap items-center justify-between gap-6 relative z-10">
            <div class="flex items-center space-x-5">
              <div class="w-16 h-16 rounded-2xl flex items-center justify-center text-3xl shadow-xl shrink-0"
                   :style="'background: ' + obtenerFondoHex(programaActual?.color) + '; color: ' + obtenerColorHex(programaActual?.color)">
                <i :class="'fa-solid ' + (programaActual?.icono || 'fa-graduation-cap')"></i>
              </div>
              <div>
                <div class="flex items-center space-x-3">
                  <h2 class="text-2xl font-black text-white" x-text="programaActual?.nombre"></h2>
                  <span class="text-xs px-3 py-0.5 rounded-full font-bold border"
                        :style="'background: ' + obtenerFondoHex(programaActual?.color) + '; color: ' + obtenerColorHex(programaActual?.color) + '; border-color: ' + obtenerColorHex(programaActual?.color) + '40'"
                        x-text="programaActual?.badge"></span>
                </div>
                <p class="text-xs text-gray-400 mt-1 max-w-2xl" x-text="programaActual?.descripcion"></p>
              </div>
            </div>

            <!-- ACCIONES Y CURSO SELECTOR RÁPIDO -->
            <div class="flex flex-wrap items-center gap-2">
              <div class="flex items-center space-x-2">
                <button @click="sincronizarRespaldoLocal()" :disabled="sincronizandoRespaldo"
                        class="px-3.5 py-1.5 rounded-xl text-xs font-bold bg-emerald-500/15 text-emerald-300 border border-emerald-500/30 hover:bg-emerald-500/25 transition flex items-center space-x-1.5 shadow-sm"
                        :title="'Sincronizar carpetas locales de estudio (/home/alexllorca/Respaldo_Estudios: DAM 1º/2º y Ciberseguridad M1-M7)'">
                  <i :class="sincronizandoRespaldo ? 'fa-solid fa-spinner fa-spin' : 'fa-solid fa-folder-tree'"></i>
                  <span x-text="sincronizandoRespaldo ? 'Importando...' : ('📥 Importar Respaldo ' + (programaActivoId === 'dam' ? 'DAM' : (programaActivoId === 'ciber' ? 'Ciberseguridad' : 'Local')))"></span>
                </button>
                <button @click="descargarCanvasOnline()" :disabled="descargandoCanvas"
                        class="px-3.5 py-1.5 rounded-xl text-xs font-bold bg-cyan-500/15 text-cyan-300 border border-cyan-500/30 hover:bg-cyan-500/25 transition flex items-center space-x-1.5 shadow-sm"
                        title="Sincronización 360° con Canvas UNIR: Archivos, CMS, Módulos y Clases Grabadas">
                  <i :class="descargandoCanvas ? 'fa-solid fa-spinner fa-spin' : 'fa-solid fa-cloud-arrow-down'"></i>
                  <span x-text="descargandoCanvas ? 'Sincronizando...' : '🌐 Sincronizar Canvas'"></span>
                </button>
              </div>

              <!-- CURSO SELECTOR RÁPIDO -->
              <div x-show="programaActual?.cursos && programaActual?.cursos.length > 1" class="flex items-center space-x-1 bg-gray-900/90 p-1 rounded-2xl border border-gray-800">
                <button @click="filtrarCurso('todos')"
                        :class="cursoFiltro === 'todos' ? 'bg-cyan-500/20 text-cyan-400 border-cyan-500/30' : 'text-gray-400 hover:text-gray-200'"
                        class="px-3.5 py-1.5 rounded-xl text-xs font-bold transition border border-transparent">
                  Todo el Ciclo
                </button>
                <template x-for="c in programaActual?.cursos" :key="c.id">
                  <button @click="filtrarCurso(c.id)"
                          :class="cursoFiltro === c.id ? 'bg-cyan-500/20 text-cyan-400 border-cyan-500/30' : 'text-gray-400 hover:text-gray-200'"
                          class="px-3.5 py-1.5 rounded-xl text-xs font-bold transition border border-transparent" x-text="c.nombre">
                  </button>
                </template>
              </div>
            </div>
          </div>
        </div>

        <!-- AGENDA & PACs WIDGET (CANVAS ALERT BANNER) -->
        <div x-show="agendaEventos.length > 0" class="glass-card p-5 rounded-3xl border border-rose-500/30 bg-gradient-to-r from-rose-950/20 via-gray-900/60 to-purple-950/20">
          <div class="flex flex-wrap items-center justify-between gap-4 mb-3">
            <div class="flex items-center space-x-3">
              <span class="w-9 h-9 rounded-xl bg-rose-500/20 text-rose-400 flex items-center justify-center text-sm font-bold animate-pulse">
                <i class="fa-solid fa-bell"></i>
              </span>
              <div>
                <h3 class="text-sm font-extrabold text-white flex items-center space-x-2">
                  <span>Próximas Entregas y PACs de Canvas</span>
                  <span class="text-[10px] px-2 py-0.5 rounded-full bg-rose-500/20 text-rose-300 font-mono font-bold" x-text="agendaEventos.length + ' en calendario'"></span>
                </h3>
                <p class="text-[11px] text-gray-400">Actividades evaluables, fechas límite y sesiones oficiales</p>
              </div>
            </div>
            <div class="flex items-center space-x-2">
              <button @click="sincronizarAgenda()" :disabled="sincronizandoAgenda" class="px-3 py-1.5 rounded-xl text-xs font-semibold bg-gray-800 hover:bg-gray-700 text-gray-300 transition flex items-center space-x-1.5 border border-gray-700">
                <i :class="sincronizandoAgenda ? 'fa-solid fa-spinner fa-spin' : 'fa-solid fa-arrows-rotate'"></i>
                <span x-text="sincronizandoAgenda ? 'Actualizando...' : 'Refrescar Canvas'"></span>
              </button>
              <button @click="tab = 'agenda'" class="px-3 py-1.5 rounded-xl text-xs font-bold bg-rose-500/20 text-rose-300 hover:bg-rose-500/30 transition flex items-center space-x-1">
                <span>Ver Agenda Completa</span>
                <i class="fa-solid fa-arrow-right text-[10px]"></i>
              </button>
            </div>
          </div>

          <div class="grid grid-cols-1 md:grid-cols-3 gap-3">
            <template x-for="ev in agendaEventos.slice(0, 3)" :key="ev.id">
              <div class="p-3.5 rounded-2xl bg-gray-900/80 border border-gray-800 hover:border-rose-500/40 transition flex flex-col justify-between">
                <div>
                  <div class="flex items-center justify-between text-[11px] mb-1.5">
                    <span class="font-bold uppercase tracking-wider text-[10px] text-gray-400 truncate max-w-[150px]" x-text="ev.asignatura"></span>
                    <span class="px-2 py-0.5 rounded-full text-[10px] font-bold"
                          :class="ev.urgencia === 'critica' ? 'bg-rose-500/20 text-rose-300 border border-rose-500/40' : (ev.urgencia === 'alta' ? 'bg-amber-500/20 text-amber-300 border border-amber-500/40' : 'bg-cyan-500/20 text-cyan-300 border border-cyan-500/40')"
                          x-text="ev.plazo_texto"></span>
                  </div>
                  <h4 class="text-xs font-bold text-white line-clamp-1" x-text="ev.titulo"></h4>
                  <p class="text-[11px] text-gray-400 mt-1 line-clamp-2" x-text="ev.descripcion"></p>
                </div>
                <div class="mt-3 pt-2 border-t border-gray-800 flex items-center justify-between text-[10px]">
                  <span class="text-gray-400 font-mono" x-text="ev.puntos ? (ev.puntos + ' pts') : 'Evaluación'"></span>
                  <a :href="ev.url || 'https://campusfp.unir.net'" target="_blank" class="text-cyan-400 hover:underline flex items-center space-x-1">
                    <span>Abrir en Canvas</span>
                    <i class="fa-solid fa-arrow-up-right-from-square text-[9px]"></i>
                  </a>
                </div>
              </div>
            </template>
          </div>
        </div>

        <!-- STATS HERO CARDS -->
        <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
          <div class="glass-card p-5 rounded-2xl relative overflow-hidden group hover:border-cyan-500/30 transition">
            <div class="flex items-center justify-between">
              <div>
                <p class="text-xs uppercase tracking-wider text-gray-400 font-semibold">Materias / Módulos</p>
                <h3 class="text-3xl font-extrabold text-white mt-1" x-text="catalogoFiltrado.length">0</h3>
              </div>
              <div class="w-12 h-12 rounded-xl bg-cyan-500/10 text-cyan-400 flex items-center justify-center text-xl">
                <i class="fa-solid fa-layer-group"></i>
              </div>
            </div>
            <div class="mt-4 flex items-center text-xs text-cyan-400">
              <i class="fa-solid fa-check-circle mr-1.5"></i>
              <span>Estructura oficial limpia</span>
            </div>
          </div>

          <div class="glass-card p-5 rounded-2xl relative overflow-hidden group hover:border-indigo-500/30 transition">
            <div class="flex items-center justify-between">
              <div>
                <p class="text-xs uppercase tracking-wider text-gray-400 font-semibold">Temarios Oficiales</p>
                <h3 class="text-3xl font-extrabold text-white mt-1" x-text="totalMaterialesPrograma">0</h3>
              </div>
              <div class="w-12 h-12 rounded-xl bg-indigo-500/10 text-indigo-400 flex items-center justify-center text-xl">
                <i class="fa-solid fa-file-pdf"></i>
              </div>
            </div>
            <div class="mt-4 flex items-center text-xs text-indigo-400">
              <i class="fa-solid fa-folder-open mr-1.5"></i>
              <span>PDFs indexados sin duplicar</span>
            </div>
          </div>

          <div class="glass-card p-5 rounded-2xl relative overflow-hidden group hover:border-amber-500/30 transition">
            <div class="flex items-center justify-between">
              <div>
                <p class="text-xs uppercase tracking-wider text-gray-400 font-semibold">Videoclases Oficiales</p>
                <h3 class="text-3xl font-extrabold text-white mt-1" x-text="totalClasesPrograma">0</h3>
              </div>
              <div class="w-12 h-12 rounded-xl bg-amber-500/10 text-amber-400 flex items-center justify-center text-xl">
                <i class="fa-solid fa-video"></i>
              </div>
            </div>
            <div class="mt-4 flex items-center text-xs text-amber-400">
              <i class="fa-solid fa-file-lines mr-1.5"></i>
              <span>Transcripciones completas</span>
            </div>
          </div>

          <div class="glass-card p-5 rounded-2xl relative overflow-hidden group hover:border-emerald-500/30 transition">
            <div class="flex items-center justify-between">
              <div>
                <p class="text-xs uppercase tracking-wider text-gray-400 font-semibold">Simulacros y Tests</p>
                <h3 class="text-3xl font-extrabold text-white mt-1" x-text="totalTestsPrograma">0</h3>
              </div>
              <div class="w-12 h-12 rounded-xl bg-emerald-500/10 text-emerald-400 flex items-center justify-center text-xl">
                <i class="fa-solid fa-bullseye"></i>
              </div>
            </div>
            <div class="mt-4 flex items-center text-xs text-emerald-400">
              <i class="fa-solid fa-bolt mr-1.5"></i>
              <span>Simulador web interactivo</span>
            </div>
          </div>
        </div>

        <!-- SEMÁFORO Y CATÁLOGO DE ASIGNATURAS -->
        <div class="glass rounded-3xl p-6">
          <div class="flex items-center justify-between mb-6">
            <div>
              <h3 class="text-lg font-bold text-white flex items-center space-x-2">
                <i class="fa-solid fa-grid-2 text-cyan-400"></i>
                <span>Materias y Módulos de <span x-text="programaActual?.nombre"></span></span>
              </h3>
              <p class="text-xs text-gray-400">Haz clic en cualquier tarjeta para abrir sus temarios, clases magistrales y tests</p>
            </div>
            <span class="text-xs bg-gray-900 text-gray-400 px-3 py-1.5 rounded-xl border border-gray-800" x-text="catalogoFiltrado.length + ' materias'"></span>
          </div>

          <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            <template x-for="asig in catalogoFiltrado" :key="asig.nombre">
              <div @click="abrirAsignatura(asig)" class="glass-card p-5 rounded-2xl cursor-pointer hover:border-cyan-500/40 hover:bg-gray-800/60 transition group flex flex-col justify-between relative">
                <div>
                  <div class="flex items-start justify-between">
                    <span class="text-[10px] font-bold px-2 py-0.5 rounded-full bg-gray-800 text-gray-300 border border-gray-700" x-text="asig.curso"></span>
                    <div class="flex items-center space-x-1.5">
                      <a :href="'/?asig=' + encodeURIComponent(asig.nombre)" target="_blank" @click.stop="" title="Abrir en pestaña nueva del navegador" class="p-1 text-gray-500 hover:text-cyan-400 opacity-0 group-hover:opacity-100 transition rounded-lg hover:bg-gray-800">
                        <i class="fa-solid fa-arrow-up-right-from-square text-[11px]"></i>
                      </a>
                      <span class="px-2 py-0.5 rounded-full text-[10px] font-semibold"
                            :class="asig.progreso.total_evaluados > 0 ? 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/20' : 'bg-gray-800 text-gray-400'"
                            x-text="asig.progreso.total_evaluados > 0 ? asig.progreso.estado : 'Sin evaluar'">
                      </span>
                    </div>
                  </div>
                  <h4 class="font-bold text-white text-sm mt-3 group-hover:text-cyan-400 transition" x-text="asig.nombre"></h4>
                </div>

                <div class="mt-5 pt-3 border-t border-gray-800/80 grid grid-cols-3 gap-2 text-xs text-gray-400">
                  <div class="flex items-center space-x-1.5">
                    <i class="fa-regular fa-file-pdf text-rose-400"></i>
                    <span x-text="asig.total_materiales + ' pdfs'"></span>
                  </div>
                  <div class="flex items-center space-x-1.5">
                    <i class="fa-solid fa-video text-amber-400"></i>
                    <span x-text="asig.total_clases + ' clases'"></span>
                  </div>
                  <div class="flex items-center space-x-1.5">
                    <i class="fa-solid fa-bolt text-emerald-400"></i>
                    <span x-text="asig.tests.length + ' tests'"></span>
                  </div>
                </div>
              </div>
            </template>
          </div>
        </div>

      </div>

      <!-- ==================== 2. VISTA EXPLORADOR DE TEMARIOS ==================== -->
      <div x-show="tab === 'asignaturas' || asigSeleccionada" x-transition class="space-y-6">
        
        <!-- DETALLE DE ASIGNATURA SELECCIONADA -->
        <div x-show="asigSeleccionada" class="space-y-6">
          <div class="glass p-6 rounded-3xl flex flex-wrap items-center justify-between gap-4 border border-gray-800">
            <div class="flex items-center space-x-4">
              <button @click="asigSeleccionada = null; tab = 'dashboard'" class="w-10 h-10 rounded-xl bg-gray-800 hover:bg-gray-700 text-gray-300 flex items-center justify-center transition">
                <i class="fa-solid fa-arrow-left"></i>
              </button>
              <div>
                <div class="flex items-center space-x-2">
                  <h2 class="text-2xl font-extrabold text-white" x-text="asigSeleccionada?.nombre"></h2>
                  <span class="text-xs px-2.5 py-0.5 rounded-full font-semibold bg-cyan-500/10 text-cyan-400 border border-cyan-500/20" x-text="asigSeleccionada?.curso"></span>
                </div>
                <p class="text-xs text-gray-400 mt-0.5">Explora temarios oficiales, videoclases magistrales y simulacros de examen</p>
              </div>
            </div>
            
            <div class="flex items-center space-x-3">
              <button @click="modalGenerador = true" class="px-4 py-2 bg-gradient-to-r from-emerald-500 to-teal-600 rounded-xl text-xs font-bold text-gray-900 hover:shadow-lg hover:shadow-emerald-500/20 transition flex items-center space-x-2">
                <i class="fa-solid fa-wand-magic-sparkles"></i><span>Generar Guía / Test IA</span>
              </button>
              <button @click="abrirChatConAsignatura(asigSeleccionada?.nombre)" class="px-4 py-2 bg-gradient-to-r from-cyan-500 to-indigo-600 rounded-xl text-xs font-semibold hover:shadow-lg hover:shadow-cyan-500/20 transition flex items-center space-x-2">
                <i class="fa-solid fa-robot"></i><span>Consultar con NOVA</span>
              </button>
            </div>
          </div>

          <!-- 3 COLUMNAS: VIDEOCLASES | TEMARIO/GUÍAS | TESTS -->
          <div class="grid grid-cols-1 lg:grid-cols-3 gap-6">
            
            <!-- COLUMNA 1: VIDEOCLASES Y TRANSCRIPCIONES -->
            <div class="glass p-5 rounded-3xl space-y-4">
              <div class="flex items-center justify-between border-b border-gray-800 pb-3">
                <h3 class="font-bold text-white flex items-center space-x-2 text-sm">
                  <i class="fa-solid fa-video text-amber-400"></i>
                  <span>Videoclases Oficiales</span>
                </h3>
                <span class="text-[11px] bg-amber-500/10 text-amber-400 px-2 py-0.5 rounded-full font-semibold" x-text="asigSeleccionada?.clases.length + ' clases'"></span>
              </div>

              <div class="space-y-2 max-h-[520px] overflow-y-auto pr-1">
                <template x-for="clase in asigSeleccionada?.clases" :key="clase.nombre">
                  <div class="glass-card p-3 rounded-xl hover:border-amber-500/30 transition flex items-center justify-between">
                    <div class="overflow-hidden pr-2 flex-1">
                      <p class="text-[10px] text-amber-400 font-bold" x-text="clase.fecha"></p>
                      <h5 class="text-xs font-medium text-white break-words line-clamp-2" x-text="clase.titulo"></h5>
                    </div>
                    <div class="flex items-center space-x-1 shrink-0 ml-2">
                      <button @click="verContenidoArchivo(clase.archivo)" class="px-2.5 py-1 bg-gray-800 hover:bg-amber-500/20 hover:text-amber-300 text-gray-300 rounded-lg text-xs font-semibold transition">
                        Leer
                      </button>
                      <a :href="'/?guia=' + encodeURIComponent(clase.archivo) + '&asig=' + encodeURIComponent(asigSeleccionada.nombre)" target="_blank" title="Abrir en pestaña nueva del navegador" class="p-1.5 text-gray-500 hover:text-amber-400 hover:bg-gray-800 rounded-lg transition text-xs">
                        <i class="fa-solid fa-arrow-up-right-from-square text-[10px]"></i>
                      </a>
                    </div>
                  </div>
                </template>
                <div x-show="asigSeleccionada?.clases.length === 0" class="text-center py-10 text-gray-500 text-xs">
                  <i class="fa-solid fa-video-slash text-2xl mb-2 opacity-40"></i>
                  <p>Sin videoclases sincronizadas para esta materia.</p>
                </div>
              </div>
            </div>

            <!-- COLUMNA 2: TEMARIO Y GUÍAS DE ESTUDIO -->
            <div class="glass p-5 rounded-3xl space-y-4">
              <div class="flex items-center justify-between border-b border-gray-800 pb-3">
                <h3 class="font-bold text-white flex items-center space-x-2 text-sm">
                  <i class="fa-solid fa-book-open text-cyan-400"></i>
                  <span>Guías de Estudio & PDFs</span>
                </h3>
                <span class="text-[11px] bg-cyan-500/10 text-cyan-400 px-2 py-0.5 rounded-full font-semibold" x-text="asigSeleccionada?.total_materiales + ' docs'"></span>
              </div>

              <div class="space-y-2 max-h-[520px] overflow-y-auto pr-1">
                <!-- GUÍAS RESUMIDAS IA -->
                <template x-for="guia in asigSeleccionada?.guias" :key="guia">
                  <div class="glass-card p-3 rounded-xl hover:border-cyan-500/30 transition flex items-center justify-between group">
                    <div class="overflow-hidden pr-2 flex-1">
                      <p class="text-[10px] text-cyan-400 font-bold">Guía de Alto Rendimiento</p>
                      <h5 class="text-xs font-medium text-white break-words line-clamp-2" x-text="guia.replace('_guia.md', '').replaceAll('_', ' ')"></h5>
                    </div>
                    <div class="flex items-center space-x-1 shrink-0 ml-2">
                      <button @click="verContenidoArchivo('data/procesado/guias/' + asigSeleccionada.nombre + '/' + guia)" class="px-2.5 py-1 bg-cyan-500/20 text-cyan-300 hover:bg-cyan-500/30 rounded-lg text-xs font-semibold transition shrink-0">
                        Estudiar
                      </button>
                      <a :href="'/?guia=' + encodeURIComponent('data/procesado/guias/' + asigSeleccionada.nombre + '/' + guia) + '&asig=' + encodeURIComponent(asigSeleccionada.nombre)" target="_blank" title="Abrir en pestaña nueva del navegador" class="p-1.5 text-gray-500 hover:text-cyan-400 hover:bg-gray-800 rounded-lg transition text-xs shrink-0">
                        <i class="fa-solid fa-arrow-up-right-from-square text-[10px]"></i>
                      </a>
                      <button @click="eliminarGuia(asigSeleccionada.nombre, guia)" title="Eliminar guía" class="p-1.5 text-gray-500 hover:text-rose-400 hover:bg-rose-500/10 rounded-lg transition text-xs shrink-0">
                        <i class="fa-solid fa-trash-can"></i>
                      </button>
                    </div>
                  </div>
                </template>

                <!-- PDFs OFICIALES -->
                <div class="pt-3 border-t border-gray-800">
                  <p class="text-[10px] uppercase text-gray-400 font-bold mb-2">Temarios Oficiales</p>
                  <template x-for="m in asigSeleccionada?.materiales" :key="m">
                    <div class="p-2 text-xs text-gray-300 hover:text-white flex items-center justify-between bg-gray-900/40 rounded-lg mb-1 group">
                      <div class="flex items-center space-x-2 pr-2 overflow-hidden flex-1 cursor-pointer" @click="abrirPdf('data/materiales/' + asigSeleccionada.nombre + '/' + m, m)">
                        <i class="fa-regular fa-file-pdf text-rose-400 shrink-0"></i>
                        <span class="break-words line-clamp-2 text-left group-hover:text-cyan-300 transition" x-text="m"></span>
                      </div>
                      <div class="flex items-center space-x-1 shrink-0">
                        <button @click="abrirPdf('data/materiales/' + asigSeleccionada.nombre + '/' + m, m)" title="Visualizar PDF integrado" class="px-2 py-0.5 bg-cyan-500/20 text-cyan-300 rounded text-[10px] transition font-semibold hover:bg-cyan-500/30">
                          👁️ Ver
                        </button>
                        <a :href="'/api/archivo-raw?ruta=' + encodeURIComponent('data/materiales/' + asigSeleccionada.nombre + '/' + m)" target="_blank" title="Abrir PDF en pestaña nueva del navegador" class="px-1.5 py-0.5 bg-gray-800 hover:bg-gray-700 text-gray-300 rounded text-[10px] transition font-semibold flex items-center">
                          <i class="fa-solid fa-arrow-up-right-from-square text-[9px]"></i>
                        </a>
                        <button @click="generarGuiaDesdePdf(m)" title="Generar Guía y Test IA de este tema" class="px-2 py-0.5 bg-emerald-500/20 text-emerald-400 hover:text-emerald-300 rounded text-[10px] transition hover:bg-emerald-500/30 font-semibold flex items-center space-x-1">
                          <i class="fa-solid fa-wand-magic-sparkles text-[9px]"></i>
                          <span>IA</span>
                        </button>
                      </div>
                    </div>
                  </template>
                  <div x-show="asigSeleccionada?.materiales.length === 0" class="text-center py-6 text-gray-500 text-xs">
                    <p>Sin documentos locales todavía.</p>
                  </div>
                </div>
              </div>
            </div>

            <!-- COLUMNA 3: TESTS Y EXÁMENES INTERACTIVOS -->
            <div class="glass p-5 rounded-3xl space-y-4">
              <div class="flex items-center justify-between border-b border-gray-800 pb-3">
                <h3 class="font-bold text-white flex items-center space-x-2 text-sm">
                  <i class="fa-solid fa-bullseye text-emerald-400"></i>
                  <span>Simulacros de Examen</span>
                </h3>
                <span class="text-[11px] bg-emerald-500/10 text-emerald-400 px-2 py-0.5 rounded-full font-semibold" x-text="asigSeleccionada?.tests.length + ' tests'"></span>
              </div>

              <!-- BANCO DE FALLOS DE ESTA ASIGNATURA -->
              <div x-show="fallosDeAsignatura(asigSeleccionada?.nombre).length > 0"
                   class="p-3.5 rounded-2xl bg-rose-950/30 border border-rose-500/40 flex items-center justify-between gap-3 shadow-lg">
                <div class="flex items-center space-x-2.5">
                  <div class="w-8 h-8 rounded-xl bg-rose-500/20 text-rose-400 flex items-center justify-center shrink-0">
                    <i class="fa-solid fa-fire-flame-curved text-sm"></i>
                  </div>
                  <div>
                    <h5 class="text-xs font-bold text-rose-200" x-text="fallosDeAsignatura(asigSeleccionada?.nombre).length + ' preguntas falladas pendientes'"></h5>
                    <p class="text-[10px] text-rose-300/80">Aciértalas en modo repaso para limpiarlas de tu expediente</p>
                  </div>
                </div>
                <button @click="iniciarRepasoFallos(asigSeleccionada.nombre)"
                        class="px-3 py-1.5 bg-rose-500 hover:bg-rose-400 text-white font-bold rounded-xl text-xs transition shrink-0 shadow-md shadow-rose-500/20">
                  Repasar Fallos
                </button>
              </div>

              <div class="space-y-3">
                <template x-for="test in asigSeleccionada?.tests" :key="test">
                  <div class="glass-card p-4 rounded-2xl border-l-4 border-emerald-400 hover:bg-gray-800/50 transition">
                    <div class="flex items-start justify-between gap-2">
                      <h5 class="font-bold text-white text-xs flex-1 break-words" x-text="test.replace('_test', '').replaceAll('_', ' ')"></h5>
                      <button @click="eliminarTest(asigSeleccionada.nombre, test)" title="Eliminar simulacro" class="text-gray-500 hover:text-rose-400 hover:bg-rose-500/10 p-1.5 rounded-lg transition text-xs shrink-0">
                        <i class="fa-solid fa-trash-can"></i>
                      </button>
                    </div>
                    <p class="text-[11px] text-gray-400 mt-1">Simulacro interactivo con baremo oficial UNIR</p>
                    <div class="mt-3 flex items-center space-x-2">
                      <button @click="iniciarTestWeb(asigSeleccionada.nombre, test)" class="flex-1 py-2 bg-emerald-500 hover:bg-emerald-600 text-gray-900 font-bold rounded-xl text-xs transition shadow-lg shadow-emerald-500/20">
                        Comenzar Simulacro
                      </button>
                      <a :href="'/?test=' + encodeURIComponent(test) + '&asig=' + encodeURIComponent(asigSeleccionada.nombre)" target="_blank" title="Abrir simulacro en pestaña nueva del navegador" class="p-2 bg-gray-800 hover:bg-emerald-500/20 text-gray-400 hover:text-emerald-400 rounded-xl transition border border-gray-700/80">
                        <i class="fa-solid fa-arrow-up-right-from-square text-xs"></i>
                      </a>
                    </div>
                  </div>
                </template>
                <div x-show="asigSeleccionada?.tests.length === 0" class="text-center py-10 text-gray-500 text-xs">
                  <i class="fa-solid fa-list-check text-2xl mb-2 opacity-40"></i>
                  <p>No hay tests generados aún.</p>
                  <button @click="modalGenerador = true" class="mt-3 px-3 py-1.5 bg-emerald-500/20 text-emerald-400 rounded-lg text-xs font-semibold hover:bg-emerald-500/30 transition">
                    Crear Test con IA
                  </button>
                </div>
              </div>
            </div>

          </div>
        </div>

        <!-- REJILLA GENERAL SI NO HAY ASIGNATURA SELECCIONADA -->
        <div x-show="!asigSeleccionada" class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
          <template x-for="asig in catalogoFiltrado" :key="asig.nombre">
            <div @click="abrirAsignatura(asig)" class="glass p-6 rounded-3xl cursor-pointer hover:border-cyan-500/40 hover:bg-gray-800/40 transition group relative">
              <div class="flex items-center justify-between">
                <span class="text-xs font-bold text-cyan-400 bg-cyan-500/10 px-2.5 py-1 rounded-full" x-text="asig.curso"></span>
                <div class="flex items-center space-x-1.5">
                  <a :href="'/?asig=' + encodeURIComponent(asig.nombre)" target="_blank" @click.stop="" title="Abrir en pestaña nueva del navegador" class="p-1 text-gray-500 hover:text-cyan-400 opacity-0 group-hover:opacity-100 transition rounded-lg hover:bg-gray-800">
                    <i class="fa-solid fa-arrow-up-right-from-square text-[11px]"></i>
                  </a>
                  <i class="fa-solid fa-chevron-right text-gray-500 group-hover:text-cyan-400 transition"></i>
                </div>
              </div>
              <h3 class="text-base font-bold text-white mt-3 group-hover:text-cyan-300 transition" x-text="asig.nombre"></h3>
              
              <div class="mt-5 pt-4 border-t border-gray-800 flex items-center justify-between text-xs text-gray-400">
                <span x-text="asig.total_materiales + ' temarios'"></span>
                <span x-text="asig.total_clases + ' videoclases'"></span>
                <span class="text-emerald-400 font-semibold" x-text="asig.tests.length + ' tests'"></span>
              </div>
            </div>
          </template>
        </div>
      </div>

      <!-- ==================== 3. VISTA EXPEDIENTE Y CALIFICACIONES ==================== -->
      <div x-show="tab === 'progreso'" x-transition class="space-y-6">
        <div class="glass p-6 rounded-3xl border border-gray-800">
          <div class="flex flex-wrap items-center justify-between gap-4">
            <div>
              <h2 class="text-xl font-bold text-white flex items-center space-x-2">
                <i class="fa-solid fa-trophy text-amber-400"></i>
                <span>Expediente Académico y Calificaciones</span>
              </h2>
              <p class="text-xs text-gray-400 mt-1">Registro de todos los simulacros de examen realizados y banco inteligente de errores</p>
            </div>
            <button x-show="(progreso.total_fallos_pendientes || 0) > 0"
                    @click="iniciarRepasoFallos('')"
                    class="px-4 py-2.5 bg-gradient-to-r from-rose-500 to-amber-500 hover:from-rose-400 hover:to-amber-400 text-gray-950 font-black rounded-xl text-xs transition flex items-center space-x-2 shadow-lg shadow-rose-500/20">
              <i class="fa-solid fa-fire-flame-curved"></i>
              <span x-text="'Repasar mis ' + (progreso.total_fallos_pendientes || 0) + ' Preguntas Falladas'"></span>
            </button>
          </div>

          <div class="grid grid-cols-1 md:grid-cols-4 gap-4 mt-6">
            <div class="glass-card p-5 rounded-2xl">
              <p class="text-xs text-gray-400 font-semibold">Nota Media General</p>
              <h3 class="text-3xl font-extrabold text-emerald-400 mt-1" x-text="(progreso.media_global || 0) + ' / 10'"></h3>
            </div>
            <div class="glass-card p-5 rounded-2xl">
              <p class="text-xs text-gray-400 font-semibold">Tests Completados</p>
              <h3 class="text-3xl font-extrabold text-white mt-1" x-text="progreso.tests_realizados || 0"></h3>
            </div>
            <div class="glass-card p-5 rounded-2xl">
              <p class="text-xs text-gray-400 font-semibold">Fallos por Superar</p>
              <h3 class="text-3xl font-extrabold mt-1"
                  :class="(progreso.total_fallos_pendientes || 0) > 0 ? 'text-rose-400' : 'text-emerald-400'"
                  x-text="progreso.total_fallos_pendientes || 0"></h3>
            </div>
            <div class="glass-card p-5 rounded-2xl">
              <p class="text-xs text-gray-400 font-semibold">Semáforo General</p>
              <h3 class="text-2xl font-extrabold text-cyan-400 mt-1" x-text="(progreso.media_global >= 7 ? '🟢 Dominado' : (progreso.media_global >= 5 ? '🟡 Aprobado' : '🔴 En Repaso'))"></h3>
            </div>
          </div>

          <div class="mt-8">
            <h4 class="text-sm font-bold text-white mb-4">Últimos Simulacros Realizados</h4>
            <div class="space-y-2">
              <template x-for="item in progreso.historial" :key="item.fecha + item.tema">
                <div class="glass-card p-4 rounded-xl flex items-center justify-between">
                  <div>
                    <h5 class="text-sm font-bold text-white" x-text="item.asignatura + ' — ' + item.tema"></h5>
                    <p class="text-xs text-gray-400" x-text="item.fecha + ' • ' + item.aciertos + ' de ' + (item.total_preguntas || item.total) + ' aciertos'"></p>
                  </div>
                  <span class="text-base font-black px-3 py-1 rounded-xl"
                        :class="item.nota >= 5 ? 'bg-emerald-500/20 text-emerald-400' : 'bg-rose-500/20 text-rose-400'"
                        x-text="item.nota.toFixed(2) + ' / 10'"></span>
                </div>
              </template>
              <div x-show="!progreso.historial || progreso.historial.length === 0" class="text-center py-10 text-gray-500 text-xs">
                <p>Aún no has realizado ningún simulacro de examen.</p>
              </div>
            </div>
          </div>
        </div>
      </div>

      <!-- ==================== VISTA AGENDA Y PACS CANVAS ==================== -->
      <div x-show="tab === 'agenda'" x-transition class="space-y-6">
        <div class="glass p-6 rounded-3xl border border-gray-800 flex flex-wrap items-center justify-between gap-4">
          <div class="flex items-center space-x-3">
            <div class="w-10 h-10 rounded-xl bg-rose-500/10 text-rose-400 flex items-center justify-center text-lg">
              <i class="fa-solid fa-calendar-check"></i>
            </div>
            <div>
              <h2 class="text-xl font-bold text-white">Agenda & Calendario de Entregas (Canvas LMS)</h2>
              <p class="text-xs text-gray-400">Control de plazos, PACs obligatorias, foros puntuables y sesiones en directo</p>
            </div>
          </div>
          <button @click="sincronizarAgenda()" :disabled="sincronizandoAgenda" class="px-4 py-2 bg-gradient-to-r from-rose-500 to-purple-600 text-white font-bold rounded-xl text-xs transition flex items-center space-x-2 shadow-lg shadow-rose-500/20 disabled:opacity-50">
            <i :class="sincronizandoAgenda ? 'fa-solid fa-spinner fa-spin' : 'fa-solid fa-arrows-rotate'"></i>
            <span x-text="sincronizandoAgenda ? 'Sincronizando con Canvas...' : 'Sincronizar con Canvas'"></span>
          </button>
        </div>

        <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          <template x-for="ev in agendaEventos" :key="ev.id">
            <div class="glass p-5 rounded-2xl border transition flex flex-col justify-between group"
                 :class="ev.urgencia === 'critica' ? 'border-rose-500/40 bg-rose-950/10' : (ev.urgencia === 'alta' ? 'border-amber-500/30' : 'border-gray-800')">
              <div>
                <div class="flex items-center justify-between gap-2 mb-2">
                  <span class="text-[10px] font-bold px-2.5 py-0.5 rounded-full uppercase"
                        :class="ev.tipo === 'tarea' ? 'bg-cyan-500/10 text-cyan-400 border border-cyan-500/20' : 'bg-purple-500/10 text-purple-400 border border-purple-500/20'"
                        x-text="ev.tipo === 'tarea' ? 'PAC / Tarea' : 'Clase en Directo'"></span>
                  <span class="text-[11px] font-black px-2 py-0.5 rounded-full"
                        :class="ev.urgencia === 'critica' ? 'bg-rose-500/20 text-rose-300 border border-rose-500/40' : (ev.urgencia === 'alta' ? 'bg-amber-500/20 text-amber-300' : 'bg-gray-800 text-gray-400')"
                        x-text="ev.plazo_texto"></span>
                </div>
                <p class="text-[11px] font-semibold text-gray-400 truncate" x-text="ev.asignatura"></p>
                <h4 class="font-bold text-white text-sm mt-1 group-hover:text-cyan-300 transition" x-text="ev.titulo"></h4>
                <p class="text-xs text-gray-400 mt-2 line-clamp-3 leading-relaxed" x-text="ev.descripcion || 'Sin descripción detallada disponible.'"></p>
              </div>

              <div class="mt-4 pt-3 border-t border-gray-800 flex items-center justify-between text-xs">
                <span class="text-gray-400 font-mono text-[11px]" x-text="ev.puntos ? (ev.puntos + ' puntos') : 'Sesión evaluativa'"></span>
                <a :href="ev.url || 'https://campusfp.unir.net'" target="_blank" class="px-3 py-1.5 bg-gray-800 hover:bg-gray-700 text-cyan-300 rounded-xl font-semibold transition flex items-center space-x-1.5 border border-gray-700">
                  <span>Ir a la Entrega</span>
                  <i class="fa-solid fa-arrow-up-right-from-square text-[10px]"></i>
                </a>
              </div>
            </div>
          </template>
        </div>
      </div>

      <!-- ==================== VISTA FLASHCARDS / REPASO ACTIVO ==================== -->
      <div x-show="tab === 'flashcards'" x-transition class="space-y-6">
        <div class="glass p-6 rounded-3xl border border-gray-800 flex flex-wrap items-center justify-between gap-4">
          <div class="flex items-center space-x-3">
            <div class="w-10 h-10 rounded-xl bg-purple-500/10 text-purple-400 flex items-center justify-center text-lg">
              <i class="fa-solid fa-clone"></i>
            </div>
            <div>
              <h2 class="text-xl font-bold text-white">Tarjetas de Repaso Activo (Flashcards / Anki)</h2>
              <p class="text-xs text-gray-400">Técnica de memorización activa y repetición espaciada para conceptos de examen</p>
            </div>
          </div>
          <div class="flex items-center space-x-3">
            <select x-model="flashcardFiltroAsig" @change="filtrarFlashcards()" class="bg-gray-900 text-gray-200 text-xs px-3.5 py-2.5 rounded-xl border border-gray-800 outline-none">
              <option value="">Todas las materias</option>
              <template x-for="asig in asignaturasConFlashcards" :key="asig">
                <option :value="asig" x-text="asig"></option>
              </template>
            </select>
            <button @click="abrirModalGenerarCards()" class="px-4 py-2.5 bg-gradient-to-r from-purple-500 to-indigo-600 hover:from-purple-600 hover:to-indigo-700 text-white font-bold rounded-xl text-xs transition flex items-center space-x-2 shadow-lg shadow-purple-500/20">
              <i class="fa-solid fa-wand-magic-sparkles"></i>
              <span>Crear con IA</span>
            </button>
          </div>
        </div>

        <div x-show="flashcardsFiltradas.length > 0" class="max-w-2xl mx-auto space-y-6">
          <div class="flex items-center justify-between text-xs text-gray-400">
            <span x-text="'Tarjeta ' + (flashcardIndice + 1) + ' de ' + flashcardsFiltradas.length"></span>
            <span class="text-purple-400 font-semibold" x-text="tarjetaActual?.asignatura"></span>
          </div>

          <!-- TARJETA INTERACTIVA 3D FLIP -->
          <div @click="flashcardVolteada = !flashcardVolteada"
               class="min-h-[280px] p-8 rounded-3xl glass cursor-pointer border hover:border-purple-500/40 transition flex flex-col justify-between shadow-2xl relative select-none"
               :class="flashcardVolteada ? 'border-purple-500/50 bg-purple-950/20' : 'border-gray-700 bg-gray-900/90'">
            <div>
              <div class="flex items-center justify-between text-xs text-gray-400 mb-4">
                <span class="font-bold uppercase tracking-wider text-[10px]"
                      :class="flashcardVolteada ? 'text-purple-400' : 'text-cyan-400'"
                      x-text="flashcardVolteada ? '💡 Respuesta Técnica' : '❓ Pregunta de Concepto'"></span>
                <span class="text-[10px] px-2 py-0.5 rounded-full bg-gray-800 text-gray-300" x-text="tarjetaActual?.nivel"></span>
              </div>
              <h3 class="text-lg md:text-xl font-bold text-white leading-relaxed" x-show="!flashcardVolteada" x-text="tarjetaActual?.anverso"></h3>
              <div class="markdown-content text-sm md:text-base leading-relaxed text-gray-200" x-show="flashcardVolteada" x-html="renderMarkdown(tarjetaActual?.reverso || '')"></div>
            </div>

            <div class="pt-4 border-t border-gray-800/80 flex items-center justify-between text-xs text-gray-400">
              <span class="text-[11px]"><i class="fa-solid fa-arrows-rotate mr-1"></i>Haz clic en cualquier parte para voltear</span>
              <span class="text-xs font-semibold text-purple-400" x-text="flashcardVolteada ? '✔ Reversa' : 'Ver respuesta'"></span>
            </div>
          </div>

          <!-- BOTONES DE CALIFICACIÓN ESPACIADA -->
          <div class="grid grid-cols-3 gap-3" x-show="flashcardVolteada">
            <button @click="calificarTarjeta('dificil')" class="py-3 px-4 rounded-xl bg-rose-500/20 hover:bg-rose-500/30 text-rose-300 font-bold text-xs transition border border-rose-500/30">
              🔴 Difícil (Reforzar)
            </button>
            <button @click="calificarTarjeta('medio')" class="py-3 px-4 rounded-xl bg-amber-500/20 hover:bg-amber-500/30 text-amber-300 font-bold text-xs transition border border-amber-500/30">
              🟡 Bien (En repaso)
            </button>
            <button @click="calificarTarjeta('facil')" class="py-3 px-4 rounded-xl bg-emerald-500/20 hover:bg-emerald-500/30 text-emerald-300 font-bold text-xs transition border border-emerald-500/30">
              🟢 Fácil (Dominada)
            </button>
          </div>

          <!-- BOTONES DE NAVEGACIÓN -->
          <div class="flex items-center justify-between pt-2">
            <button @click="anteriorFlashcard()" :disabled="flashcardIndice === 0" class="px-4 py-2 bg-gray-800 rounded-xl text-xs text-gray-300 font-semibold disabled:opacity-40">
              Anterior
            </button>
            <button @click="siguienteFlashcard()" class="px-5 py-2 bg-gray-800 hover:bg-gray-700 text-white rounded-xl text-xs font-semibold transition">
              Siguiente Tarjeta
            </button>
          </div>
        </div>

        <div x-show="flashcardsFiltradas.length === 0" class="text-center py-16 text-gray-500 text-xs">
          <i class="fa-solid fa-clone text-3xl mb-3 opacity-30 text-purple-400"></i>
          <p class="text-sm">No hay tarjetas de repaso para este filtro.</p>
        </div>
      </div>

      <!-- ==================== 4. VISTA TUTOR NOVA EN DIRECTO ==================== -->
      <div x-show="tab === 'tutor'" x-transition class="glass rounded-3xl flex flex-col h-[750px] overflow-hidden border border-gray-800">
        <!-- HEADER CHAT -->
        <div class="p-4 bg-gray-900/90 border-b border-gray-800 flex flex-wrap items-center justify-between gap-3">
          <div class="flex items-center space-x-3">
            <div class="w-10 h-10 rounded-xl flex items-center justify-center text-white shadow-lg"
                 :style="'background: ' + obtenerColorHex(programaActual?.color)">
              <i class="fa-solid fa-robot"></i>
            </div>
            <div>
              <div class="flex items-center space-x-2">
                <h3 class="font-bold text-white">NOVA — Copiloto Académico</h3>
                <span class="text-[10px] font-bold px-2 py-0.5 rounded-full uppercase"
                      :style="'background: ' + obtenerFondoHex(programaActual?.color) + '; color: ' + obtenerColorHex(programaActual?.color)"
                      x-text="'Modo ' + programaActual?.titulo_corto"></span>
              </div>
              <p class="text-xs text-emerald-400 flex items-center">
                <span class="w-1.5 h-1.5 rounded-full bg-emerald-400 mr-1.5 animate-pulse"></span>
                <span x-text="(chatModeloActualNombre || 'Gemini 3 Flash Preview') + ' · Memoria Conversacional Activa'"></span>
              </p>
            </div>
          </div>

          <div class="flex flex-wrap items-center gap-2">
            <span class="text-[11px] text-amber-400 bg-amber-500/10 border border-amber-500/20 px-2.5 py-1 rounded-xl flex items-center space-x-1.5"
                  x-show="clasesDeAsignaturaActual.length > 0">
              <i class="fa-solid fa-video"></i>
              <span x-text="clasesDeAsignaturaActual.length + ' videoclases vinculadas'"></span>
            </span>

            <!-- SELECTOR DE MODELOS GRATUITOS ESTILO OPENCODE -->
            <div class="flex items-center space-x-1">
              <select x-model="chatModeloSeleccionado" @change="cambiarModeloIa($event.target.value)"
                      class="bg-gray-950 text-cyan-300 text-xs font-semibold px-3 py-1.5 rounded-xl border border-cyan-500/30 outline-none hover:border-cyan-400 transition"
                      title="Seleccionar modelo de IA gratuito">
                <template x-for="m in modelosIaDisponibles" :key="m.id">
                  <option :value="m.id"
                          x-text="(m.agotado_temporalmente ? '⏳ ' : (m.configurado ? '🟢 ' : '🔑 ')) + m.nombre"></option>
                </template>
              </select>
              <button @click="configurarClavesIaGratis()"
                      class="text-xs text-cyan-400 hover:text-white px-2.5 py-1.5 rounded-xl bg-gray-800 border border-gray-700 hover:border-cyan-500/40 transition"
                      title="Añadir claves gratuitas opcionales de OpenRouter o Groq (DeepSeek V3, Qwen 3 235B, Llama 3.3 70B)">
                <i class="fa-solid fa-key text-[11px]"></i>
              </button>
            </div>

            <select x-model="chatAsignatura" @change="cambiarAsignaturaChat($event.target.value)" class="bg-gray-800 text-gray-200 text-xs px-3 py-1.5 rounded-xl border border-gray-700 outline-none">
              <option value="">General (Todo el programa)</option>
              <template x-for="asig in catalogoFiltrado" :key="asig.nombre">
                <option :value="asig.nombre" x-text="asig.nombre"></option>
              </template>
            </select>
            <button @click="limpiarChat()" class="text-xs text-gray-400 hover:text-rose-400 px-2.5 py-1.5 rounded-xl bg-gray-800 border border-gray-700 hover:border-rose-500/30 transition flex items-center space-x-1" :title="'Reiniciar conversación de ' + (chatAsignatura || 'NOVA')">
              <i class="fa-solid fa-rotate-right text-[11px]"></i>
              <span class="hidden sm:inline">Reiniciar</span>
            </button>
          </div>
        </div>

        <!-- MENSAJES -->
        <div class="flex-1 p-6 overflow-y-auto space-y-4" id="chatBox">
          <template x-for="(msg, idx) in chatHistorial" :key="idx">
            <div :class="msg.rol === 'user' ? 'flex justify-end' : 'flex justify-start'">
              <div :class="msg.rol === 'user' ? 'bg-cyan-600 text-white rounded-2xl rounded-tr-none' : 'bg-gray-800/90 text-gray-200 rounded-2xl rounded-tl-none border border-gray-700'" class="max-w-3xl lg:max-w-4xl p-4 shadow-md break-words relative group/msg">
                <div class="flex items-center justify-between mb-1 gap-2">
                  <p class="text-xs font-bold opacity-75" x-text="msg.rol === 'user' ? 'Tú' : 'NOVA'"></p>
                  <!-- Botones de acción para respuestas de NOVA -->
                  <div x-show="msg.rol !== 'user'" class="flex items-center space-x-1.5 opacity-60 group-hover/msg:opacity-100 transition text-[11px]">
                    <button @click="copiarTexto(msg.texto)" class="px-2 py-0.5 rounded-lg bg-gray-700/60 hover:bg-gray-700 text-gray-300 transition flex items-center space-x-1" title="Copiar texto">
                      <i class="fa-regular fa-copy text-[10px]"></i>
                      <span class="text-[10px]">Copiar</span>
                    </button>
                    <button x-show="msg.texto && msg.texto.length > 200" @click="guardarRespuestaComoGuia(msg.texto)" class="px-2 py-0.5 rounded-lg bg-emerald-500/20 hover:bg-emerald-500/30 text-emerald-300 transition flex items-center space-x-1" title="Guardar esta explicación como archivo de Guía Oficial">
                      <i class="fa-solid fa-floppy-disk text-[10px]"></i>
                      <span class="text-[10px]">Guardar como Guía</span>
                    </button>
                  </div>
                </div>
                <div class="markdown-content text-sm leading-relaxed" x-html="renderMarkdown(msg.texto)"></div>
              </div>
            </div>
          </template>
          <div x-show="chatCargando" class="flex justify-start">
            <div class="bg-gray-800 p-4 rounded-2xl rounded-tl-none text-gray-400 text-sm flex items-center space-x-2">
              <i class="fa-solid fa-spinner fa-spin"></i><span>NOVA está razonando la respuesta técnica...</span>
            </div>
          </div>
        </div>

        <!-- QUICK PROMPT CHIPS SOBRE LAS CLASES -->
        <div class="px-4 py-2.5 bg-gray-900/80 border-t border-gray-800 flex flex-wrap items-center gap-2 text-xs" x-show="chatAsignatura">
          <span class="text-[10px] text-amber-400 font-bold uppercase shrink-0 flex items-center space-x-1">
            <i class="fa-solid fa-bolt text-[10px]"></i>
            <span>Preguntar a NOVA:</span>
          </span>
          <button @click="lanzarPreguntaRapida('¿Qué explicó el profesor en la última videoclase de ' + chatAsignatura + '?')" class="px-2.5 py-1 rounded-lg bg-gray-800 hover:bg-gray-700 hover:text-cyan-300 text-gray-300 transition text-[11px] border border-gray-700">
            🎙️ ¿Qué explicó el profesor en la última sesión?
          </button>
          <button @click="lanzarPreguntaRapida('¿Qué indicaciones dio el docente sobre el anteproyecto y las entregas en ' + chatAsignatura + '?')" class="px-2.5 py-1 rounded-lg bg-gray-800 hover:bg-gray-700 hover:text-cyan-300 text-gray-300 transition text-[11px] border border-gray-700">
            📋 Directrices de anteproyecto y entregas
          </button>
          <button @click="lanzarPreguntaRapida('¿Cuáles son las fechas límite y criterios de evaluación comentados en clase de ' + chatAsignatura + '?')" class="px-2.5 py-1 rounded-lg bg-gray-800 hover:bg-gray-700 hover:text-cyan-300 text-gray-300 transition text-[11px] border border-gray-700">
            📅 Fechas y criterios de evaluación
          </button>
        </div>

        <!-- INPUT BAR -->
        <div class="p-4 bg-gray-900/90 border-t border-gray-800 flex items-center space-x-3">
          <input type="text" x-model="chatInput" @keydown.enter="enviarMensajeChat()"
                 :placeholder="'Haz cualquier pregunta técnica o sobre lo que dijo el docente en las videoclases...'"
                 class="flex-1 bg-gray-800 text-white placeholder-gray-500 text-xs px-4 py-3 rounded-xl border border-gray-700 focus:outline-none focus:border-cyan-500">
          <button @click="enviarMensajeChat()" :disabled="chatCargando || !chatInput.trim()"
                  class="px-5 py-3 bg-gradient-to-r from-cyan-500 to-indigo-600 text-white font-semibold rounded-xl text-xs hover:shadow-lg hover:shadow-cyan-500/20 disabled:opacity-50 transition">
            <i class="fa-solid fa-paper-plane mr-1.5"></i>Enviar
          </button>
        </div>
      </div>

      <!-- ==================== 5. VISTA VIDEOCLASES Y TRANSCRIPCIONES ==================== -->
      <div x-show="tab === 'clases'" x-transition class="space-y-6">
        
        <div class="glass p-6 rounded-3xl border border-gray-800 flex flex-wrap items-center justify-between gap-4">
          <div>
            <div class="flex items-center space-x-3">
              <div class="w-10 h-10 rounded-xl bg-amber-500/10 text-amber-400 flex items-center justify-center text-lg">
                <i class="fa-solid fa-video"></i>
              </div>
              <div>
                <h2 class="text-xl font-bold text-white">Videoclases y Transcripciones Oficiales</h2>
                <p class="text-xs text-gray-400">Catálogo oficial con transcripciones completas y resúmenes de cada lección magistral</p>
              </div>
            </div>
          </div>
          
          <div class="flex items-center space-x-3">
            <span class="text-xs bg-amber-500/10 text-amber-400 border border-amber-500/20 px-3 py-1.5 rounded-xl font-semibold"
                  x-text="videoclasesFiltradas.length + ' clases disponibles'"></span>
          </div>
        </div>

        <!-- FILTROS Y BÚSQUEDA DE CLASES -->
        <div class="glass-card p-4 rounded-2xl flex flex-wrap items-center justify-between gap-4">
          <div class="flex items-center space-x-3 flex-1 min-w-[280px]">
            <div class="relative flex-1">
              <i class="fa-solid fa-magnifying-glass absolute left-3 top-3 text-xs text-gray-500"></i>
              <input type="text" x-model="busquedaClaseTexto" placeholder="Buscar por profesor, tema, fecha o palabra dicha en clase..."
                     class="w-full bg-gray-900 text-white placeholder-gray-500 text-xs pl-8 pr-3 py-2.5 rounded-xl border border-gray-800 focus:outline-none focus:border-amber-500">
            </div>
          </div>
          
          <div class="flex items-center space-x-3">
            <select x-model="filtroClaseAsignatura" class="bg-gray-900 text-gray-200 text-xs px-3.5 py-2.5 rounded-xl border border-gray-800 outline-none focus:border-amber-500">
              <option value="">Todas las asignaturas</option>
              <template x-for="asig in asignaturasConClases" :key="asig">
                <option :value="asig" x-text="asig"></option>
              </template>
            </select>
          </div>
        </div>

        <!-- LISTA / REJILLA DE VIDEOCLASES -->
        <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
          <template x-for="clase in videoclasesFiltradas" :key="clase.archivo">
            <div class="glass p-5 rounded-2xl border border-gray-800 hover:border-amber-500/40 transition flex flex-col justify-between group">
              <div>
                <div class="flex items-center justify-between gap-2">
                  <span class="text-[10px] font-bold px-2 py-0.5 rounded-full bg-cyan-500/10 text-cyan-400 border border-cyan-500/20 break-words" x-text="clase.asignatura"></span>
                  <span class="text-[11px] font-semibold text-amber-400 flex items-center space-x-1 shrink-0">
                    <i class="fa-regular fa-calendar mr-1"></i>
                    <span x-text="clase.fecha"></span>
                  </span>
                </div>
                <h4 class="font-bold text-white text-sm mt-3 group-hover:text-amber-300 transition break-words" x-text="clase.titulo"></h4>
                <p class="text-xs text-gray-400 mt-2 line-clamp-3 leading-relaxed break-words" x-text="clase.summary || 'Transcripción oficial completa disponible con registro detallado de la intervención del profesor y dudas de los estudiantes.'"></p>
              </div>

                <div class="flex items-center space-x-1.5 flex-wrap gap-y-1">
                  <button @click="abrirMinutero(clase)" class="px-2.5 py-1.5 bg-amber-500/20 hover:bg-amber-500/30 text-amber-300 rounded-xl text-xs font-bold transition flex items-center space-x-1 border border-amber-500/30 shadow-sm" title="Ver marcas de tiempo e intervenciones">
                    <i class="fa-solid fa-clock text-[10px]"></i>
                    <span>Minutero</span>
                  </button>
                  <button @click="abrirResumenClase(clase)"
                          :class="clase.has_resumen ? 'bg-purple-500/20 text-purple-300 border-purple-500/40' : 'bg-gray-800 hover:bg-gray-700 text-gray-300 border-gray-700'"
                          class="px-2.5 py-1.5 rounded-xl text-xs font-bold transition flex items-center space-x-1 border shadow-sm"
                          title="Generar o consultar el informe pedagógico estructurado de la clase con IA">
                    <i class="fa-solid fa-wand-magic-sparkles text-[10px]" :class="clase.has_resumen ? 'text-purple-400' : 'text-gray-400'"></i>
                    <span x-text="clase.has_resumen ? 'Resumen IA ✔' : 'Resumen IA'"></span>
                  </button>
                  <button @click="verContenidoArchivo(clase.archivo)" class="px-2 py-1.5 bg-gray-800 hover:bg-gray-700 text-gray-300 rounded-xl text-xs font-semibold transition flex items-center space-x-1" title="Ver transcripción literal en texto">
                    <i class="fa-solid fa-file-lines text-xs text-gray-400"></i>
                    <span>Texto</span>
                  </button>
                </div>
                <div class="flex items-center space-x-1.5">
                  <a x-show="clase.recording_url" :href="clase.recording_url" target="_blank" class="px-2.5 py-1.5 bg-gray-800/80 hover:bg-gray-700 text-cyan-400 rounded-xl text-xs transition flex items-center space-x-1" title="Ver en servidor UNIR">
                    <i class="fa-solid fa-play text-[10px]"></i>
                    <span>Vídeo</span>
                  </a>
                  <button @click="preguntarSobreClase(clase)" class="px-2.5 py-1.5 bg-cyan-500/20 hover:bg-cyan-500/30 text-cyan-300 border border-cyan-500/30 rounded-xl text-xs font-semibold transition flex items-center space-x-1" title="Preguntar dudas a NOVA en el chat">
                    <i class="fa-solid fa-robot"></i>
                    <span>NOVA</span>
                  </button>
                </div>
            </div>
          </template>
        </div>

        <div x-show="videoclasesFiltradas.length === 0" class="text-center py-16 text-gray-500 text-xs">
          <i class="fa-solid fa-video-slash text-3xl mb-3 opacity-30 text-amber-400"></i>
          <p class="text-sm">No se encontraron videoclases que coincidan con la búsqueda.</p>
        </div>

      </div>

    </main>

  </div>

  <!-- ==================== MODAL: GENERADOR DE GUÍA IA ==================== -->
  <div x-show="modalGenerador" x-transition class="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/80 backdrop-blur-sm">
    <div class="glass w-full max-w-xl rounded-3xl p-6 border border-emerald-500/30 shadow-2xl space-y-4">
      <div class="flex items-center justify-between border-b border-gray-800 pb-3">
        <h4 class="font-bold text-white text-base flex items-center space-x-2">
          <i class="fa-solid fa-wand-magic-sparkles text-emerald-400"></i>
          <span>Generar Guía y Test con IA</span>
        </h4>
        <button @click="modalGenerador = false" class="text-gray-400 hover:text-white p-1 rounded-lg">
          <i class="fa-solid fa-xmark text-lg"></i>
        </button>
      </div>

      <p class="text-xs text-gray-400">NOVA sintetiza el temario oficial en PDF y las lecciones del profesor para crear apuntes de alto nivel y simulacros tipo test con justificación detallada.</p>

      <div>
        <label class="block text-xs font-bold text-gray-300 mb-1">Materia Seleccionada:</label>
        <p class="text-sm font-semibold text-white bg-gray-900 p-2.5 rounded-xl border border-gray-800" x-text="asigSeleccionada?.nombre"></p>
      </div>

      <div>
        <label class="block text-xs font-bold text-gray-300 mb-1">Documento / Temario Oficial (PDF):</label>
        <select x-model="generadorPdfSeleccionado"
                @change="if (!generadorTemaNombre && generadorPdfSeleccionado) generadorTemaNombre = generadorPdfSeleccionado.replace('.pdf', '').replaceAll('_', ' ')"
                class="w-full bg-gray-900 text-white text-xs p-3 rounded-xl border border-gray-700 focus:outline-none focus:border-emerald-500">
          <option value="">🔍 Detección automática según el nombre del tema</option>
          <template x-for="m in asigSeleccionada?.materiales" :key="m">
            <option :value="m" x-text="m"></option>
          </template>
        </select>
      </div>

      <div>
        <label class="block text-xs font-bold text-gray-300 mb-1">Nombre o Título del Tema:</label>
        <input type="text" x-model="generadorTemaNombre" placeholder="Ej: Tema 1 - Fundamentos y Arquitectura" class="w-full bg-gray-900 text-white text-xs p-3 rounded-xl border border-gray-700 focus:outline-none focus:border-emerald-500">
      </div>

      <!-- Selector de Profundidad de la Guía -->
      <div>
        <label class="block text-xs font-bold text-gray-300 mb-2">Profundidad y Rigor de la Guía:</label>
        <div class="grid grid-cols-1 sm:grid-cols-3 gap-2.5">
          <!-- Opción Corta -->
          <div @click="generadorProfundidad = 'corta'; generadorNumPreguntas = 5"
               :class="generadorProfundidad === 'corta' ? 'border-emerald-500 bg-emerald-500/10 ring-1 ring-emerald-500' : 'border-gray-800 bg-gray-900/60 hover:border-gray-700'"
               class="p-3 rounded-2xl border cursor-pointer transition flex flex-col justify-between space-y-1">
            <div class="flex items-center justify-between">
              <span class="text-xs font-bold text-emerald-400 flex items-center space-x-1.5">
                <i class="fa-solid fa-bolt"></i>
                <span>Corta</span>
              </span>
              <span class="text-[10px] font-mono px-1.5 py-0.5 rounded bg-emerald-500/20 text-emerald-300">Exprés</span>
            </div>
            <p class="text-[11px] text-gray-300 font-medium">Repaso Rápido</p>
            <p class="text-[10px] text-gray-400 leading-tight">~1.200 palabras, chuleta de sintaxis y test de 5 preguntas.</p>
          </div>

          <!-- Opción Media -->
          <div @click="generadorProfundidad = 'media'; generadorNumPreguntas = 10"
               :class="generadorProfundidad === 'media' ? 'border-blue-500 bg-blue-500/10 ring-1 ring-blue-500' : 'border-gray-800 bg-gray-900/60 hover:border-gray-700'"
               class="p-3 rounded-2xl border cursor-pointer transition flex flex-col justify-between space-y-1">
            <div class="flex items-center justify-between">
              <span class="text-xs font-bold text-blue-400 flex items-center space-x-1.5">
                <i class="fa-solid fa-book-open"></i>
                <span>Media</span>
              </span>
              <span class="text-[10px] font-mono px-1.5 py-0.5 rounded bg-blue-500/20 text-blue-300">Estándar</span>
            </div>
            <p class="text-[11px] text-gray-300 font-medium">Equilibrada</p>
            <p class="text-[10px] text-gray-400 leading-tight">~3.000 palabras, temario completo y test de 10 preguntas.</p>
          </div>

          <!-- Opción Robusta -->
          <div @click="generadorProfundidad = 'robusta'; generadorNumPreguntas = 15"
               :class="generadorProfundidad === 'robusta' ? 'border-purple-500 bg-purple-500/10 ring-1 ring-purple-500' : 'border-gray-800 bg-gray-900/60 hover:border-gray-700'"
               class="p-3 rounded-2xl border cursor-pointer transition flex flex-col justify-between space-y-1">
            <div class="flex items-center justify-between">
              <span class="text-xs font-bold text-purple-400 flex items-center space-x-1.5">
                <i class="fa-solid fa-brain"></i>
                <span>Robusta</span>
              </span>
              <span class="text-[10px] font-mono px-1.5 py-0.5 rounded bg-purple-500/20 text-purple-300">Pro / 10</span>
            </div>
            <p class="text-[11px] text-gray-300 font-medium">Alto Rendimiento</p>
            <p class="text-[10px] text-gray-400 leading-tight">~4.500+ palabras, código de producción, trampas UNIR y test de 15 preguntas.</p>
          </div>
        </div>
      </div>

      <!-- Selector de Preguntas para el Test -->
      <div class="flex items-center justify-between bg-gray-900/80 p-2.5 rounded-xl border border-gray-800">
        <label class="text-xs text-gray-300 font-semibold flex items-center space-x-2">
          <i class="fa-solid fa-list-check text-emerald-400"></i>
          <span>Preguntas en el simulacro tipo test:</span>
        </label>
        <div class="flex items-center space-x-1.5">
          <template x-for="n in [5, 10, 15, 20]" :key="n">
            <button type="button"
                    @click="generadorNumPreguntas = n"
                    :class="generadorNumPreguntas == n ? 'bg-emerald-500 text-gray-900 font-bold shadow-md shadow-emerald-500/20' : 'bg-gray-800 text-gray-300 hover:bg-gray-700'"
                    class="px-2.5 py-1 rounded-lg text-xs font-medium transition"
                    x-text="n">
            </button>
          </template>
        </div>
      </div>

      <!-- PROGRESO PASO A PASO DURANTE LA GENERACIÓN -->
      <div x-show="generandoIa" x-transition class="p-3.5 rounded-2xl bg-emerald-950/30 border border-emerald-500/30 space-y-2">
        <div class="flex items-center justify-between text-xs">
          <span class="font-bold text-emerald-300 flex items-center space-x-2">
            <i class="fa-solid fa-spinner fa-spin text-emerald-400"></i>
            <span x-text="generandoIaPaso === 1 ? 'Paso 1/3: Extrayendo PDF oficial y vinculando videoclase del tema...' : (generandoIaPaso === 2 ? 'Paso 2/3: Redactando Guía de Estudio (' + generadorProfundidad.toUpperCase() + ') con NOVA...' : 'Paso 3/3: Diseñando Simulacro Tipo Test (' + generadorNumPreguntas + ' preguntas UNIR)...')"></span>
          </span>
          <span class="text-[11px] font-mono text-emerald-400" x-text="generandoIaPaso + ' / 3'"></span>
        </div>
        <div class="w-full bg-gray-900 rounded-full h-1.5 overflow-hidden">
          <div class="bg-gradient-to-r from-emerald-500 to-cyan-400 h-full transition-all duration-500"
               :style="'width: ' + (generandoIaPaso === 1 ? '33%' : (generandoIaPaso === 2 ? '68%' : '95%'))"></div>
        </div>
      </div>

      <div class="pt-2 flex justify-end space-x-3">
        <button @click="modalGenerador = false" class="px-4 py-2 bg-gray-800 text-gray-300 rounded-xl text-xs font-semibold">Cancelar</button>
        <button @click="ejecutarGeneracionIa()" :disabled="generandoIa || !generadorTemaNombre.trim()" class="px-5 py-2 bg-gradient-to-r from-emerald-500 to-teal-600 text-gray-900 font-bold rounded-xl text-xs transition flex items-center space-x-2 disabled:opacity-50">
          <i :class="generandoIa ? 'fa-solid fa-spinner fa-spin' : 'fa-solid fa-bolt'"></i>
          <span x-text="generandoIa ? 'Generando con IA...' : 'Generar Paquete de Estudio'"></span>
        </button>
      </div>
    </div>
  </div>

  <!-- ==================== MODAL: VISOR DE DOCUMENTOS ==================== -->
  <div x-show="modalVisor" x-transition class="fixed inset-0 z-50 flex items-center justify-center p-3 md:p-6 bg-black/80 backdrop-blur-sm">
    <div class="glass w-full max-w-5xl lg:max-w-6xl max-h-[90vh] rounded-3xl flex flex-col overflow-hidden border border-gray-700 shadow-2xl">
      <div class="p-4 bg-gray-900 border-b border-gray-800 flex items-center justify-between gap-4">
        <h4 class="font-bold text-white text-sm break-words pr-2 line-clamp-2" x-text="modalTitulo"></h4>
        <div class="flex items-center space-x-1.5 shrink-0">
          <a :href="'/?guia=' + encodeURIComponent(modalRutaActiva || '') + '&asig=' + encodeURIComponent(asigSeleccionada?.nombre || '')" 
             target="_blank" title="Abrir en pestaña nueva del navegador" class="p-2 text-gray-400 hover:text-cyan-400 rounded-lg hover:bg-gray-800 transition">
            <i class="fa-solid fa-arrow-up-right-from-square text-sm"></i>
          </a>
          <button @click="modalVisor = false" class="text-gray-400 hover:text-white p-2 rounded-lg" title="Minimizar (conservar en pestañas)">
            <i class="fa-solid fa-minus text-sm"></i>
          </button>
          <button @click="modalVisor = false" class="text-gray-400 hover:text-white p-2 rounded-lg" title="Cerrar visor">
            <i class="fa-solid fa-xmark text-lg"></i>
          </button>
        </div>
      </div>
      <div class="flex-1 p-6 overflow-y-auto markdown-content text-sm leading-relaxed" x-html="renderMarkdown(modalContenido)"></div>
    </div>
  </div>

  <!-- ==================== MODAL: SIMULADOR DE EXAMEN WEB (MODO NOTEBOOK & EXAMEN) ==================== -->
  <div x-show="modalTest" x-transition class="fixed inset-0 z-50 flex items-center justify-center p-3 md:p-6 bg-black/85 backdrop-blur-md">
    <div class="glass w-full max-w-3xl max-h-[92vh] rounded-3xl flex flex-col overflow-hidden border border-emerald-500/30 shadow-2xl">
      
      <!-- HEADER TEST -->
      <div class="p-4 bg-gray-900 border-b border-gray-800 flex items-center justify-between">
        <div class="flex items-center space-x-3">
          <div class="w-10 h-10 rounded-xl bg-gradient-to-br from-emerald-500/20 to-teal-500/20 border border-emerald-500/30 text-emerald-400 flex items-center justify-center text-lg">
            <i class="fa-solid fa-spell-check"></i>
          </div>
          <div>
            <div class="flex items-center space-x-2">
              <h4 class="font-bold text-white text-sm" x-text="'Simulacro: ' + testActivoAsig"></h4>
              <span class="text-[10px] font-bold px-2 py-0.5 rounded-full bg-cyan-500/10 text-cyan-400 border border-cyan-500/20 font-mono"
                    x-text="'Pregunta ' + (testIndice + 1) + ' / ' + (testPreguntas.length)"></span>
            </div>
            <p class="text-xs text-gray-400 mt-0.5" x-text="testActivoNombre.replace('_test', '').replaceAll('_', ' ')"></p>
          </div>
        </div>

        <div class="flex items-center space-x-2.5">
          <!-- SELECTOR DE MODO -->
          <div class="flex items-center bg-gray-950 rounded-xl p-1 border border-gray-800 text-xs">
            <button @click="testModo = 'notebook'"
                    :class="testModo === 'notebook' ? 'bg-emerald-500 text-gray-950 font-black shadow' : 'text-gray-400 hover:text-white'"
                    class="px-2.5 py-1 rounded-lg transition text-[11px] flex items-center space-x-1"
                    title="Feedback inmediato pregunta a pregunta con explicaciones y pistas">
              <i class="fa-solid fa-bolt text-[10px]"></i>
              <span class="hidden sm:inline">Notebook</span>
            </button>
            <button @click="testModo = 'examen'"
                    :class="testModo === 'examen' ? 'bg-emerald-500 text-gray-950 font-black shadow' : 'text-gray-400 hover:text-white'"
                    class="px-2.5 py-1 rounded-lg transition text-[11px] flex items-center space-x-1"
                    title="Examen oficial a ciegas con corrección al terminar">
              <i class="fa-solid fa-clock text-[10px]"></i>
              <span class="hidden sm:inline">Examen</span>
            </button>
          </div>

          <a :href="'/?test=' + encodeURIComponent(testActivoNombre) + '&asig=' + encodeURIComponent(testActivoAsig)" target="_blank"
             title="Abrir este simulacro en pestaña nueva del navegador" class="text-gray-400 hover:text-emerald-400 p-2 rounded-xl hover:bg-gray-800 transition">
            <i class="fa-solid fa-arrow-up-right-from-square text-sm"></i>
          </a>
          <button @click="modalTest = false" title="Minimizar (conservar respuestas en curso)" class="text-gray-400 hover:text-white p-2 rounded-xl hover:bg-gray-800 transition">
            <i class="fa-solid fa-minus text-sm"></i>
          </button>
          <button @click="if (confirm('¿Deseas cerrar el simulador? Podrás retomarlo desde la pestaña superior en cualquier momento.')) { modalTest = false; }" 
                  class="text-gray-400 hover:text-rose-400 p-2 rounded-xl hover:bg-gray-800 transition" title="Cerrar simulacro">
            <i class="fa-solid fa-xmark text-lg"></i>
          </button>
        </div>
      </div>

      <!-- BARRA DE PROGRESO -->
      <div class="w-full bg-gray-950 h-1.5 overflow-hidden" x-show="!testFinalizado && testPreguntas.length > 0">
        <div class="bg-gradient-to-r from-emerald-500 via-teal-400 to-cyan-500 h-full transition-all duration-300"
             :style="'width: ' + (((testIndice + (testRespuestasUsuario[testIndice] ? 1 : 0)) / testPreguntas.length) * 100) + '%'"></div>
      </div>

      <!-- CUERPO PREGUNTA Y OPCIONES -->
      <div class="flex-1 p-6 overflow-y-auto space-y-4" x-show="!testFinalizado && testPreguntas.length > 0">
        <!-- ENUNCIADO -->
        <h3 class="text-base font-bold text-white leading-snug" x-text="testPreguntas[testIndice]?.enunciado"></h3>

        <!-- PISTA CONCEPTUAL DESPLEGABLE -->
        <div>
          <button @click="testMostrarPista = !testMostrarPista"
                  class="inline-flex items-center space-x-2 text-xs font-semibold px-3 py-1.5 rounded-xl transition border"
                  :class="testMostrarPista ? 'bg-amber-500/20 text-amber-300 border-amber-500/40 shadow-sm' : 'bg-gray-800 hover:bg-gray-700 text-amber-400/90 border-gray-700'">
            <i class="fa-solid fa-lightbulb text-[11px]"></i>
            <span x-text="testMostrarPista ? 'Ocultar pista' : '💡 ¿Dudas con la pregunta? Ver pista conceptual'"></span>
          </button>

          <div x-show="testMostrarPista"
               x-transition:enter="transition ease-out duration-200"
               x-transition:enter-start="opacity-0 -translate-y-1"
               x-transition:enter-end="opacity-100 translate-y-0"
               class="mt-2.5 p-3.5 rounded-2xl bg-amber-950/20 border border-amber-500/30 text-amber-200 text-xs leading-relaxed flex items-start space-x-3 shadow-lg">
            <div class="w-6 h-6 rounded-lg bg-amber-500/20 text-amber-400 flex items-center justify-center shrink-0 mt-0.5">
              <i class="fa-solid fa-key text-xs"></i>
            </div>
            <div class="flex-1">
              <span class="font-bold text-amber-300 block mb-0.5">Pista para razonar la respuesta:</span>
              <p class="text-amber-100/90 text-xs" x-text="obtenerPistaPregunta(testPreguntas[testIndice])"></p>
            </div>
          </div>
        </div>

        <!-- OPCIONES -->
        <div class="space-y-3 pt-1">
          <template x-for="(texto, letra) in testPreguntas[testIndice]?.opciones" :key="letra">
            <button @click="seleccionarOpcion(letra)"
                    :disabled="testModo === 'notebook' && !!testRespuestasUsuario[testIndice]"
                    :class="{
                      'border-emerald-500 bg-emerald-500/20 text-emerald-100 ring-2 ring-emerald-500/50 shadow-lg shadow-emerald-500/10':
                        testModo === 'notebook' && testRespuestasUsuario[testIndice] && letra === testPreguntas[testIndice]?.correcta,
                      'border-rose-500 bg-rose-500/20 text-rose-100 ring-2 ring-rose-500/50 shadow-lg shadow-rose-500/10':
                        testModo === 'notebook' && testRespuestasUsuario[testIndice] && testRespuestasUsuario[testIndice] === letra && letra !== testPreguntas[testIndice]?.correcta,
                      'opacity-40 border-gray-800 bg-gray-900/40 text-gray-400':
                        testModo === 'notebook' && testRespuestasUsuario[testIndice] && testRespuestasUsuario[testIndice] !== letra && letra !== testPreguntas[testIndice]?.correcta,
                      'border-cyan-500 bg-cyan-500/10 text-white':
                        (testModo !== 'notebook' || !testRespuestasUsuario[testIndice]) && testRespuestaSeleccionada === letra,
                      'border-gray-700/80 bg-gray-800/50 text-gray-300 hover:bg-gray-800 hover:border-gray-600':
                        !testRespuestasUsuario[testIndice] && testRespuestaSeleccionada !== letra
                    }"
                    class="w-full text-left p-4 rounded-2xl border transition-all duration-200 flex items-start justify-between space-x-3 group relative">
              
              <div class="flex items-start space-x-3.5">
                <span class="w-7 h-7 rounded-xl flex items-center justify-center text-xs font-bold shrink-0 transition-colors"
                      :class="{
                        'bg-emerald-500 text-gray-950 font-black shadow':
                          testModo === 'notebook' && testRespuestasUsuario[testIndice] && letra === testPreguntas[testIndice]?.correcta,
                        'bg-rose-500 text-white font-black shadow':
                          testModo === 'notebook' && testRespuestasUsuario[testIndice] && testRespuestasUsuario[testIndice] === letra && letra !== testPreguntas[testIndice]?.correcta,
                        'bg-gray-700 text-white group-hover:bg-gray-600':
                          !testRespuestasUsuario[testIndice] || testModo !== 'notebook'
                      }"
                      x-text="letra"></span>
                <span class="text-sm font-medium leading-relaxed pt-0.5" x-text="texto"></span>
              </div>

              <!-- Indicador de acierto / fallo -->
              <div x-show="testModo === 'notebook' && testRespuestasUsuario[testIndice]" class="shrink-0 pt-0.5">
                <span x-show="letra === testPreguntas[testIndice]?.correcta" class="flex items-center space-x-1 text-emerald-400 font-bold text-xs bg-emerald-500/20 px-2.5 py-0.5 rounded-full border border-emerald-500/30">
                  <i class="fa-solid fa-check"></i>
                  <span>Correcta</span>
                </span>
                <span x-show="testRespuestasUsuario[testIndice] === letra && letra !== testPreguntas[testIndice]?.correcta" class="flex items-center space-x-1 text-rose-400 font-bold text-xs bg-rose-500/20 px-2.5 py-0.5 rounded-full border border-rose-500/30">
                  <i class="fa-solid fa-xmark"></i>
                  <span>Tu fallo</span>
                </span>
              </div>
            </button>
          </template>
        </div>

        <!-- EXPLICACIÓN INSTANTÁNEA (JUSTIFICACIÓN ESTILO GEMINI NOTEBOOK) -->
        <div x-show="testModo === 'notebook' && testRespuestasUsuario[testIndice]"
             x-transition:enter="transition ease-out duration-300 transform"
             x-transition:enter-start="opacity-0 translate-y-3"
             x-transition:enter-end="opacity-100 translate-y-0"
             class="mt-4 p-4.5 rounded-2xl border"
             :class="testRespuestasUsuario[testIndice] === testPreguntas[testIndice]?.correcta ? 'bg-emerald-950/30 border-emerald-500/40' : 'bg-rose-950/30 border-rose-500/40'">
          
          <div class="flex items-center justify-between mb-2">
            <div class="flex items-center space-x-2.5">
              <div class="w-8 h-8 rounded-xl flex items-center justify-center text-sm"
                   :class="testRespuestasUsuario[testIndice] === testPreguntas[testIndice]?.correcta ? 'bg-emerald-500/20 text-emerald-400' : 'bg-rose-500/20 text-rose-400'">
                <i :class="testRespuestasUsuario[testIndice] === testPreguntas[testIndice]?.correcta ? 'fa-solid fa-circle-check' : 'fa-solid fa-circle-xmark'"></i>
              </div>
              <div>
                <h5 class="text-sm font-bold"
                    :class="testRespuestasUsuario[testIndice] === testPreguntas[testIndice]?.correcta ? 'text-emerald-300' : 'text-rose-300'"
                    x-text="testRespuestasUsuario[testIndice] === testPreguntas[testIndice]?.correcta ? '¡Respuesta Correcta!' : '¡Respuesta Incorrecta!'"></h5>
                <p class="text-[11px] text-gray-400"
                   x-show="testRespuestasUsuario[testIndice] !== testPreguntas[testIndice]?.correcta">
                  La opción correcta es la <strong class="text-emerald-400 font-bold" x-text="'[' + testPreguntas[testIndice]?.correcta + ']'"></strong>
                </p>
              </div>
            </div>

            <span class="text-[10px] font-mono px-2.5 py-1 rounded-full uppercase tracking-wider font-bold"
                  :class="testRespuestasUsuario[testIndice] === testPreguntas[testIndice]?.correcta ? 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/20' : 'bg-rose-500/10 text-rose-400 border border-rose-500/20'">
              <span x-text="testRespuestasUsuario[testIndice] === testPreguntas[testIndice]?.correcta ? '+1.0 Punto' : '-0.33 UNIR'"></span>
            </span>
          </div>

          <div class="bg-gray-900/90 rounded-xl p-3.5 border border-white/5 space-y-1.5 mt-2">
            <div class="flex items-center space-x-1.5 text-cyan-400 text-xs font-bold">
              <i class="fa-solid fa-graduation-cap text-[11px]"></i>
              <span>¿Por qué? Justificación técnica oficial:</span>
            </div>
            <p class="text-xs text-gray-200 leading-relaxed font-sans" x-text="testPreguntas[testIndice]?.justificacion"></p>
          </div>
        </div>
      </div>

      <!-- PANTALLA DE RESULTADOS AL FINALIZAR -->
      <div class="flex-1 p-8 text-center space-y-4" x-show="testFinalizado">
        <div class="w-20 h-20 rounded-full mx-auto flex items-center justify-center text-3xl"
             :class="testNota >= 5 ? 'bg-emerald-500/20 text-emerald-400 border border-emerald-500/40' : 'bg-rose-500/20 text-rose-400 border border-rose-500/40'">
          <i :class="testNota >= 5 ? 'fa-solid fa-check' : 'fa-solid fa-xmark'"></i>
        </div>
        <h3 class="text-2xl font-extrabold text-white" x-text="testNota >= 5 ? '¡Enhorabuena, has aprobado!' : 'Necesitas repasar este tema'"></h3>
        <p class="text-gray-400 text-sm">Tu puntuación oficial en el simulacro:</p>
        <p class="text-5xl font-black" :class="testNota >= 5 ? 'text-emerald-400' : 'text-rose-400'" x-text="testNota.toFixed(2) + ' / 10'"></p>
        
        <!-- DESGLOSE OFICIAL UNIR (+1 / -0.33) -->
        <div class="flex flex-wrap items-center justify-center gap-3 py-2 px-4 bg-gray-900/90 rounded-2xl border border-gray-800 max-w-sm mx-auto text-xs font-mono">
          <span class="text-emerald-400 font-bold" x-text="'+' + testAciertos + ' Aciertos'"></span>
          <span class="text-rose-400 font-bold" x-text="'-' + testFallos + ' Fallos (-' + (testFallos * 0.33).toFixed(2) + ')'"></span>
          <span class="text-gray-400" x-text="testBlancos + ' Blanco'"></span>
        </div>
        <p class="text-[11px] text-gray-500">Baremo oficial UNIR: Acierto (+1.0) • Fallo (-0.33) • Blanco (0.0)</p>
        <div class="flex flex-wrap items-center justify-center gap-3 pt-2">
          <button x-show="(testFallos + testBlancos) > 0"
                  @click="reintentarFallosIntentoActual()"
                  class="px-5 py-2.5 bg-gradient-to-r from-rose-500 to-amber-500 hover:from-rose-400 hover:to-amber-400 text-gray-950 font-black rounded-xl text-xs transition flex items-center space-x-2 shadow-lg shadow-rose-500/20">
            <i class="fa-solid fa-fire-flame-curved"></i>
            <span x-text="'Repasar Ahora mis ' + (testFallos + testBlancos) + ' Errores / Blancos'"></span>
          </button>
          <button @click="modalTest = false" class="px-6 py-2.5 bg-gray-800 hover:bg-gray-700 text-white rounded-xl text-xs font-semibold transition">
            Cerrar y Guardar Nota
          </button>
        </div>
      </div>

      <!-- FOOTER NAVEGACIÓN -->
      <div class="p-4 bg-gray-900 border-t border-gray-800 flex items-center justify-between" x-show="!testFinalizado">
        <button @click="anteriorPregunta()" :disabled="testIndice === 0" class="px-4 py-2 bg-gray-800 hover:bg-gray-700 text-gray-300 rounded-xl text-xs font-semibold transition disabled:opacity-30 disabled:pointer-events-none flex items-center space-x-1.5">
          <i class="fa-solid fa-chevron-left text-[10px]"></i>
          <span>Anterior</span>
        </button>

        <div class="hidden sm:flex items-center space-x-3 text-xs font-mono" x-show="testModo === 'notebook'">
          <span class="text-emerald-400 font-bold flex items-center space-x-1">
            <i class="fa-solid fa-check text-[10px]"></i>
            <span x-text="contarAciertosVivos() + ' Aciertos'"></span>
          </span>
          <span class="text-rose-400 font-bold flex items-center space-x-1">
            <i class="fa-solid fa-xmark text-[10px]"></i>
            <span x-text="contarFallosVivos() + ' Fallos'"></span>
          </span>
        </div>

        <button @click="siguientePregunta()" class="px-6 py-2.5 bg-gradient-to-r from-emerald-500 to-teal-500 hover:from-emerald-400 hover:to-teal-400 text-gray-950 font-black rounded-xl text-xs transition shadow-lg shadow-emerald-500/20 flex items-center space-x-2">
          <span x-text="testIndice === testPreguntas.length - 1 ? 'Finalizar y Ver Nota' : (testModo === 'notebook' && !testRespuestasUsuario[testIndice] ? 'Saltar Pregunta' : 'Siguiente Pregunta')"></span>
          <i class="fa-solid fa-chevron-right text-[10px]"></i>
        </button>
      </div>
    </div>
  </div>

  <!-- ==================== MODAL: VISOR DE CLASE CON MINUTERO INTERACTIVO ==================== -->
  <div x-show="modalMinutero" x-transition class="fixed inset-0 z-50 flex items-center justify-center p-3 md:p-6 bg-black/85 backdrop-blur-md">
    <div class="glass w-full max-w-5xl max-h-[92vh] rounded-3xl flex flex-col overflow-hidden border border-amber-500/30 shadow-2xl">
      <!-- HEADER -->
      <div class="p-4 bg-gray-900/95 border-b border-gray-800 flex flex-wrap items-center justify-between gap-3">
        <div class="flex items-center space-x-3">
          <div class="w-10 h-10 rounded-xl bg-amber-500/10 text-amber-400 flex items-center justify-center text-lg shrink-0">
            <i class="fa-solid fa-clock"></i>
          </div>
          <div>
            <div class="flex items-center space-x-2">
              <span class="text-[10px] font-bold px-2 py-0.5 rounded-full bg-cyan-500/10 text-cyan-400 uppercase" x-text="minuteroClase?.asignatura"></span>
              <span class="text-xs text-amber-400 font-mono" x-text="minuteroClase?.fecha"></span>
            </div>
            <h3 class="font-bold text-white text-sm mt-0.5" x-text="minuteroClase?.titulo"></h3>
          </div>
        </div>

        <div class="flex items-center space-x-2">
          <a x-show="minuteroClase?.recording_url" :href="minuteroClase?.recording_url" target="_blank" class="px-3 py-1.5 bg-amber-500/20 hover:bg-amber-500/30 text-amber-300 rounded-xl text-xs font-bold transition flex items-center space-x-1.5 border border-amber-500/30">
            <i class="fa-solid fa-play"></i>
            <span>Grabación UNIR</span>
          </a>
          <button @click="modalMinutero = false" class="text-gray-400 hover:text-white p-2 rounded-lg">
            <i class="fa-solid fa-xmark text-lg"></i>
          </button>
        </div>
      </div>

      <!-- BUSCADOR INTERNO DENTRO DE LA CLASE -->
      <div class="px-4 py-2.5 bg-gray-950/80 border-b border-gray-800 flex items-center space-x-3">
        <i class="fa-solid fa-magnifying-glass text-xs text-amber-400"></i>
        <input type="text" x-model="minuteroFiltro" placeholder="Filtrar por frase, duda o minuto..." class="w-full bg-transparent text-white placeholder-gray-500 text-xs focus:outline-none">
        <span class="text-[10px] text-gray-500 shrink-0" x-text="minuteroCuesFiltrados.length + ' intervenciones'"></span>
      </div>

      <!-- LISTA DE INTERVENCIONES CON MINUTAJE EXACTO -->
      <div class="flex-1 p-4 md:p-6 overflow-y-auto space-y-2.5">
        <template x-for="cue in minuteroCuesFiltrados" :key="cue.start">
          <div class="p-3.5 rounded-2xl bg-gray-900/60 border border-gray-800 hover:border-amber-500/30 transition flex flex-col md:flex-row md:items-start justify-between gap-3 group">
            <div class="flex-1 space-y-1">
              <div class="flex items-center space-x-2 text-xs">
                <button @click="abrirGrabacionEnMinuto(minuteroClase, cue.start)"
                        class="font-mono font-bold text-amber-400 bg-amber-500/10 hover:bg-amber-500/25 px-2 py-0.5 rounded-lg border border-amber-500/30 text-[11px] transition flex items-center space-x-1"
                        title="Abrir grabación en este minuto">
                  <i class="fa-solid fa-play text-[9px]"></i>
                  <span x-text="cue.start_str"></span>
                </button>
                <span class="font-bold text-gray-200" x-text="cue.speaker"></span>
              </div>
              <p class="text-xs text-gray-300 leading-relaxed pl-1" x-text="cue.text"></p>
            </div>
            <button @click="preguntarMinuto(cue, minuteroClase)" class="opacity-80 group-hover:opacity-100 px-3 py-1 bg-cyan-500/10 hover:bg-cyan-500/20 text-cyan-300 border border-cyan-500/30 rounded-xl text-[11px] font-semibold transition shrink-0 flex items-center space-x-1.5 self-start">
              <i class="fa-solid fa-robot text-[10px]"></i>
              <span>Consultar a NOVA</span>
            </button>
          </div>
        </template>
        <div x-show="minuteroCuesFiltrados.length === 0" class="text-center py-12 text-gray-500 text-xs">
          <p>No se encontraron fragmentos para este filtro.</p>
        </div>
      </div>
    </div>
  </div>

  <!-- ==================== MODAL: INFORME Y RESUMEN EJECUTIVO IA DE LA CLASE ==================== -->
  <div x-show="modalResumenClase" x-transition class="fixed inset-0 z-50 flex items-center justify-center p-3 md:p-6 bg-black/85 backdrop-blur-md">
    <div class="glass w-full max-w-5xl max-h-[92vh] rounded-3xl flex flex-col overflow-hidden border border-purple-500/30 shadow-2xl">
      <!-- HEADER -->
      <div class="p-4 bg-gray-900/95 border-b border-gray-800 flex flex-wrap items-center justify-between gap-3">
        <div class="flex items-center space-x-3">
          <div class="w-10 h-10 rounded-xl bg-purple-500/10 text-purple-400 flex items-center justify-center text-lg shrink-0">
            <i class="fa-solid fa-wand-magic-sparkles"></i>
          </div>
          <div>
            <div class="flex items-center space-x-2">
              <span class="text-[10px] font-bold px-2 py-0.5 rounded-full bg-purple-500/10 text-purple-400 uppercase" x-text="resumenClaseActual?.asignatura"></span>
              <span class="text-xs text-amber-400 font-mono" x-text="resumenClaseActual?.fecha"></span>
              <span x-show="resumenClaseCached" class="text-[10px] font-semibold bg-emerald-500/10 text-emerald-400 border border-emerald-500/20 px-2 py-0.5 rounded-full">En caché local</span>
            </div>
            <h3 class="font-bold text-white text-sm mt-0.5" x-text="resumenClaseActual?.titulo"></h3>
          </div>
        </div>

        <div class="flex items-center space-x-2">
          <button @click="regenerarResumenClase()" :disabled="cargandoResumenClase" class="px-3 py-1.5 bg-gray-800 hover:bg-gray-700 text-purple-300 rounded-xl text-xs font-semibold transition flex items-center space-x-1.5 border border-purple-500/20 disabled:opacity-50" title="Volver a generar con IA">
            <i class="fa-solid fa-arrows-rotate" :class="cargandoResumenClase ? 'fa-spin' : ''"></i>
            <span>Regenerar</span>
          </button>
          <button @click="copiarResumenClase()" class="px-3 py-1.5 bg-gray-800 hover:bg-gray-700 text-gray-200 rounded-xl text-xs font-semibold transition flex items-center space-x-1.5 border border-gray-700">
            <i class="fa-regular fa-copy"></i>
            <span>Copiar</span>
          </button>
          <a x-show="resumenClaseActual?.recording_url" :href="resumenClaseActual?.recording_url" target="_blank" class="px-3 py-1.5 bg-amber-500/20 hover:bg-amber-500/30 text-amber-300 rounded-xl text-xs font-bold transition flex items-center space-x-1.5 border border-amber-500/30">
            <i class="fa-solid fa-play"></i>
            <span>Vídeo</span>
          </a>
          <button @click="modalResumenClase = false" class="text-gray-400 hover:text-white p-2 rounded-lg">
            <i class="fa-solid fa-xmark text-lg"></i>
          </button>
        </div>
      </div>

      <!-- CONTENIDO O SPINNER DE CARGA -->
      <div class="flex-1 p-6 md:p-8 overflow-y-auto">
        <!-- ESTADO CARGANDO -->
        <div x-show="cargandoResumenClase" class="text-center py-20 space-y-4">
          <div class="w-16 h-16 rounded-2xl bg-purple-500/10 text-purple-400 flex items-center justify-center text-2xl mx-auto animate-bounce border border-purple-500/20">
            <i class="fa-solid fa-brain"></i>
          </div>
          <h4 class="text-base font-bold text-white">Analizando Transcripción Completa con NOVA...</h4>
          <p class="text-xs text-gray-400 max-w-md mx-auto leading-relaxed">
            Procesando hasta 60.000 caracteres de la lección para extraer avisos de examen, explicaciones de código, minutero y preguntas resueltas.
          </p>
        </div>

        <!-- REPORTE MARKDOWN RENDERIZADO -->
        <div x-show="!cargandoResumenClase && resumenClaseTexto" class="markdown-content text-sm leading-relaxed text-gray-200 space-y-4" x-html="renderMarkdown(resumenClaseTexto)">
        </div>

        <div x-show="!cargandoResumenClase && !resumenClaseTexto" class="text-center py-16 text-gray-500 text-xs">
          <p>No se pudo generar el resumen de esta clase.</p>
        </div>
      </div>
    </div>
  </div>

  <!-- ==================== MODAL: GENERADOR DE FLASHCARDS CON IA ==================== -->
  <div x-show="modalGenerarFlashcards" x-transition class="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/80 backdrop-blur-md">
    <div class="glass w-full max-w-lg rounded-3xl p-6 border border-purple-500/40 shadow-2xl space-y-5">
      <div class="flex items-center justify-between border-b border-gray-800 pb-4">
        <div class="flex items-center space-x-3">
          <div class="w-10 h-10 rounded-xl bg-purple-500/10 text-purple-400 flex items-center justify-center text-lg">
            <i class="fa-solid fa-wand-magic-sparkles"></i>
          </div>
          <div>
            <h3 class="font-bold text-white text-base">Crear Flashcards con IA</h3>
            <p class="text-xs text-gray-400">Genera preguntas y respuestas de examen con NOVA</p>
          </div>
        </div>
        <button @click="modalGenerarFlashcards = false" class="text-gray-400 hover:text-white p-2">
          <i class="fa-solid fa-xmark text-lg"></i>
        </button>
      </div>

      <div class="space-y-4">
        <div>
          <label class="block text-xs font-bold text-gray-300 mb-1.5">Materia / Asignatura</label>
          <select x-model="nuevaCardAsig" class="w-full bg-gray-900 text-white text-xs px-3.5 py-3 rounded-xl border border-gray-700 focus:outline-none focus:border-purple-500">
            <template x-for="asig in todasLasAsignaturasPrograma" :key="asig">
              <option :value="asig" x-text="asig"></option>
            </template>
          </select>
        </div>

        <div>
          <label class="block text-xs font-bold text-gray-300 mb-1.5">Tema o Concepto a Reforzar</label>
          <input type="text" x-model="nuevaCardTema" placeholder="Ej: Consultas SQL complejas, JavaFX Layouts, Sockets TCP..."
                 class="w-full bg-gray-900 text-white placeholder-gray-500 text-xs px-3.5 py-3 rounded-xl border border-gray-700 focus:outline-none focus:border-purple-500">
        </div>

        <div class="flex items-center justify-between text-xs text-gray-400">
          <span>Cantidad de tarjetas:</span>
          <div class="flex space-x-2">
            <button @click="nuevaCardCantidad = 3" :class="nuevaCardCantidad === 3 ? 'bg-purple-600 text-white' : 'bg-gray-800 text-gray-300'" class="px-3 py-1 rounded-lg font-bold">3</button>
            <button @click="nuevaCardCantidad = 5" :class="nuevaCardCantidad === 5 ? 'bg-purple-600 text-white' : 'bg-gray-800 text-gray-300'" class="px-3 py-1 rounded-lg font-bold">5</button>
            <button @click="nuevaCardCantidad = 8" :class="nuevaCardCantidad === 8 ? 'bg-purple-600 text-white' : 'bg-gray-800 text-gray-300'" class="px-3 py-1 rounded-lg font-bold">8</button>
          </div>
        </div>
      </div>

      <div class="pt-2 flex items-center justify-end space-x-3">
        <button @click="modalGenerarFlashcards = false" class="px-4 py-2.5 bg-gray-800 hover:bg-gray-700 text-gray-300 rounded-xl text-xs font-semibold transition">
          Cancelar
        </button>
        <button @click="ejecutarGeneracionFlashcards()" :disabled="generandoFlashcards || !nuevaCardTema.trim()"
                class="px-5 py-2.5 bg-gradient-to-r from-purple-500 to-indigo-600 hover:from-purple-600 hover:to-indigo-700 text-white font-bold rounded-xl text-xs transition flex items-center space-x-2 disabled:opacity-50 shadow-lg shadow-purple-500/20">
          <i :class="generandoFlashcards ? 'fa-solid fa-spinner fa-spin' : 'fa-solid fa-wand-magic-sparkles'"></i>
          <span x-text="generandoFlashcards ? 'Generando con NOVA...' : 'Generar Flashcards'"></span>
        </button>
      </div>
    </div>
  </div>

  <!-- ==================== MODAL: VISOR DE PDF INTEGRADO ==================== -->
  <div x-show="modalVisorPdf" x-transition class="fixed inset-0 z-50 flex items-center justify-center p-2 md:p-4 bg-black/85 backdrop-blur-md">
    <div class="glass w-full max-w-6xl h-[95vh] rounded-3xl flex flex-col overflow-hidden border border-cyan-500/30 shadow-2xl">
      <!-- HEADER -->
      <div class="p-3.5 bg-gray-900/95 border-b border-gray-800 flex items-center justify-between gap-4">
        <div class="flex items-center space-x-2.5 overflow-hidden">
          <i class="fa-regular fa-file-pdf text-rose-400 text-lg shrink-0"></i>
          <h4 class="font-bold text-white text-xs md:text-sm truncate" x-text="modalTitulo"></h4>
        </div>
        <div class="flex items-center space-x-2 shrink-0">
          <button @click="consultarNovaPdf()" class="px-3 py-1.5 bg-gradient-to-r from-cyan-500 to-indigo-600 text-white rounded-xl text-xs font-semibold hover:shadow-lg transition flex items-center space-x-1.5">
            <i class="fa-solid fa-robot"></i>
            <span>Preguntar a NOVA</span>
          </button>
          <a :href="'/api/archivo-raw?ruta=' + encodeURIComponent(modalRutaPdf)" target="_blank" class="px-2.5 py-1.5 bg-gray-800 hover:bg-gray-700 text-gray-300 rounded-xl text-xs transition" title="Abrir en pestaña nueva del navegador">
            <i class="fa-solid fa-arrow-up-right-from-square"></i>
          </a>
          <button @click="modalVisorPdf = false" class="text-gray-400 hover:text-white p-1.5 rounded-lg" title="Minimizar (conservar en pestañas)">
            <i class="fa-solid fa-minus text-sm"></i>
          </button>
          <button @click="modalVisorPdf = false" class="text-gray-400 hover:text-white p-1.5 rounded-lg" title="Cerrar visor">
            <i class="fa-solid fa-xmark text-lg"></i>
          </button>
        </div>
      </div>
      <!-- IFRAME VISOR NATIVO CON TEMA OSCURO -->
      <div class="flex-1 bg-gray-950 w-full h-full">
        <iframe :src="'/api/archivo-raw?ruta=' + encodeURIComponent(modalRutaPdf)" class="w-full h-full border-0"></iframe>
      </div>
    </div>
  </div>

  <script>
    function studyHubApp() {
      return {
        tab: 'dashboard',
        sidebarVisible: localStorage.getItem('unir_sidebar_visible') !== 'false',
        
        // Sistema de Pestañas Multi-tarea
        pestanasAbiertas: [
          { id: 'dashboard', titulo: 'Inicio (Dashboard)', icono: 'fa-house', tipo: 'seccion', seccion: 'dashboard', url: '/?tab=dashboard' }
        ],
        pestanaActivaId: 'dashboard',
        modalRutaActiva: '',

        programas: [],
        programaActivoId: 'dam',
        cursoFiltro: 'todos',
        busquedaTexto: '',
        catalogoTotal: [],
        catalogoFiltrado: [],
        progreso: {},
        asigSeleccionada: null,
        sincronizando: false,
        escaneando1dam: false,
        sincronizandoRespaldo: false,
        descargandoCanvas: false,

        // Generador IA Modal
        modalGenerador: false,
        generadorTemaNombre: '',
        generadorPdfSeleccionado: '',
        generadorProfundidad: 'media',
        generadorNumPreguntas: 10,
        generandoIa: false,
        generandoIaPaso: 1,
        
        // Modal Visor
        modalVisor: false,
        modalTitulo: '',
        modalContenido: '',

        // Chat
        chatInput: '',
        chatAsignatura: '',
        chatCargando: false,
        chatHistorial: [],
        chatHistorialesPorAsignatura: {},
        chatModeloSeleccionado: localStorage.getItem('unir_modelo_ia') || 'auto',
        chatModeloActualNombre: 'Gemini 3 Flash Preview',
        modelosIaDisponibles: [],

        // Test Web
        modalTest: false,
        testActivoAsig: '',
        testActivoNombre: '',
        testPreguntas: [],
        testIndice: 0,
        testRespuestasUsuario: {},
        testRespuestaSeleccionada: null,
        testFinalizado: false,
        testAciertos: 0,
        testFallos: 0,
        testBlancos: 0,
        testPuntosNetos: 0,
        testNota: 0,
        testMostrarPista: false,
        testModo: 'notebook', // 'notebook' (feedback inmediato estilo NotebookLM) o 'examen'

        // Videoclases y Transcripciones
        todasLasClases: [],
        filtroClaseAsignatura: '',
        busquedaClaseTexto: '',

        // Minutero y Citas Interactivas
        modalMinutero: false,
        minuteroClase: null,
        minuteroCues: [],
        minuteroFiltro: '',

        // Resumen Ejecutivo IA de Videoclases
        modalResumenClase: false,
        resumenClaseActual: null,
        resumenClaseTexto: '',
        cargandoResumenClase: false,
        resumenClaseCached: false,

        // Visor PDF Integrado
        modalVisorPdf: false,
        modalRutaPdf: '',

        // Agenda Canvas LMS
        agendaEventos: [],
        sincronizandoAgenda: false,

        // Flashcards Repetición Espaciada
        flashcards: [],
        flashcardIndice: 0,
        flashcardVolteada: false,
        flashcardFiltroAsig: '',
        modalGenerarFlashcards: false,
        nuevaCardAsig: '',
        nuevaCardTema: '',
        nuevaCardCantidad: 4,
        generandoFlashcards: false,

        get programaActual() {
          return this.programas.find(p => p.id === this.programaActivoId) || this.programas[0];
        },

        get totalMaterialesPrograma() {
          return this.catalogoFiltrado.reduce((acc, a) => acc + (a.total_materiales || 0), 0);
        },

        get totalClasesPrograma() {
          return this.catalogoFiltrado.reduce((acc, a) => acc + (a.total_clases || 0), 0);
        },

        get totalTestsPrograma() {
          return this.catalogoFiltrado.reduce((acc, a) => acc + (a.tests?.length || 0), 0);
        },

        get videoclasesFiltradas() {
          let lista = this.todasLasClases;
          if (this.filtroClaseAsignatura) {
            lista = lista.filter(c => c.asignatura === this.filtroClaseAsignatura);
          }
          if (this.busquedaClaseTexto.trim()) {
            const q = this.busquedaClaseTexto.toLowerCase();
            lista = lista.filter(c => c.titulo.toLowerCase().includes(q) || c.asignatura.toLowerCase().includes(q) || (c.summary && c.summary.toLowerCase().includes(q)) || c.fecha.includes(q));
          }
          return lista;
        },

        get asignaturasConClases() {
          return [...new Set(this.todasLasClases.map(c => c.asignatura))].sort();
        },

        get clasesDeAsignaturaActual() {
          if (!this.chatAsignatura) return [];
          return this.todasLasClases.filter(c => c.asignatura.toLowerCase().includes(this.chatAsignatura.toLowerCase()) || this.chatAsignatura.toLowerCase().includes(c.asignatura.toLowerCase()));
        },

        get minuteroCuesFiltrados() {
          if (!this.minuteroFiltro.trim()) return this.minuteroCues;
          const q = this.minuteroFiltro.toLowerCase();
          return this.minuteroCues.filter(c => c.text.toLowerCase().includes(q) || c.speaker.toLowerCase().includes(q) || c.start_str.includes(q));
        },

        get flashcardsFiltradas() {
          let lista = this.flashcards;
          if (this.flashcardFiltroAsig) {
            lista = lista.filter(f => f.asignatura === this.flashcardFiltroAsig);
          }
          return lista;
        },

        get tarjetaActual() {
          return this.flashcardsFiltradas[this.flashcardIndice] || null;
        },

        get asignaturasConFlashcards() {
          return [...new Set(this.flashcards.map(f => f.asignatura))].sort();
        },

        get todasLasAsignaturasPrograma() {
          const asigs = new Set();
          this.catalogoFiltrado.forEach(a => asigs.add(a.nombre));
          this.flashcards.forEach(f => asigs.add(f.asignatura));
          return [...asigs].sort();
        },

        async init() {
          if (window.marked) {
            marked.setOptions({
              gfm: true,
              breaks: true,
            });
          }
          await this.cargarProgramas();
          await this.cargarCatalogo();
          await this.cargarProgreso();
          await this.cargarVideoclases();
          await this.cargarAgenda();
          await this.cargarFlashcards();
          await this.cargarModelosIa();
          this.actualizarMensajeBienvenidaChat();
          await this.procesarUrlInicial();
        },

        async cargarModelosIa() {
          try {
            const res = await fetch('/api/modelos-ia');
            const data = await res.json();
            this.modelosIaDisponibles = data.modelos || [];
            if (data.ultimo_modelo_nombre) {
              this.chatModeloActualNombre = data.ultimo_modelo_nombre;
            }
          } catch(e) { console.error(e); }
        },

        cambiarModeloIa(nuevoModelo) {
          this.chatModeloSeleccionado = nuevoModelo || 'auto';
          try {
            localStorage.setItem('unir_modelo_ia', this.chatModeloSeleccionado);
          } catch(e) {}
          const modObj = this.modelosIaDisponibles.find(m => m.id === this.chatModeloSeleccionado);
          if (modObj && this.chatModeloSeleccionado !== 'auto') {
            this.chatModeloActualNombre = modObj.nombre;
          }
        },

        async configurarClavesIaGratis() {
          const openrouterKey = prompt(
            '🌐 Clave Gratuita de OpenRouter (opcional):\\n\\n' +
            'Consíguela gratis en https://openrouter.ai/keys para usar DeepSeek V3 Free, Qwen 3 235B Free, Llama 3.3 70B Free y Mistral Small Free.\\n\\n' +
            'Pégala aquí (o déjalo vacío para pasar a Groq):'
          );
          const groqKey = prompt(
            '⚡ Clave Gratuita de Groq Cloud (opcional):\\n\\n' +
            'Consíguela gratis en https://console.groq.com/keys para usar Llama 3.3 70B LPU y DeepSeek R1 70B a velocidad instantánea.\\n\\n' +
            'Pégala aquí (o déjalo vacío):'
          );
          if ((!openrouterKey || !openrouterKey.trim()) && (!groqKey || !groqKey.trim())) {
            return;
          }
          try {
            const res = await fetch('/api/proveedores-ia', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({
                openrouter_key: openrouterKey ? openrouterKey.trim() : null,
                groq_key: groqKey ? groqKey.trim() : null
              })
            });
            const data = await res.json();
            this.modelosIaDisponibles = data.modelos || [];
            alert('¡Proveedores gratuitos guardados en .env y activados!');
          } catch(e) {
            alert('No se pudieron guardar las claves.');
          }
        },

        abrirOPestana(p) {
          const index = this.pestanasAbiertas.findIndex(x => x.id === p.id);
          if (index === -1) {
            this.pestanasAbiertas.push(p);
          } else {
            this.pestanasAbiertas[index] = { ...this.pestanasAbiertas[index], ...p };
          }
          this.activarPestana(p.id);
        },

        activarPestana(id) {
          this.pestanaActivaId = id;
          const p = this.pestanasAbiertas.find(x => x.id === id);
          if (!p) return;

          if (p.url && window.history && window.history.replaceState) {
            window.history.replaceState(null, '', p.url);
          }

          if (p.tipo === 'tutor' || p.id.startsWith('tutor-')) {
            this.tab = 'tutor';
            this.asigSeleccionada = null;
            this.modalVisor = false;
            this.modalTest = false;
            this.modalVisorPdf = false;
            const targetAsig = p.data?.asignatura !== undefined ? p.data.asignatura : (p.id.replace('tutor-', '') === 'global' ? '' : (p.data?.asignatura || this.chatAsignatura));
            if (this.chatAsignatura !== targetAsig) {
              this.guardarHistorialActual();
              this.chatAsignatura = targetAsig;
              this.cargarHistorialParaAsignatura(targetAsig);
            }
          } else if (p.tipo === 'seccion') {
            this.tab = p.seccion;
            this.asigSeleccionada = null;
            this.modalVisor = false;
            this.modalTest = false;
            this.modalVisorPdf = false;
            if (p.seccion === 'tutor') {
              this.cargarHistorialParaAsignatura(this.chatAsignatura || '');
            }
          } else if (p.tipo === 'asignatura') {
            this.tab = 'asignaturas';
            const asigObj = this.catalogoTotal.find(a => a.nombre.toLowerCase() === (p.data?.nombre || p.data || '').toLowerCase());
            if (asigObj) {
              this.asigSeleccionada = asigObj;
            } else if (p.data && typeof p.data === 'object') {
              this.asigSeleccionada = p.data;
            }
            this.modalVisor = false;
            this.modalTest = false;
            this.modalVisorPdf = false;
          } else if (p.tipo === 'guia') {
            this.modalTitulo = p.titulo;
            this.modalContenido = p.data?.contenido || this.modalContenido;
            this.modalRutaActiva = p.data?.ruta || '';
            this.modalVisor = true;
            this.modalTest = false;
            this.modalVisorPdf = false;
          } else if (p.tipo === 'test') {
            this.modalTest = true;
            this.modalVisor = false;
            this.modalVisorPdf = false;
          } else if (p.tipo === 'pdf') {
            this.modalRutaPdf = p.data?.ruta || '';
            this.modalTitulo = p.titulo;
            this.modalVisorPdf = true;
            this.modalVisor = false;
            this.modalTest = false;
          }
        },

        cerrarPestana(id) {
          const index = this.pestanasAbiertas.findIndex(x => x.id === id);
          if (index === -1) return;

          const cerradaEsActiva = (this.pestanaActivaId === id);
          const pCerrada = this.pestanasAbiertas[index];

          if (pCerrada.tipo === 'test' && this.modalTest && this.testActivoNombre) {
            if (!this.testFinalizado) {
              if (!confirm('¿Cerrar la pestaña del test en curso? Se perderán las respuestas actuales de este intento.')) {
                return;
              }
            }
            this.modalTest = false;
          } else if (pCerrada.tipo === 'guia' && this.modalVisor) {
            this.modalVisor = false;
          } else if (pCerrada.tipo === 'pdf' && this.modalVisorPdf) {
            this.modalVisorPdf = false;
          }

          this.pestanasAbiertas.splice(index, 1);

          if (this.pestanasAbiertas.length === 0) {
            this.pestanasAbiertas.push({
              id: 'dashboard',
              titulo: 'Inicio (Dashboard)',
              icono: 'fa-house',
              tipo: 'seccion',
              seccion: 'dashboard',
              url: '/?tab=dashboard'
            });
            this.activarPestana('dashboard');
            return;
          }

          if (cerradaEsActiva) {
            const nuevoIndex = Math.max(0, index - 1);
            this.activarPestana(this.pestanasAbiertas[nuevoIndex].id);
          }
        },

        cerrarOtrasPestanas() {
          const activa = this.pestanasAbiertas.find(x => x.id === this.pestanaActivaId);
          if (activa) {
            this.pestanasAbiertas = [activa];
          }
        },

        cerrarPestanaActiva() {
          this.cerrarPestana(this.pestanaActivaId);
        },

        irASeccion(sec) {
          const titulos = {
            dashboard: 'Inicio',
            asignaturas: 'Materias & Temarios',
            clases: 'Videoclases',
            tutor: 'Tutor NOVA',
            agenda: 'Agenda & PACs',
            flashcards: 'Flashcards',
            progreso: 'Calificaciones'
          };
          const iconos = {
            dashboard: 'fa-house',
            asignaturas: 'fa-folder-tree',
            clases: 'fa-video',
            tutor: 'fa-robot',
            agenda: 'fa-calendar-check',
            flashcards: 'fa-clone',
            progreso: 'fa-trophy'
          };
          this.tab = sec;
          this.asigSeleccionada = null;
          this.abrirOPestana({
            id: 'sec-' + sec,
            titulo: titulos[sec] || sec,
            icono: iconos[sec] || 'fa-folder',
            tipo: 'seccion',
            seccion: sec,
            url: '/?tab=' + encodeURIComponent(sec)
          });
        },

        async procesarUrlInicial() {
          const params = new URLSearchParams(window.location.search);
          const tabParam = params.get('tab');
          const asigParam = params.get('asig');
          const guiaParam = params.get('guia');
          const testParam = params.get('test');
          const pdfParam = params.get('pdf');

          if (asigParam) {
            const asigObj = this.catalogoTotal.find(a => a.nombre.toLowerCase() === asigParam.toLowerCase());
            if (asigObj) {
              this.asigSeleccionada = asigObj;
            }
          }

          if (testParam && asigParam) {
            await this.iniciarTestWeb(asigParam, testParam);
          } else if (guiaParam) {
            await this.verContenidoArchivo(guiaParam);
          } else if (pdfParam) {
            const nombre = pdfParam.split('/').pop() || 'Documento PDF';
            this.abrirPdf(pdfParam, nombre);
          } else if (asigParam && !tabParam) {
            if (this.asigSeleccionada) {
              this.abrirAsignatura(this.asigSeleccionada);
            }
          } else if (tabParam) {
            if (tabParam === 'tutor' && asigParam) {
              this.abrirChatConAsignatura(asigParam);
            } else {
              this.irASeccion(tabParam);
            }
          }
        },

        async cargarProgramas() {
          try {
            const res = await fetch('/api/programas');
            this.programas = await res.json();
          } catch(e) { console.error(e); }
        },

        async cargarCatalogo() {
          try {
            const res = await fetch('/api/catalogo');
            this.catalogoTotal = await res.json();
            this.aplicarFiltroCatalogo();
          } catch(e) { console.error(e); }
        },

        async cargarProgreso() {
          try {
            const res = await fetch('/api/progreso');
            this.progreso = await res.json();
          } catch(e) { console.error(e); }
        },

        seleccionarPrograma(progId) {
          this.programaActivoId = progId;
          this.cursoFiltro = 'todos';
          this.asigSeleccionada = null;
          this.tab = 'dashboard';
          this.aplicarFiltroCatalogo();
          this.actualizarMensajeBienvenidaChat();
          this.cargarFlashcards();
        },

        filtrarCurso(cursoId) {
          this.cursoFiltro = cursoId;
          this.asigSeleccionada = null;
          this.aplicarFiltroCatalogo();
        },

        aplicarFiltroCatalogo() {
          let lista = this.catalogoTotal.filter(a => a.programa_id === this.programaActivoId);
          if (this.programaActivoId === 'dam' && this.cursoFiltro !== 'todos') {
            const nombreCurso = this.cursoFiltro === '2dam' ? '2º Curso' : '1º Curso';
            lista = lista.filter(a => a.curso === nombreCurso);
          }
          if (this.busquedaTexto.trim()) {
            const q = this.busquedaTexto.toLowerCase();
            lista = lista.filter(a => a.nombre.toLowerCase().includes(q) || a.materiales.some(m => m.toLowerCase().includes(q)));
          }
          this.catalogoFiltrado = lista;
        },

        async sincronizarMateriales() {
          this.sincronizando = true;
          try {
            await fetch('/api/sincronizar', { method: 'POST' });
            await this.cargarProgramas();
            await this.cargarCatalogo();
            await this.cargarVideoclases();
          } catch(e) { console.error(e); }
          finally { this.sincronizando = false; }
        },

        toggleSidebar() {
          this.sidebarVisible = !this.sidebarVisible;
          try {
            localStorage.setItem('unir_sidebar_visible', this.sidebarVisible);
          } catch(e) {}
        },

        async sincronizarRespaldoLocal() {
          this.sincronizandoRespaldo = true;
          try {
            const res = await fetch('/api/sincronizar-respaldo', { method: 'POST' });
            const data = await res.json();
            await this.cargarProgramas();
            await this.cargarCatalogo();
            await this.cargarVideoclases();
            alert(data.mensaje || 'Respaldo local sincronizado correctamente.');
          } catch(e) {
            alert('Error al sincronizar el respaldo local.');
          } finally {
            this.sincronizandoRespaldo = false;
          }
        },

        async escanearPrimerAno() {
          this.escaneando1dam = true;
          try {
            const res = await fetch('/api/escanear-primer-ano', { method: 'POST' });
            const data = await res.json();
            await this.cargarProgramas();
            await this.cargarCatalogo();
            await this.cargarVideoclases();
            alert(data.mensaje);
          } catch(e) {
            alert('Error al escanear los materiales del 1er año.');
          } finally {
            this.escaneando1dam = false;
          }
        },

        async descargarCanvasOnline() {
          this.descargandoCanvas = true;
          try {
            const res = await fetch('/api/descargar-campus', { method: 'POST' });
            const data = await res.json();
            await this.cargarProgramas();
            await this.cargarCatalogo();
            await this.cargarVideoclases();
            alert(data.mensaje || ('Sincronización de Canvas completada (' + (data.nuevos_archivos || 0) + ' recursos nuevos descargados).'));
          } catch(e) {
            alert('Error al conectar con Canvas. Verifica tu sesión con: unir-hub --login');
          } finally {
            this.descargandoCanvas = false;
          }
        },

        async cargarVideoclases() {
          try {
            const res = await fetch('/api/videoclases');
            this.todasLasClases = await res.json();
          } catch(e) { console.error(e); }
        },

        preguntarSobreClase(clase) {
          this.chatAsignatura = clase.asignatura;
          this.tab = 'tutor';
          this.chatInput = '¿Qué explicó el profesor en la sesión ' + clase.titulo + ' (' + clase.fecha + ') y qué indicaciones concretas dio?';
          this.enviarMensajeChat();
        },

        async abrirResumenClase(clase) {
          this.resumenClaseActual = clase;
          this.modalResumenClase = true;
          this.cargandoResumenClase = true;
          this.resumenClaseTexto = '';
          this.resumenClaseCached = false;
          try {
            const res = await fetch('/api/resumen-clase', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({
                asignatura: clase.asignatura,
                nombre_archivo: clase.nombre_archivo
              })
            });
            const data = await res.json();
            if (data.resumen) {
              this.resumenClaseTexto = data.resumen;
              this.resumenClaseCached = !!data.cached;
              clase.has_resumen = true;
            } else {
              this.resumenClaseTexto = 'No se pudo generar el informe para esta clase.';
            }
          } catch(e) {
            console.error(e);
            this.resumenClaseTexto = 'Error de conexión al generar el resumen con NOVA.';
          } finally {
            this.cargandoResumenClase = false;
          }
        },

        async regenerarResumenClase() {
          if (!this.resumenClaseActual || this.cargandoResumenClase) return;
          this.cargandoResumenClase = true;
          this.resumenClaseTexto = '';
          try {
            const res = await fetch('/api/resumen-clase', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({
                asignatura: this.resumenClaseActual.asignatura,
                nombre_archivo: this.resumenClaseActual.nombre_archivo,
                forzar: true
              })
            });
            const data = await res.json();
            this.resumenClaseTexto = data.resumen || 'Error al regenerar.';
            this.resumenClaseCached = false;
          } catch(e) {
            console.error(e);
          } finally {
            this.cargandoResumenClase = false;
          }
        },

        copiarResumenClase() {
          if (!this.resumenClaseTexto) return;
          navigator.clipboard.writeText(this.resumenClaseTexto);
          alert('¡Resumen copiado al portapapeles!');
        },

        lanzarPreguntaRapida(texto) {
          this.chatInput = texto;
          this.enviarMensajeChat();
        },

        generarGuiaDesdePdf(pdfNombre) {
          this.generadorPdfSeleccionado = pdfNombre;
          this.generadorTemaNombre = pdfNombre.replace('.pdf', '').replaceAll('_', ' ');
          this.generadorProfundidad = 'media';
          this.generadorNumPreguntas = 10;
          this.modalGenerador = true;
        },

        async ejecutarGeneracionIa() {
          if (!this.asigSeleccionada || !this.generadorTemaNombre.trim()) return;
          this.generandoIa = true;
          this.generandoIaPaso = 1;
          const timerPaso2 = setTimeout(() => { if (this.generandoIa) this.generandoIaPaso = 2; }, 3500);
          const timerPaso3 = setTimeout(() => { if (this.generandoIa) this.generandoIaPaso = 3; }, 11000);
          try {
            const res = await fetch('/api/generar-guia', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({
                asignatura: this.asigSeleccionada.nombre,
                nombre_tema: this.generadorTemaNombre.trim(),
                archivo_pdf: this.generadorPdfSeleccionado || null,
                profundidad: this.generadorProfundidad || 'media',
                num_preguntas: parseInt(this.generadorNumPreguntas) || 10
              })
            });
            const data = await res.json();
            if (!res.ok) {
              alert(data.detail || 'Error al generar material con IA');
              return;
            }
            this.generandoIaPaso = 3;
            await this.cargarCatalogo();
            await this.cargarProgreso();
            // Refrescar asignatura seleccionada
            this.asigSeleccionada = this.catalogoTotal.find(a => a.nombre === this.asigSeleccionada.nombre) || this.asigSeleccionada;
            this.modalGenerador = false;
            this.generadorPdfSeleccionado = '';
            
            if (data.tiene_guia && data.tiene_test) {
              alert('¡Guía de estudio y test interactivo generados con éxito!');
            } else if (data.tiene_guia) {
              alert('¡Guía de estudio generada con éxito!');
            } else if (data.tiene_test) {
              alert('Se ha generado el simulacro de test, pero la redacción de la guía no pudo completarse debido a saturación temporal de la IA.');
            }
          } catch(e) {
            console.error(e);
            alert('Error de conexión al generar con IA.');
          } finally {
            clearTimeout(timerPaso2);
            clearTimeout(timerPaso3);
            this.generandoIa = false;
            this.generandoIaPaso = 1;
          }
        },

        fallosDeAsignatura(nombreAsig) {
          const lista = this.progreso?.preguntas_falladas || [];
          if (!nombreAsig) return lista;
          return lista.filter(f => f.asignatura === nombreAsig);
        },

        async iniciarRepasoFallos(nombreAsig) {
          try {
            const url = nombreAsig
              ? '/api/preguntas-falladas?asignatura=' + encodeURIComponent(nombreAsig)
              : '/api/preguntas-falladas';
            const res = await fetch(url);
            const fallos = await res.json();
            if (!Array.isArray(fallos) || fallos.length === 0) {
              alert('¡Enhorabuena! No tienes preguntas pendientes de repaso' + (nombreAsig ? ` en ${nombreAsig}` : '') + '.');
              return;
            }
            this.testPreguntas = fallos;
            this.testActivoAsig = nombreAsig || (fallos[0]?.asignatura || 'Repaso Global');
            this.testActivoNombre = nombreAsig ? `Repaso_de_Fallos_${nombreAsig}` : 'Repaso_Global_de_Fallos';
            this.testIndice = 0;
            this.testRespuestasUsuario = {};
            this.testRespuestaSeleccionada = null;
            this.testFinalizado = false;
            this.testMostrarPista = false;
            this.testModo = 'notebook';
            this.modalTest = true;
          } catch(e) {
            console.error(e);
            alert('No se pudo cargar el banco de preguntas falladas.');
          }
        },

        reintentarFallosIntentoActual() {
          const falladas = [];
          this.testPreguntas.forEach((p, idx) => {
            const resp = this.testRespuestasUsuario[idx];
            if (resp && resp !== p.correcta) {
              falladas.push(p);
            }
          });
          if (falladas.length === 0) return;
          this.testPreguntas = falladas;
          this.testActivoNombre = 'Repaso_de_Fallos_' + (this.testActivoAsig || '');
          this.testIndice = 0;
          this.testRespuestasUsuario = {};
          this.testRespuestaSeleccionada = null;
          this.testFinalizado = false;
          this.testMostrarPista = false;
          this.testModo = 'notebook';
        },

        obtenerClaveChat(asig) {
          const asigNorm = (asig || '').trim().toLowerCase().replace(/[^a-z0-9]/g, '_');
          const progId = this.programaActivoId || 'dam';
          return asigNorm ? `${progId}_${asigNorm}` : `${progId}__global_`;
        },

        obtenerMensajeBienvenida(asig) {
          if (asig && asig.trim()) {
            return `¡Hola Alex! Soy **NOVA**, tu mentora y copiloto académica en **${asig}**. ¿Qué tema, videoclase o duda técnica quieres que trabajemos hoy?`;
          }
          const p = this.programaActual;
          if (p?.id === 'ciber') {
            return '¡Hola Alex! Soy **NOVA**, tu copiloto en **Ciberseguridad y Auditorías**. ¿Qué módulo o temática (OWASP, SIEM, Forense, Malware o Hacking Ético) repasamos hoy?';
          } else if (p?.id === 'pfu' || p?.id === 'grado') {
            return '¡Hola Alex! Soy **NOVA**, tu mentora en **Ingeniería del Software & PFU**. Lista para resolver dudas de algoritmia, estructuras de datos o diseño formal.';
          } else if (p?.id === 'bootcamp') {
            return '¡Hola Alex! Soy **NOVA**, tu mentora en **Gestión Ágil y Habilidades Digitales**. ¿Repasamos Scrum, Kanban o transformación digital?';
          } else if (p?.id === 'python') {
            return '¡Hola Alex! Soy **NOVA**, tu profesora de **Programación en Python**. ¿Qué script, estructura de datos o ejercicio de ESIT ponemos a prueba hoy?';
          } else if (p?.id === 'aws') {
            return '¡Hola Alex! Soy **NOVA**, tu instructora para la certificación **AWS Cloud Practitioner (CLF-C02)**. ¿Hacemos un test rápido o repasamos servicios de AWS?';
          } else if (p?.id === 'azure') {
            return '¡Hola Alex! Soy **NOVA**, tu instructora para la certificación **Microsoft Azure Fundamentals (AZ-900)**. ¿Repasamos modelos cloud, seguridad o preguntas de examen?';
          }
          return '¡Hola Alex! Soy **NOVA**, tu copiloto para **FP DAM**. ¿Qué duda de examen o de código quieres que resolvamos hoy?';
        },

        cargarHistorialParaAsignatura(asig) {
          const clave = this.obtenerClaveChat(asig);
          if (this.chatHistorialesPorAsignatura[clave] && this.chatHistorialesPorAsignatura[clave].length > 0) {
            this.chatHistorial = this.chatHistorialesPorAsignatura[clave];
            return;
          }
          try {
            const guardado = localStorage.getItem('unir_chat_' + clave);
            if (guardado) {
              const parsed = JSON.parse(guardado);
              if (Array.isArray(parsed) && parsed.length > 0) {
                this.chatHistorialesPorAsignatura[clave] = parsed;
                this.chatHistorial = parsed;
                return;
              }
            }
          } catch(e) {
            console.error('Error cargando chat desde localStorage:', e);
          }
          const bienvenida = [{ rol: 'model', texto: this.obtenerMensajeBienvenida(asig) }];
          this.chatHistorialesPorAsignatura[clave] = bienvenida;
          this.chatHistorial = bienvenida;
          this.guardarHistorialActual();
        },

        guardarHistorialActual() {
          const clave = this.obtenerClaveChat(this.chatAsignatura);
          this.chatHistorialesPorAsignatura[clave] = this.chatHistorial;
          try {
            localStorage.setItem('unir_chat_' + clave, JSON.stringify(this.chatHistorial));
          } catch(e) {
            console.error('Error guardando chat en localStorage:', e);
          }
        },

        cambiarAsignaturaChat(nuevaAsig) {
          this.guardarHistorialActual();
          this.chatAsignatura = nuevaAsig || '';
          this.cargarHistorialParaAsignatura(this.chatAsignatura);

          const activa = this.pestanasAbiertas.find(x => x.id === this.pestanaActivaId);
          if (activa && (activa.tipo === 'tutor' || activa.seccion === 'tutor' || activa.id.startsWith('tutor-'))) {
            const clave = this.obtenerClaveChat(this.chatAsignatura);
            activa.id = 'tutor-' + clave;
            activa.titulo = this.chatAsignatura ? `Tutor (${this.chatAsignatura})` : 'Tutor NOVA';
            activa.tipo = 'tutor';
            activa.data = { asignatura: this.chatAsignatura };
            activa.url = '/?tab=tutor' + (this.chatAsignatura ? '&asig=' + encodeURIComponent(this.chatAsignatura) : '');
            this.pestanaActivaId = activa.id;
            if (window.history && window.history.replaceState) {
              window.history.replaceState(null, '', activa.url);
            }
          }
        },

        actualizarMensajeBienvenidaChat() {
          this.cargarHistorialParaAsignatura(this.chatAsignatura);
        },

        obtenerColorHex(color) {
          const map = {
            cyan: '#22d3ee',
            rose: '#fb7185',
            indigo: '#818cf8',
            amber: '#f59e0b',
            emerald: '#10b981',
            violet: '#a855f7',
            sky: '#38bdf8'
          };
          return map[color] || '#38bdf8';
        },

        obtenerFondoHex(color) {
          const map = {
            cyan: 'rgba(34, 211, 238, 0.12)',
            rose: 'rgba(251, 113, 133, 0.12)',
            indigo: 'rgba(129, 140, 248, 0.12)',
            amber: 'rgba(245, 158, 11, 0.12)',
            emerald: 'rgba(16, 185, 129, 0.12)',
            violet: 'rgba(168, 85, 247, 0.12)',
            sky: 'rgba(56, 189, 248, 0.12)'
          };
          return map[color] || 'rgba(56, 189, 248, 0.12)';
        },

        abrirAsignatura(asig) {
          this.asigSeleccionada = asig;
          this.tab = 'asignaturas';
          this.abrirOPestana({
            id: 'asig-' + asig.nombre,
            titulo: asig.nombre,
            icono: 'fa-book-bookmark',
            tipo: 'asignatura',
            data: asig,
            url: '/?asig=' + encodeURIComponent(asig.nombre)
          });
        },

        abrirChatConAsignatura(nombre) {
          this.guardarHistorialActual();
          this.chatAsignatura = nombre || '';
          this.tab = 'tutor';
          this.cargarHistorialParaAsignatura(this.chatAsignatura);
          const clave = this.obtenerClaveChat(this.chatAsignatura);
          const tit = nombre ? `Tutor (${nombre})` : 'Tutor NOVA';
          this.abrirOPestana({
            id: 'tutor-' + clave,
            titulo: tit,
            icono: 'fa-robot',
            tipo: 'tutor',
            seccion: 'tutor',
            data: { asignatura: this.chatAsignatura },
            url: '/?tab=tutor' + (nombre ? '&asig=' + encodeURIComponent(nombre) : '')
          });
        },

        async verContenidoArchivo(ruta) {
          try {
            const res = await fetch('/api/contenido?ruta=' + encodeURIComponent(ruta));
            const data = await res.json();
            const tituloLimpio = data.nombre.replace(/_/g, ' ').replace(/\\.md$/i, '');
            this.modalTitulo = tituloLimpio;
            this.modalContenido = data.contenido;
            this.modalRutaActiva = ruta;
            this.modalVisor = true;
            this.abrirOPestana({
              id: 'doc-' + ruta,
              titulo: tituloLimpio,
              icono: 'fa-file-lines',
              tipo: 'guia',
              data: { ruta, contenido: data.contenido },
              url: '/?guia=' + encodeURIComponent(ruta) + (this.asigSeleccionada ? '&asig=' + encodeURIComponent(this.asigSeleccionada.nombre) : '')
            });
          } catch(e) { alert('No se pudo abrir el archivo'); }
        },

        async eliminarGuia(asignatura, guia) {
          const nombreLegible = guia.replace('_guia.md', '').replaceAll('_', ' ');
          if (!confirm(`¿Estás seguro de que deseas eliminar la guía de estudio "${nombreLegible}"?`)) {
            return;
          }
          try {
            const res = await fetch(`/api/guia/${encodeURIComponent(asignatura)}/${encodeURIComponent(guia)}`, {
              method: 'DELETE'
            });
            if (res.ok) {
              await this.cargarCatalogo();
              await this.cargarProgreso();
              if (this.asigSeleccionada && this.asigSeleccionada.nombre === asignatura) {
                this.asigSeleccionada = this.catalogoTotal.find(a => a.nombre === asignatura) || this.asigSeleccionada;
              }
            } else {
              const err = await res.json();
              alert(err.detail || 'Error al eliminar la guía');
            }
          } catch(e) {
            console.error(e);
            alert('Error de red al intentar eliminar la guía');
          }
        },

        async eliminarTest(asignatura, test) {
          const nombreLegible = test.replace('_test', '').replaceAll('_', ' ');
          if (!confirm(`¿Estás seguro de que deseas eliminar el simulacro "${nombreLegible}"?`)) {
            return;
          }
          try {
            const res = await fetch(`/api/test/${encodeURIComponent(asignatura)}/${encodeURIComponent(test)}`, {
              method: 'DELETE'
            });
            if (res.ok) {
              await this.cargarCatalogo();
              await this.cargarProgreso();
              if (this.asigSeleccionada && this.asigSeleccionada.nombre === asignatura) {
                this.asigSeleccionada = this.catalogoTotal.find(a => a.nombre === asignatura) || this.asigSeleccionada;
              }
            } else {
              const err = await res.json();
              alert(err.detail || 'Error al eliminar el test');
            }
          } catch(e) {
            console.error(e);
            alert('Error de red al intentar eliminar el test');
          }
        },

        async iniciarTestWeb(asignatura, testNombre) {
          try {
            const res = await fetch(`/api/test/${encodeURIComponent(asignatura)}/${encodeURIComponent(testNombre)}`);
            this.testPreguntas = await res.json();
            this.testActivoAsig = asignatura;
            this.testActivoNombre = testNombre;
            this.testIndice = 0;
            this.testRespuestasUsuario = {};
            this.testRespuestaSeleccionada = null;
            this.testFinalizado = false;
            this.testMostrarPista = false;
            this.testModo = 'notebook';
            this.modalTest = true;
            const tit = testNombre.replace('_test', '').replaceAll('_', ' ');
            this.abrirOPestana({
              id: 'test-' + asignatura + '-' + testNombre,
              titulo: 'Test: ' + tit,
              icono: 'fa-spell-check',
              tipo: 'test',
              data: { asignatura, testNombre },
              url: '/?test=' + encodeURIComponent(testNombre) + '&asig=' + encodeURIComponent(asignatura)
            });
          } catch(e) { alert('No se pudo cargar el test'); }
        },

        seleccionarOpcion(letra) {
          // En modo notebook, una vez respondida la pregunta, se bloquea para evitar trampas
          if (this.testModo === 'notebook' && this.testRespuestasUsuario[this.testIndice]) {
            return;
          }
          this.testRespuestaSeleccionada = letra;
          this.testRespuestasUsuario[this.testIndice] = letra;
        },

        siguientePregunta() {
          this.testMostrarPista = false;
          if (this.testIndice < this.testPreguntas.length - 1) {
            this.testIndice++;
            this.testRespuestaSeleccionada = this.testRespuestasUsuario[this.testIndice] || null;
          } else {
            this.finalizarTest();
          }
        },

        anteriorPregunta() {
          this.testMostrarPista = false;
          if (this.testIndice > 0) {
            this.testIndice--;
            this.testRespuestaSeleccionada = this.testRespuestasUsuario[this.testIndice] || null;
          }
        },

        obtenerPistaPregunta(p) {
          if (!p) return '';
          if (p.pista && String(p.pista).trim()) return p.pista;
          if (p.justificacion) {
            const limpio = p.justificacion
              .replace(/la\\s+opción\\s+[A-D]\\s+es\\s+correcta/gi, 'la respuesta correcta se basa en')
              .replace(/opción\\s+[A-D]/gi, 'la opción adecuada')
              .replace(/respuesta\\s+correcta:\\s*[A-D]/gi, '')
              .replace(/\\([A-D]\\)/g, '');
            const oraciones = limpio.split('.').filter(s => s.trim().length > 15);
            if (oraciones.length > 0) {
              return oraciones[0].trim() + '. Recuerda relacionar los términos técnicos con la arquitectura de la materia.';
            }
          }
          return 'Recuerda los conceptos y definiciones clave tratados en el temario oficial de UNIR.';
        },

        contarAciertosVivos() {
          let count = 0;
          for (let idx in this.testRespuestasUsuario) {
            if (this.testPreguntas[idx] && this.testRespuestasUsuario[idx] === this.testPreguntas[idx].correcta) {
              count++;
            }
          }
          return count;
        },

        contarFallosVivos() {
          let count = 0;
          for (let idx in this.testRespuestasUsuario) {
            if (this.testPreguntas[idx] && this.testRespuestasUsuario[idx] && this.testRespuestasUsuario[idx] !== this.testPreguntas[idx].correcta) {
              count++;
            }
          }
          return count;
        },

        async finalizarTest() {
          try {
            const res = await fetch('/api/evaluar-test-unir', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({
                preguntas: this.testPreguntas,
                respuestas: this.testRespuestasUsuario
              })
            });
            const evalData = await res.json();
            this.testAciertos = evalData.aciertos;
            this.testFallos = evalData.fallos;
            this.testBlancos = evalData.blancos;
            this.testPuntosNetos = evalData.puntos_netos;
            this.testNota = evalData.nota;
            this.testFinalizado = true;

            const preguntasFalladas = [];
            const enunciadosAcertados = [];
            this.testPreguntas.forEach((p, idx) => {
              const resp = this.testRespuestasUsuario[idx];
              if (resp && resp === p.correcta) {
                if (p.enunciado) enunciadosAcertados.push(p.enunciado);
              } else if (resp && resp !== p.correcta) {
                preguntasFalladas.push({
                  ...p,
                  asignatura: p.asignatura || this.testActivoAsig,
                  tema: p.tema || this.testActivoNombre.replace('_test', '').replaceAll('_', ' '),
                  respuesta_erronea: resp
                });
              }
            });

            await fetch('/api/test/guardar', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({
                asignatura: this.testActivoAsig,
                tema: this.testActivoNombre.replace('_test', '').replaceAll('_', ' '),
                nota: this.testNota,
                aciertos: this.testAciertos,
                total: this.testPreguntas.length,
                preguntas_falladas: preguntasFalladas,
                enunciados_acertados: enunciadosAcertados
              })
            });
            await this.cargarProgreso();
            await this.cargarCatalogo();
          } catch(e) {
            console.error(e);
            let aciertos = 0;
            this.testPreguntas.forEach((p, idx) => {
              if (this.testRespuestasUsuario[idx] === p.correcta) aciertos++;
            });
            this.testAciertos = aciertos;
            this.testNota = (aciertos / this.testPreguntas.length) * 10;
            this.testFinalizado = true;
          }
        },

        async cargarAgenda() {
          try {
            const res = await fetch('/api/agenda');
            this.agendaEventos = await res.json();
          } catch(e) { console.error(e); }
        },

        async sincronizarAgenda() {
          this.sincronizandoAgenda = true;
          try {
            const res = await fetch('/api/sincronizar-agenda', { method: 'POST' });
            const data = await res.json();
            this.agendaEventos = data.eventos || [];
            alert(`Sincronización completada. ${this.agendaEventos.length} entregas y eventos en tu calendario de Canvas.`);
          } catch(e) {
            alert('Error al sincronizar la agenda con Canvas.');
          } finally {
            this.sincronizandoAgenda = false;
          }
        },

        async cargarFlashcards() {
          try {
            const res = await fetch('/api/flashcards?programa=' + encodeURIComponent(this.programaActivoId));
            this.flashcards = await res.json();
            this.flashcardIndice = 0;
            this.flashcardVolteada = false;
          } catch(e) { console.error(e); }
        },

        filtrarFlashcards() {
          this.flashcardIndice = 0;
          this.flashcardVolteada = false;
        },

        siguienteFlashcard() {
          if (this.flashcardIndice < this.flashcardsFiltradas.length - 1) {
            this.flashcardIndice++;
            this.flashcardVolteada = false;
          } else {
            this.flashcardIndice = 0;
            this.flashcardVolteada = false;
          }
        },

        anteriorFlashcard() {
          if (this.flashcardIndice > 0) {
            this.flashcardIndice--;
            this.flashcardVolteada = false;
          }
        },

        async calificarTarjeta(dificultad) {
          if (!this.tarjetaActual) return;
          try {
            await fetch('/api/flashcards/calificar', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({
                programa: this.programaActivoId,
                tarjeta_id: this.tarjetaActual.id,
                dificultad: dificultad
              })
            });
          } catch(e) {}
          this.siguienteFlashcard();
        },

        abrirModalGenerarCards() {
          this.nuevaCardAsig = this.flashcardFiltroAsig || (this.todasLasAsignaturasPrograma[0] || 'Acceso a Datos');
          this.nuevaCardTema = '';
          this.nuevaCardCantidad = 4;
          this.modalGenerarFlashcards = true;
        },

        async ejecutarGeneracionFlashcards() {
          if (!this.nuevaCardTema.trim() || this.generandoFlashcards) return;
          this.generandoFlashcards = true;
          try {
            const res = await fetch('/api/flashcards/generar', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({
                asignatura: this.nuevaCardAsig || (this.todasLasAsignaturasPrograma[0] || 'Acceso a Datos'),
                programa: this.programaActivoId,
                texto: this.nuevaCardTema.trim(),
                cantidad: this.nuevaCardCantidad || 4
              })
            });
            const data = await res.json();
            if (data.tarjetas && data.tarjetas.length > 0) {
              await this.cargarFlashcards();
              this.flashcardFiltroAsig = this.nuevaCardAsig;
              this.flashcardIndice = 0;
              this.modalGenerarFlashcards = false;
              this.nuevaCardTema = '';
              alert(`¡Se han añadido ${data.tarjetas.length} nuevas flashcards con éxito!`);
            } else {
              alert('No se pudieron generar tarjetas. Intenta con otro término.');
            }
          } catch(e) {
            console.error(e);
            alert('Error al conectar con NOVA.');
          } finally {
            this.generandoFlashcards = false;
          }
        },

        async abrirMinutero(clase) {
          this.minuteroClase = clase;
          this.minuteroFiltro = '';
          this.minuteroCues = [];
          this.modalMinutero = true;
          try {
            const res = await fetch(`/api/clase-cues?asig=${encodeURIComponent(clase.asignatura)}&nombre_archivo=${encodeURIComponent(clase.nombre_archivo)}`);
            this.minuteroCues = await res.json();
          } catch(e) { console.error(e); }
        },

        abrirGrabacionEnMinuto(clase, startSeconds) {
          if (!clase || !clase.recording_url) {
            alert('Esta sesión no tiene un enlace directo a la grabación de vídeo disponible.');
            return;
          }
          const segs = Math.max(0, Math.floor(Number(startSeconds) || 0));
          let url = clase.recording_url;
          if (!url.includes('#t=') && !url.includes('t=')) {
            url += (url.includes('#') ? `&t=${segs}` : `#t=${segs}`);
          }
          window.open(url, '_blank', 'noopener');
        },

        preguntarMinuto(cue, clase) {
          this.modalMinutero = false;
          this.chatAsignatura = clase.asignatura;
          this.tab = 'tutor';
          this.chatInput = `En la sesión ${clase.titulo} (${clase.fecha}), en el minuto [${cue.start_str}], ${cue.speaker} explicó: "${cue.text}". ¿Me lo puedes explicar con más detalle técnico y darme un ejemplo práctico?`;
          this.enviarMensajeChat();
        },

        abrirPdf(ruta, titulo) {
          if (/\\.(docx|md|txt)$/i.test(ruta || '')) {
            this.verContenidoArchivo(ruta);
            return;
          }
          this.modalRutaPdf = ruta;
          this.modalTitulo = titulo;
          this.modalVisorPdf = true;
          this.abrirOPestana({
            id: 'pdf-' + ruta,
            titulo: titulo.replace(/\\.pdf$/i, ''),
            icono: 'fa-file-pdf',
            tipo: 'pdf',
            data: { ruta, titulo },
            url: '/?pdf=' + encodeURIComponent(ruta) + (this.asigSeleccionada ? '&asig=' + encodeURIComponent(this.asigSeleccionada.nombre) : '')
          });
        },

        consultarNovaPdf() {
          this.modalVisorPdf = false;
          this.chatAsignatura = this.asigSeleccionada?.nombre || '';
          this.tab = 'tutor';
          this.chatInput = `Estoy estudiando el documento "${this.modalTitulo}". ¿Cuáles son los puntos clave más importantes de cara al examen y qué trampas o preguntas típicas suelen poner en UNIR sobre este temario?`;
          this.enviarMensajeChat();
        },

        limpiarChat() {
          const asig = this.chatAsignatura;
          const etiqueta = asig ? `la asignatura "${asig}"` : 'el chat global de NOVA';
          if (!confirm(`¿Deseas reiniciar la conversación de ${etiqueta}?\n\nEsta acción solo reiniciará el chat de esta materia. Las guías y chats de las demás asignaturas no se borrarán.`)) {
            return;
          }
          const clave = this.obtenerClaveChat(asig);
          try {
            localStorage.removeItem('unir_chat_' + clave);
          } catch(e) {}
          delete this.chatHistorialesPorAsignatura[clave];

          const bienvenida = [{
            rol: 'model',
            texto: `¡Memoria reiniciada para ${asig || 'NOVA'}! Hola Alex, ¿en qué nos enfocamos ahora?`
          }];
          this.chatHistorialesPorAsignatura[clave] = bienvenida;
          this.chatHistorial = bienvenida;
          this.guardarHistorialActual();
        },

        copiarTexto(texto) {
          if (!texto) return;
          navigator.clipboard.writeText(texto).then(() => {
            alert('¡Contenido copiado al portapapeles!');
          }).catch(() => {
            alert('No se pudo copiar automáticamente.');
          });
        },

        async guardarRespuestaComoGuia(texto) {
          if (!texto) return;
          const asig = this.chatAsignatura || (this.asigSeleccionada ? this.asigSeleccionada.nombre : '');
          if (!asig) {
            alert('Selecciona primero una asignatura en el desplegable superior del chat para poder guardar esta guía en su biblioteca correspondiente.');
            return;
          }
          let tituloSugerido = 'Guia_Clase_' + new Date().toISOString().slice(0, 10);
          const primerH1 = texto.match(/#+\\s+(.+)/);
          if (primerH1 && primerH1[1]) {
            tituloSugerido = primerH1[1].slice(0, 50).trim();
          }
          const tituloFinal = prompt('Introduce el título para guardar esta Guía de Estudio:', tituloSugerido);
          if (!tituloFinal || !tituloFinal.trim()) return;

          try {
            const res = await fetch('/api/guardar-guia-chat', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({
                asignatura: asig,
                titulo: tituloFinal.trim(),
                contenido: texto
              })
            });
            const data = await res.json();
            if (res.ok) {
              await this.cargarCatalogo();
              await this.cargarProgreso();
              if (this.asigSeleccionada && this.asigSeleccionada.nombre === asig) {
                this.asigSeleccionada = this.catalogoTotal.find(a => a.nombre === asig) || this.asigSeleccionada;
              }
              alert(`¡Guía guardada con éxito en ${asig}!\nArchivo: ${data.nombre}`);
            } else {
              alert(data.detail || 'Error al guardar la guía.');
            }
          } catch(e) {
            console.error(e);
            alert('Error de conexión al guardar la guía.');
          }
        },

        renderMarkdown(texto) {
          const rawHtml = marked.parse(texto || '');
          return window.DOMPurify ? window.DOMPurify.sanitize(rawHtml) : rawHtml;
        },

        async enviarMensajeChat() {
          if (!this.chatInput.trim() || this.chatCargando) return;
          const texto = this.chatInput.trim();
          const asigOrigen = this.chatAsignatura;
          const programaOrigen = this.programaActivoId;
          const claveOrigen = this.obtenerClaveChat(asigOrigen);

          // Capturar historial previo antes de registrar el nuevo mensaje en pantalla
          const historialPrevio = this.chatHistorial.map(m => ({
            rol: m.rol,
            texto: m.texto
          })).slice(-8);

          this.chatHistorial.push({ rol: 'user', texto });
          this.guardarHistorialActual();
          this.chatInput = '';
          this.chatCargando = true;
          this.$nextTick(() => {
            const el = document.getElementById('chatBox');
            if (el) el.scrollTop = el.scrollHeight;
          });

          const listaInicial = Array.isArray(this.chatHistorialesPorAsignatura[claveOrigen])
            ? [...this.chatHistorialesPorAsignatura[claveOrigen]]
            : [...this.chatHistorial];
          const indiceMensajeModelo = listaInicial.length;
          listaInicial.push({ rol: 'model', texto: '' });
          this.chatHistorialesPorAsignatura[claveOrigen] = listaInicial;

          const actualizarMensajeEnOrigen = (textoParcial, persistir = false) => {
            const listaOrigen = Array.isArray(this.chatHistorialesPorAsignatura[claveOrigen])
              ? [...this.chatHistorialesPorAsignatura[claveOrigen]]
              : [...this.chatHistorial];
            if (listaOrigen[indiceMensajeModelo]) {
              listaOrigen[indiceMensajeModelo] = { rol: 'model', texto: textoParcial };
            } else {
              listaOrigen.push({ rol: 'model', texto: textoParcial });
            }
            this.chatHistorialesPorAsignatura[claveOrigen] = listaOrigen;
            if (persistir) {
              try {
                localStorage.setItem('unir_chat_' + claveOrigen, JSON.stringify(listaOrigen.slice(-40)));
              } catch (err) {}
            }
            if (this.obtenerClaveChat(this.chatAsignatura) === claveOrigen) {
              this.chatHistorial = [...listaOrigen];
              this.$nextTick(() => {
                const el = document.getElementById('chatBox');
                if (el) el.scrollTop = el.scrollHeight;
              });
            }
          };

          try {
            const res = await fetch('/api/chat-stream', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({
                mensaje: texto,
                asignatura: asigOrigen,
                programa: programaOrigen,
                historial: historialPrevio,
                modelo: this.chatModeloSeleccionado || 'auto'
              })
            });

            if (!res.ok || !res.body) {
              throw new Error('Streaming no disponible');
            }

            const reader = res.body.getReader();
            const decoder = new TextDecoder('utf-8');
            let buffer = '';
            let acumulado = '';
            let primerChunkRecibido = false;

            while (true) {
              const { value, done } = await reader.read();
              if (done) break;
              buffer += decoder.decode(value, { stream: true });
              const eventos = buffer.split('\\n\\n');
              buffer = eventos.pop() || '';

              for (const ev of eventos) {
                const linea = ev.trim();
                if (!linea.startsWith('data:')) continue;
                const jsonStr = linea.slice(5).trim();
                if (!jsonStr) continue;
                try {
                  const payload = JSON.parse(jsonStr);
                  if (payload.modelo_nombre) {
                    this.chatModeloActualNombre = payload.modelo_nombre;
                  }
                  if (payload.chunk) {
                    if (!primerChunkRecibido) {
                      primerChunkRecibido = true;
                      this.chatCargando = false;
                    }
                    acumulado += payload.chunk;
                    actualizarMensajeEnOrigen(acumulado, false);
                  }
                  if (payload.done && acumulado.trim()) {
                    actualizarMensajeEnOrigen(acumulado, true);
                  }
                } catch (parseErr) {}
              }
            }
            if (!acumulado.trim()) {
              throw new Error('Stream vacío');
            }
            actualizarMensajeEnOrigen(acumulado, true);
          } catch(e) {
            try {
              const resFallback = await fetch('/api/chat', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                  mensaje: texto,
                  asignatura: asigOrigen,
                  programa: programaOrigen,
                  historial: historialPrevio,
                  modelo: this.chatModeloSeleccionado || 'auto'
                })
              });
              const data = await resFallback.json();
              if (data.modelo_nombre) {
                this.chatModeloActualNombre = data.modelo_nombre;
              }
              actualizarMensajeEnOrigen(data.respuesta || 'Sin respuesta de NOVA.', true);
            } catch (errFallback) {
              actualizarMensajeEnOrigen('Error al contactar con NOVA.', true);
            }
          } finally {
            this.chatCargando = false;
            this.cargarModelosIa();
            this.$nextTick(() => {
              const el = document.getElementById('chatBox');
              if (el) el.scrollTop = el.scrollHeight;
            });
          }
        }
      }
    }
  </script>
</body>
</html>"""


def iniciar_servidor_web(host: str = "0.0.0.0", port: int = 8080):
    """Lanza el servidor web de FastAPI accesible en local y con token seguro en la red local o nube."""
    from src.core.cloud_sync import descargar_datos_desde_hf_si_vacio
    descargar_datos_desde_hf_si_vacio()
    port = int(os.getenv("PORT", str(port)))
    ip_local = obtener_ip_red_local()
    console.print(f"\n[bold green]🚀 Iniciando Interfaz Gráfica Web de UNIR Study Hub (Multi-Titulación)...[/bold green]")
    console.print(f"[bold cyan]💻 En tu ordenador (acceso directo):[/bold cyan]      [bold white underline]http://localhost:{port}[/bold white underline]")
    console.print(f"[bold yellow]📱 En tu móvil / Nube (con token seguro):[/bold yellow] [bold white underline]http://{ip_local}:{port}/?token={HUB_ACCESS_TOKEN}[/bold white underline]\n")
    uvicorn.run(app, host=host, port=port, log_level="warning")


if __name__ == "__main__":
    iniciar_servidor_web()
