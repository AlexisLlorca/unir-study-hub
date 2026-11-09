"""
Servidor Web y Dashboard Gráfico Interactivo para NOVA Study Hub.
Plataforma Académica Multi-Titulación 100% Web:
  - FP Grado Superior DAM (1º y 2º Curso)
  - Máster en Ciberseguridad (Módulos Oficiales M1 a M7)
  - PFU / Grado en Ingeniería del Software (UNIR)
  - Certificaciones Técnicas & Formación Continua
Permite seleccionar programas, explorar materias, leer temarios y transcripciones,
generar guías y tests con IA y realizar simulacros tipo test directamente desde la web.
"""

from __future__ import annotations

import os
import re
import json
import socket
import asyncio
import time
from pathlib import Path
from typing import Dict, Any, Optional, List, Tuple

import uvicorn
from fastapi import FastAPI, Request, HTTPException, Response
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse, StreamingResponse, RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
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
    normalizar_nombre_asignatura,
)
from src.core.progress_tracker import GestorProgreso
from src.core.config import RESPALDO_DIR, get_hub_access_token
from src.ai.tutor import TutorIA
from src.generators.study_guide_generator import GeneradorGuiaEstudio
from src.core.auth import (
    inicializar_bd_usuarios,
    autenticar_usuario,
    registrar_usuario,
    actualizar_api_key_usuario,
    crear_sesion,
    eliminar_sesion,
    listar_usuarios,
    cambiar_estado_usuario,
    cambiar_rol_usuario,
    eliminar_usuario,
    contar_pendientes,
)
from src.core.feedback import (
    inicializar_bd_feedback,
    crear_reporte,
    listar_reportes,
    actualizar_estado_reporte,
    eliminar_reporte,
    contar_feedback_pendientes,
)
from src.ui.security import (
    AccesoSeguroMiddleware,
    COOKIE_TOKEN,
    _obtener_ip_cliente,
    _esta_ip_bloqueada,
    _registrar_intento_fallido,
    _limpiar_intentos_exitosos,
    nombre_seguro,
    obtener_ip_red_local,
    ruta_dentro_de,
    ruta_segura_lectura,
)

app = FastAPI(title="NOVA Study Hub — Plataforma Inteligente Multi-Titulación", version="3.3.0")

# Sin CORS: el frontend se sirve desde este mismo servidor, así que ninguna web externa
# necesita (ni debe poder) leer las respuestas de la API.
HUB_ACCESS_TOKEN = get_hub_access_token()
IP_LOCAL = obtener_ip_red_local()
app.add_middleware(AccesoSeguroMiddleware, token=HUB_ACCESS_TOKEN, ip_local=IP_LOCAL)
app.add_middleware(GZipMiddleware, minimum_size=1000)

inicializar_bd_usuarios()
inicializar_bd_feedback()

tracker = GestorProgreso()
tutor_ia = TutorIA()
generador = GeneradorGuiaEstudio(tutor_ia)

# Programador nocturno automático: sincroniza notas, entregas y PDFs nuevos de cada estudiante
try:
    from src.crawler.student_canvas import iniciar_programador_sincronizacion
    iniciar_programador_sincronizacion()
except Exception:
    pass


# --- MODELOS AUTH Y ADMIN ---

class LoginRequest(BaseModel):
    username: str
    password: str

class RegistroRequest(BaseModel):
    nombre: str
    username: str
    password: str
    api_key: Optional[str] = None

class CambiarEstadoRequest(BaseModel):
    user_id: int
    activo: int

class CambiarRolRequest(BaseModel):
    user_id: int
    rol: str

class ActualizarKeyRequest(BaseModel):
    api_key: str

class CrearFeedbackRequest(BaseModel):
    tipo: str = "fallo"  # 'fallo' o 'mejora'
    titulo: str
    descripcion: str
    asignatura: Optional[str] = ""

class ActualizarFeedbackRequest(BaseModel):
    feedback_id: int
    estado: str  # 'pendiente', 'en_proceso', 'resuelto', 'descartado'
    respuesta_admin: Optional[str] = None

def _verificar_es_admin(request: Request):
    u = getattr(request.state, "usuario", None)
    if not u or u.get("rol") != "admin":
        raise HTTPException(status_code=403, detail="Acceso denegado: se requieren permisos de administrador.")

def _obtener_user_key_o_exigir(request: Request, key_override: Optional[str] = None) -> Optional[str]:
    """
    Obtiene la clave de Gemini del usuario:
    1. Si se pasa en key_override (payload JSON)
    2. Si se pasa en cabecera 'x-user-gemini-key'
    3. Si está en el perfil de sesión del usuario (request.state.usuario)
    
    Si el usuario no es admin y no dispone de ninguna clave propia (Gemini, OpenRouter o Groq):
    Lanza HTTPException(400) exigiendo BYOK para proteger la cuota personal del administrador.
    
    Si el usuario es admin o configuró algún proveedor, retorna la clave de Gemini o None.
    """
    u = getattr(request.state, "usuario", None) or {}
    es_admin = u.get("rol") == "admin"
    user_key = (key_override or request.headers.get("x-user-gemini-key") or u.get("gemini_api_key") or "").strip() or None
    tiene_otra_clave = bool(
        (u.get("openrouter_api_key") or "").strip()
        or (u.get("groq_api_key") or "").strip()
        or request.headers.get("x-user-openrouter-key")
        or request.headers.get("x-user-groq-key")
    )
    if not es_admin and not user_key and not tiene_otra_clave:
        raise HTTPException(
            status_code=400,
            detail="Es obligatorio configurar tu propia clave gratuita de IA (Gemini, OpenRouter o Groq). Pulsa en 'Claves IA' arriba para introducirla.",
        )
    return user_key


# --- API ENDPOINTS ---

@app.get("/ping")
@app.get("/health")
def api_health_check():
    """Endpoint público y ultraligero para monitorización y pinger keep-alive 24/7."""
    return {"status": "ok", "app": "unir-study-hub", "uptime": "active"}


@app.post("/api/auth/login")
def api_auth_login(req: LoginRequest, request: Request, response: Response):
    ip = _obtener_ip_cliente(request)
    bloqueada, restantes = _esta_ip_bloqueada(ip)
    if bloqueada:
        minutos = max(1, restantes // 60)
        raise HTTPException(status_code=429, detail=f"Demasiados intentos fallidos. IP bloqueada durante {minutos} min.")

    usuario, msg = autenticar_usuario(req.username, req.password)
    if not usuario:
        bloqueada_ahora, duracion = _registrar_intento_fallido(ip)
        if bloqueada_ahora:
            raise HTTPException(status_code=429, detail="Demasiados intentos fallidos. Acceso bloqueado durante 5 minutos.")
        raise HTTPException(status_code=401, detail=msg)

    _limpiar_intentos_exitosos(ip)
    token = crear_sesion(usuario["username"])
    es_https = request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"
    response.set_cookie(
        COOKIE_TOKEN,
        token,
        max_age=60 * 60 * 24 * 30,
        httponly=True,
        secure=es_https,
        samesite="lax",
    )
    return {"status": "ok", "usuario": usuario}


@app.post("/api/auth/registro")
def api_auth_registro(req: RegistroRequest):
    key = (req.api_key or "").strip()
    if not key:
        raise HTTPException(
            status_code=400,
            detail="Es obligatorio indicar tu clave gratuita de Google AI Studio (Gemini) para crear una cuenta de estudiante.",
        )
    ok, msg = registrar_usuario(req.username, req.nombre, req.password, rol="estudiante", activo=0, gemini_api_key=key)
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    return {"status": "ok", "message": "Cuenta registrada con éxito. Pendiente de activación por el administrador."}


@app.post("/api/usuario/guardar-key")
def api_usuario_guardar_key(req: ActualizarKeyRequest, request: Request):
    u = getattr(request.state, "usuario", None)
    if not u or not u.get("username"):
        raise HTTPException(status_code=401, detail="No autenticado")
    actualizar_api_key_usuario(u["username"], req.api_key)
    return {"status": "ok"}


@app.post("/api/auth/logout")
def api_auth_logout(request: Request, response: Response):
    token = request.cookies.get(COOKIE_TOKEN)
    if token:
        eliminar_sesion(token)
    response.delete_cookie(COOKIE_TOKEN)
    return {"status": "ok"}


@app.get("/logout")
def ruta_logout_directo(request: Request):
    token = request.cookies.get(COOKIE_TOKEN)
    if token:
        eliminar_sesion(token)
    resp = RedirectResponse(url="/", status_code=303)
    resp.delete_cookie(COOKIE_TOKEN)
    return resp


@app.get("/api/usuario/actual")
def api_usuario_actual(request: Request):
    u = getattr(request.state, "usuario", None)
    if not u:
        return {"autenticado": False, "es_admin": False}
    return {
        "autenticado": True,
        "id": u.get("id"),
        "username": u.get("username"),
        "nombre": u.get("nombre"),
        "rol": u.get("rol"),
        "activo": u.get("activo"),
        "es_admin": u.get("rol") == "admin",
        "gemini_api_key": u.get("gemini_api_key", ""),
        "openrouter_api_key": u.get("openrouter_api_key", ""),
        "groq_api_key": u.get("groq_api_key", ""),
    }


@app.get("/api/admin/usuarios")
def api_admin_usuarios(request: Request):
    _verificar_es_admin(request)
    return {
        "usuarios": listar_usuarios(),
        "pendientes": contar_pendientes()
    }


@app.post("/api/admin/usuario/estado")
def api_admin_usuario_estado(req: CambiarEstadoRequest, request: Request):
    _verificar_es_admin(request)
    cambiar_estado_usuario(req.user_id, req.activo)
    return {"status": "ok"}


@app.post("/api/admin/usuario/rol")
def api_admin_usuario_rol(req: CambiarRolRequest, request: Request):
    _verificar_es_admin(request)
    cambiar_rol_usuario(req.user_id, req.rol)
    return {"status": "ok"}


@app.delete("/api/admin/usuario/{user_id}")
def api_admin_usuario_eliminar(user_id: int, request: Request):
    _verificar_es_admin(request)
    ok = eliminar_usuario(user_id)
    if not ok:
        raise HTTPException(status_code=400, detail="No se puede eliminar la cuenta del administrador principal.")
    return {"status": "ok"}


# --- API FEEDBACK / INCIDENCIAS Y MEJORAS ---

@app.get("/api/feedback")
def api_feedback_listar():
    """Retorna la lista de incidencias y propuestas de mejora con contador de pendientes."""
    return {
        "reportes": listar_reportes(),
        "pendientes": contar_feedback_pendientes(),
    }


@app.post("/api/feedback")
def api_feedback_crear(req: CrearFeedbackRequest, request: Request):
    """Crea una nueva incidencia o propuesta de mejora enviada por un alumno o admin."""
    u = getattr(request.state, "usuario", None) or {}
    username = u.get("username") or "anonimo"
    nombre = u.get("nombre") or username
    try:
        rep = crear_reporte(
            username=username,
            nombre=nombre,
            tipo=req.tipo,
            titulo=req.titulo,
            descripcion=req.descripcion,
            asignatura=req.asignatura or "",
        )
        return {"status": "ok", "reporte": rep}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/admin/feedback/estado")
def api_admin_feedback_estado(req: ActualizarFeedbackRequest, request: Request):
    """Permite al administrador cambiar el estado de un reporte y añadir respuesta."""
    _verificar_es_admin(request)
    try:
        ok = actualizar_estado_reporte(req.feedback_id, req.estado, req.respuesta_admin)
        if not ok:
            raise HTTPException(status_code=404, detail="Reporte no encontrado")
        return {"status": "ok"}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.delete("/api/admin/feedback/{feedback_id}")
def api_admin_feedback_eliminar(feedback_id: int, request: Request):
    """Permite al administrador eliminar un reporte."""
    _verificar_es_admin(request)
    ok = eliminar_reporte(feedback_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Reporte no encontrado")
    return {"status": "ok"}


def _username_actual(request: Request) -> str:
    """Devuelve el nombre de usuario de la cuenta autenticada (o 'alexking44' en modo local/admin)."""
    u = getattr(request.state, "usuario", None)
    username = (u.get("username") if isinstance(u, dict) else None) or "alexking44"
    return "alexking44" if username in ("local", "admin") else username


def _rutas_usuario_request(request: Request) -> dict:
    """Devuelve las rutas de guías, tests, resúmenes, progreso y flashcards privadas del usuario actual."""
    from src.core.config import obtener_rutas_usuario
    return obtener_rutas_usuario(_username_actual(request))


def _tracker_usuario(request: Request) -> GestorProgreso:
    """Devuelve el gestor de progreso y calificaciones privado del usuario actual."""
    return GestorProgreso(username=_username_actual(request))


# --- API ENDPOINTS ---

@app.get("/api/programas")
def api_programas(request: Request):
    """Retorna la lista de programas académicos con todas sus métricas y cursos."""
    rutas = _rutas_usuario_request(request)
    return obtener_resumen_programas(examenes_dir=rutas["examenes"])


def _orden_natural_items(texto: Any) -> tuple:
    """Ordenamiento natural de menor a mayor (Tema 1, Tema 2, ..., Tema 10)."""
    s = str(getattr(texto, "name", texto)).replace("_", " ").strip()
    m = re.search(r"(?:tema|unit|m[óo]dulo)\s*[\._-]?\s*(\d+)", s, re.IGNORECASE)
    if m:
        return (0, int(m.group(1)), s.lower())
    m_num = re.search(r"\d+", s)
    if m_num:
        return (1, int(m_num.group(0)), s.lower())
    return (2, 999, s.lower())


_CACHE_CATALOGO: Dict[str, tuple[float, List[Dict[str, Any]]]] = {}


@app.get("/api/catalogo")
def api_catalogo(request: Request, programa: Optional[str] = None, curso: Optional[str] = None):
    """Retorna el catálogo consolidado de asignaturas filtrado por programa, curso y espacio privado del usuario."""
    username = _username_actual(request)
    cache_key = f"{username}:{programa or ''}:{curso or ''}"
    cached = _CACHE_CATALOGO.get(cache_key)
    if cached and (time.time() - cached[0] < 15):
        return cached[1]

    catalogo = obtener_catalogo_academico(programa_id=programa, curso_id=curso)
    rutas = _rutas_usuario_request(request)
    tracker_u = _tracker_usuario(request)
    resultado = []
    asigs_vistas = set()

    for asig, info in catalogo.items():
        asigs_vistas.add(asig)
        dir_guias = rutas["guias"] / asig
        dir_examenes = rutas["examenes"] / asig
        dir_mat_u = rutas["materiales"] / asig

        guias = sorted([f.name for f in dir_guias.glob("*.md")], key=_orden_natural_items) if dir_guias.exists() else []
        tests = sorted([f.stem for f in dir_examenes.glob("*.json")], key=_orden_natural_items) if dir_examenes.exists() else []

        mats_base = [f.name for f in info["materiales"]]
        mats_privados = [f.name for f in dir_mat_u.glob("*.pdf")] if dir_mat_u.exists() else []
        mats_combinados = sorted(list(set(mats_base + mats_privados)), key=_orden_natural_items)

        resumen_progreso = tracker_u.obtener_resumen_asignatura(asig)

        resultado.append({
            "nombre": asig,
            "programa_id": info["programa_id"],
            "curso": info["curso"],
            "total_materiales": len(mats_combinados),
            "materiales": mats_combinados,
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

    # Añadir asignaturas exclusivas sincronizadas por este alumno en su carpeta privada
    if rutas["materiales"].exists() and (not programa or programa == "dam"):
        for d_priv in sorted(rutas["materiales"].iterdir()):
            if not d_priv.is_dir() or d_priv.name in asigs_vistas:
                continue
            asig = d_priv.name
            pdfs_priv = sorted([f.name for f in d_priv.glob("*.pdf")], key=_orden_natural_items)
            if not pdfs_priv:
                continue
            dir_guias = rutas["guias"] / asig
            dir_examenes = rutas["examenes"] / asig
            guias = sorted([f.name for f in dir_guias.glob("*.md")], key=_orden_natural_items) if dir_guias.exists() else []
            tests = sorted([f.stem for f in dir_examenes.glob("*.json")], key=_orden_natural_items) if dir_examenes.exists() else []
            resultado.append({
                "nombre": asig,
                "programa_id": programa or "dam",
                "curso": curso or "2º Curso",
                "total_materiales": len(pdfs_priv),
                "materiales": pdfs_priv,
                "total_clases": 0,
                "clases": [],
                "guias": guias,
                "tests": tests,
                "progreso": tracker_u.obtener_resumen_asignatura(asig),
            })

    _CACHE_CATALOGO[cache_key] = (time.time(), resultado)
    return resultado


@app.get("/api/progreso")
def api_progreso(request: Request):
    """Retorna el estado del expediente, banco de fallos y estadísticas globales privadas del usuario."""
    tracker_u = _tracker_usuario(request)
    datos = tracker_u._cargar()
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
def api_preguntas_falladas(request: Request, asig: Optional[str] = None, asignatura: Optional[str] = None):
    """Devuelve las preguntas pendientes en el banco de fallos privado del usuario para el Modo Repaso de Fallos."""
    asig_raw = asig or asignatura
    asig_limpia = nombre_seguro(asig_raw, "asignatura") if asig_raw else None
    return _tracker_usuario(request).obtener_preguntas_falladas(asignatura=asig_limpia)


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
def api_contenido(ruta: str, request: Request):
    """Devuelve el contenido en texto de cualquier guía (del espacio privado del usuario), transcripción o .docx."""
    ruta_normalizada = (ruta or "").strip().replace("\\", "/").lstrip("/")
    rutas_u = _rutas_usuario_request(request)
    if ruta_normalizada.startswith("data/procesado/guias/"):
        rel_guia = ruta_normalizada[len("data/procesado/guias/"):]
        ruta_personal = rutas_u["guias"] / rel_guia
        ruta = str(ruta_personal.relative_to(BASE_DIR))
    elif ruta_normalizada.startswith("data/procesado/resumenes_clases/"):
        rel_res = ruta_normalizada[len("data/procesado/resumenes_clases/"):]
        ruta_personal = rutas_u["resumenes_clases"] / rel_res
        ruta = str(ruta_personal.relative_to(BASE_DIR))

    archivo = ruta_segura_lectura(ruta, extensiones={".md", ".txt", ".json", ".docx"})
    if archivo.suffix.lower() == ".docx":
        from src.ai.materials_search import extraer_texto_docx
        parrafos = [p.strip() for p in extraer_texto_docx(archivo).splitlines() if p.strip()]
        contenido = f"# 📄 {archivo.name}\n\n" + "\n\n".join(parrafos) if parrafos else "*(Documento Word sin texto extraíble)*"
        return {"contenido": contenido, "nombre": archivo.name}
    with open(archivo, "r", encoding="utf-8") as f:
        return {"contenido": f.read(), "nombre": archivo.name}


@app.get("/api/archivo-raw")
def api_archivo_raw(ruta: str, request: Request):
    """Sirve archivos directamente (como PDFs, DOCX o imágenes) para visores integrados, validando la ruta y el espacio privado del usuario."""
    ruta_norm = (ruta or "").strip().replace("\\", "/").lstrip("/")
    if ruta_norm.startswith("data/materiales/"):
        rel_mat = ruta_norm[len("data/materiales/"):]
        rutas_u = _rutas_usuario_request(request)
        cand_priv = rutas_u["materiales"] / rel_mat
        if cand_priv.exists() and cand_priv.is_file():
            ruta = str(cand_priv.relative_to(BASE_DIR))

    archivo = ruta_segura_lectura(
        ruta,
        extensiones={".pdf", ".docx", ".png", ".jpg", ".jpeg", ".webp", ".svg", ".md", ".txt", ".mp3", ".mp4", ".wav", ".m4a"},
    )
    media_type = "application/pdf" if archivo.suffix.lower() == ".pdf" else None
    resp = FileResponse(
        archivo,
        media_type=media_type,
        filename=archivo.name,
        content_disposition_type="inline",
    )
    resp.headers["X-Frame-Options"] = "SAMEORIGIN"
    return resp


@app.get("/api/test/{asignatura}/{test_name}")
def api_obtener_test(asignatura: str, test_name: str, request: Request):
    """Devuelve las preguntas de un test en JSON del espacio privado del usuario de forma tolerante."""
    import unicodedata
    rutas_u = _rutas_usuario_request(request)
    dir_examenes_u = rutas_u["examenes"]
    asig_limpia = nombre_seguro(asignatura, "asignatura")
    stem = nombre_seguro(test_name.replace(".json", ""), "test")
    archivo = ruta_dentro_de(dir_examenes_u, asig_limpia, f"{stem}.json")
    try:
        from src.core.cloud_sync import asegurar_archivo_local
        archivo = asegurar_archivo_local(archivo)
    except Exception:
        pass
    
    if not archivo.exists() or not archivo.is_file() or archivo.stat().st_size == 0:
        # Fallback tolerante con normalización de acentos y caracteres
        def norm(s):
            return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower().replace("_", " ").strip()
        
        dir_asig = dir_examenes_u / asig_limpia
        if dir_asig.exists():
            stem_norm = norm(stem)
            for f in dir_asig.glob("*.json"):
                if norm(f.stem) == stem_norm:
                    archivo = f
                    break

    if not archivo.exists() or not archivo.is_file() or archivo.stat().st_size == 0:
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
def api_guardar_test(sub: TestSubmission, request: Request):
    """Registra la calificación de un test realizado en la web en el expediente privado del usuario."""
    _tracker_usuario(request).registrar_resultado_test(
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
def api_eliminar_guia(asignatura: str, guia_name: str, request: Request):
    """Elimina una guía de estudio del espacio privado del usuario tanto localmente como en la nube."""
    rutas_u = _rutas_usuario_request(request)
    asig_limpia = nombre_seguro(asignatura, "asignatura")
    guia_limpia = nombre_seguro(guia_name, "guía")
    guia_path = ruta_dentro_de(rutas_u["guias"], asig_limpia, guia_limpia)
    if not guia_path.exists() or not guia_path.is_file():
        raise HTTPException(status_code=404, detail="Guía no encontrada")

    try:
        from src.core.cloud_sync import eliminar_archivo_en_hf_async
        eliminar_archivo_en_hf_async(guia_path)
        guia_path.unlink()
        return {"ok": True, "mensaje": f"Guía {guia_limpia} eliminada correctamente"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error al eliminar la guía: {str(e)}")


@app.delete("/api/test/{asignatura}/{test_name}")
def api_eliminar_test(asignatura: str, test_name: str, request: Request):
    """Elimina un simulacro de test del espacio privado del usuario tanto localmente como en la nube."""
    rutas_u = _rutas_usuario_request(request)
    asig_limpia = nombre_seguro(asignatura, "asignatura")
    stem = nombre_seguro(test_name.replace(".json", "").replace(".md", ""), "test")
    f_json = ruta_dentro_de(rutas_u["examenes"], asig_limpia, f"{stem}.json")
    f_md = ruta_dentro_de(rutas_u["examenes"], asig_limpia, f"{stem}.md")

    eliminado = False
    try:
        from src.core.cloud_sync import eliminar_archivo_en_hf_async
        if f_json.exists() and f_json.is_file():
            eliminar_archivo_en_hf_async(f_json)
            f_json.unlink()
            eliminado = True
        if f_md.exists() and f_md.is_file():
            eliminar_archivo_en_hf_async(f_md)
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
    profundidad: Optional[str] = "robusta"
    num_preguntas: Optional[int] = 15
    tipo_generacion: Optional[str] = "ambos"
    api_key: Optional[str] = None


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
def api_generar_guia(req: GenerarGuiaRequest, request: Request):
    """Genera una guía de estudio y/o test interactivo con IA desde la web."""
    user_key = _obtener_user_key_o_exigir(request, req.api_key)
    asig_limpia = nombre_seguro(req.asignatura, "asignatura")
    rutas_u = _rutas_usuario_request(request)
    ruta_pdf = None
    if req.archivo_pdf:
        pdf_limpio = nombre_seguro(req.archivo_pdf, "archivo_pdf")
        p_priv = ruta_dentro_de(rutas_u["materiales"], asig_limpia, pdf_limpio)
        p_base = ruta_dentro_de(MATERIALES_DIR, asig_limpia, pdf_limpio)
        if p_priv.exists() and p_priv.is_file():
            ruta_pdf = p_priv
        elif p_base.exists() and p_base.is_file():
            ruta_pdf = p_base

    if not ruta_pdf:
        ruta_pdf = _buscar_pdf_relevante(rutas_u["materiales"] / asig_limpia, req.nombre_tema) or _buscar_pdf_relevante(MATERIALES_DIR / asig_limpia, req.nombre_tema)

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
        tipo_generacion=req.tipo_generacion or "ambos",
        api_key_override=user_key,
        guias_dir=rutas_u["guias"],
        examenes_dir=rutas_u["examenes"],
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
def api_guardar_guia_chat(req: GuardarGuiaChatRequest, request: Request):
    """Guarda una respuesta o guía generada en el chat de NOVA directamente como guía de estudio en el espacio privado del usuario."""
    if not req.asignatura or not req.contenido.strip():
        raise HTTPException(status_code=400, detail="Faltan datos de asignatura o contenido")

    rutas_u = _rutas_usuario_request(request)
    slug_asig = nombre_seguro(req.asignatura, "asignatura")
    slug_titulo = re.sub(r'[\\/*?:"<>|.]', "", req.titulo).strip().replace(" ", "_")
    if not slug_titulo:
        slug_titulo = "Guia_Clases_NOVA"
    if not slug_titulo.lower().endswith("_guia"):
        slug_titulo = f"{slug_titulo}_guia"
    slug_titulo = nombre_seguro(slug_titulo, "título")

    dir_guia = ruta_dentro_de(rutas_u["guias"], slug_asig)
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
    """Resincroniza carpetas externas de estudio o actualiza desde la nube si está en Render."""
    from src.core.cloud_sync import descargar_datos_desde_hf_si_vacio
    if os.getenv("RENDER"):
        descargar_datos_desde_hf_si_vacio(forzar=True)
    reorganizar_biblioteca_completa()
    return {"status": "ok"}


@app.post("/api/sincronizar-respaldo")
@app.post("/api/escanear-primer-ano")
def api_sincronizar_respaldo():
    """Escanea el respaldo local en PC o sincroniza desde la Nube Privada cuando se ejecuta en Render."""
    from src.core.cloud_sync import descargar_datos_desde_hf_si_vacio, crear_y_subir_bundle_rapido
    if os.getenv("RENDER"):
        ok = descargar_datos_desde_hf_si_vacio(forzar=True)
        return {
            "status": "ok" if ok else "error",
            "mensaje": "☁️ Biblioteca sincronizada desde tu Nube Privada (temarios de 1º/2º DAM, Ciberseguridad y clases actualizados)."
        }

    try:
        from src.crawler.campus_scraper import CampusScraper
        scraper = CampusScraper()
        scraper.sincronizar_desde_respaldo_local()
    except Exception:
        pass
    sincronizar_estudios_externos()
    reorganizar_biblioteca_completa()
    import threading
    threading.Thread(target=crear_y_subir_bundle_rapido, daemon=True).start()
    return {
        "status": "ok",
        "mensaje": "Respaldo local sincronizado con éxito y respaldado en tu nube."
    }


@app.post("/api/descargar-campus")
def api_descargar_campus():
    """
    En PC local: conecta con Canvas UNIR, descarga nuevas clases/materiales y los sube automáticamente a la nube.
    En Render Cloud: descarga al instante las últimas clases, materiales y guías subidas a tu Nube Privada.
    """
    from src.core.cloud_sync import descargar_datos_desde_hf_si_vacio, subir_datos_a_hf

    # Si estamos en el servidor cloud (Render) o no está Playwright instalado, sincronizar desde Hugging Face Cloud
    es_cloud = bool(os.getenv("RENDER"))
    if not es_cloud:
        try:
            import playwright  # noqa: F401
        except ImportError:
            es_cloud = True

    if es_cloud:
        ok = descargar_datos_desde_hf_si_vacio(forzar=True)
        return {
            "status": "ok" if ok else "error",
            "nuevos_archivos": 0,
            "mensaje": "☁️ Biblioteca en la nube actualizada al 100% con tus últimas clases grabadas, temarios, guías y tests."
        }

    from src.crawler.campus_scraper import CampusScraper
    scraper = CampusScraper()
    nuevos = scraper.ejecutar_sincronizacion_completa()
    reorganizar_biblioteca_completa()

    # Subir en segundo plano a Hugging Face Cloud para que Render tenga los nuevos materiales y clases al instante
    import threading
    threading.Thread(target=subir_datos_a_hf, daemon=True).start()

    return {
        "status": "ok",
        "nuevos_archivos": nuevos,
        "mensaje": f"Sincronización 360° completada ({nuevos} recursos nuevos) y subida automática a la nube iniciada."
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
    contexto_str = "\n[ACCESO A VIDEOCLASES Y TRANSCRIPCIONES OFICIALES DISPONIBLES EN NOVA STUDY HUB]:\n"

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


_CACHE_VIDEOCLASES: Dict[str, tuple[float, List[Dict[str, Any]]]] = {}


@app.get("/api/videoclases")
def api_videoclases(request: Request, programa: Optional[str] = None):
    """Devuelve el catálogo de todas las clases grabadas con sus metadatos y resúmenes del usuario."""
    username = _username_actual(request)
    cache_key = f"{username}:{programa or ''}"
    cached = _CACHE_VIDEOCLASES.get(cache_key)
    if cached and (time.time() - cached[0] < 30):
        return cached[1]

    if not TRANSCRIPCIONES_DIR.exists():
        return []

    rutas_u = _rutas_usuario_request(request)
    dir_resumenes_u = rutas_u["resumenes_clases"]
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
            resumen_file = dir_resumenes_u / asig / f"{f_md.stem}_resumen.md"

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
    _CACHE_VIDEOCLASES[cache_key] = (time.time(), clases)
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
    api_key: Optional[str] = None


@app.post("/api/resumen-clase")
def api_resumen_clase(req: ResumenClaseRequest, request: Request):
    """Genera o recupera de caché del usuario un informe exhaustivo de una videoclase oficial."""
    user_key = _obtener_user_key_o_exigir(request, req.api_key)
    rutas_u = _rutas_usuario_request(request)
    asig_limpia = nombre_seguro(req.asignatura, "asignatura")
    stem = nombre_seguro(
        Path(req.nombre_archivo).stem.replace("_cues", "").replace("_info", "").replace("_resumen", ""),
        "nombre_archivo",
    )

    asig_dir_cache = ruta_dentro_de(rutas_u["resumenes_clases"], asig_limpia)
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
        nombre_clase=nombre_clase,
        api_key_override=user_key,
    )

    if not resumen:
        raise HTTPException(status_code=500, detail="No se pudo generar el resumen con la IA.")

    cache_file.write_text(resumen, encoding="utf-8")
    try:
        from src.core.cloud_sync import respaldar_archivo_en_hf_async
        respaldar_archivo_en_hf_async(cache_file)
    except Exception:
        pass

    return {
        "resumen": resumen,
        "cached": False,
        "archivo": str(cache_file.relative_to(BASE_DIR))
    }


class ConectarMiCampusReq(BaseModel):
    canvas_token: Optional[str] = None
    ics_url: Optional[str] = None
    base_url: Optional[str] = None
    token_expira: Optional[str] = None


class FavoritasReq(BaseModel):
    asignaturas: List[str]


@app.get("/api/mi-campus")
def api_obtener_mi_campus(request: Request):
    """Devuelve el estado de conexión al Campus UNIR (Canvas) personal del estudiante y sus datos sincronizados."""
    from src.crawler.student_canvas import obtener_datos_canvas_estudiante
    return obtener_datos_canvas_estudiante(_username_actual(request))


@app.post("/api/mi-campus/conectar")
def api_conectar_mi_campus(req: ConectarMiCampusReq, request: Request):
    """Conecta o resincroniza la cuenta personal de Canvas UNIR del estudiante."""
    from src.crawler.student_canvas import sincronizar_canvas_estudiante, obtener_datos_canvas_estudiante
    username = _username_actual(request)
    ok, msg, _ = sincronizar_canvas_estudiante(
        username=username,
        canvas_token=req.canvas_token,
        ics_url=req.ics_url,
        base_url=req.base_url,
        token_expira=req.token_expira,
    )
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    return {
        "status": "ok",
        "mensaje": msg,
        "datos": obtener_datos_canvas_estudiante(username),
    }


@app.post("/api/mi-campus/desconectar")
def api_desconectar_mi_campus(request: Request):
    """Desconecta la cuenta personal de Canvas UNIR del estudiante."""
    from src.crawler.student_canvas import desconectar_canvas_estudiante, obtener_datos_canvas_estudiante
    username = _username_actual(request)
    desconectar_canvas_estudiante(username)
    return {"status": "ok", "datos": obtener_datos_canvas_estudiante(username)}


@app.post("/api/mi-campus/sincronizar-materiales")
def api_sincronizar_materiales_mi_campus(request: Request):
    """Descarga los PDFs nuevos o exclusivos de las asignaturas del alumno desde su propio Canvas UNIR a su carpeta privada."""
    from src.crawler.student_canvas import sincronizar_materiales_canvas_estudiante
    username = _username_actual(request)
    ok, msg, resumen = sincronizar_materiales_canvas_estudiante(username)
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    return {"status": "ok", "mensaje": msg, "resumen": resumen}


@app.post("/api/mi-campus/favoritas")
def api_guardar_favoritas(req: FavoritasReq, request: Request):
    """Guarda las asignaturas fijadas ('Mis Asignaturas') por el estudiante."""
    from src.crawler.student_canvas import guardar_asignaturas_favoritas_usuario
    cfg = guardar_asignaturas_favoritas_usuario(_username_actual(request), req.asignaturas)
    return {"status": "ok", "asignaturas_favoritas": cfg.get("asignaturas_favoritas", [])}


class ActualizarItemAgendaReq(BaseModel):
    item_id: str
    titulo: Optional[str] = None
    asignatura: Optional[str] = None
    categoria: Optional[str] = None
    estado: Optional[str] = None
    fecha_dia: Optional[str] = None
    hora_inicio: Optional[str] = None
    recordatorio: Optional[bool] = None
    nota: Optional[float] = None
    nota_personal: Optional[float] = None
    comentario: Optional[str] = None


class CrearItemAgendaReq(BaseModel):
    titulo: str
    asignatura: str
    categoria: Optional[str] = "Tema"
    fecha_dia: Optional[str] = None
    hora_inicio: Optional[str] = None
    estado: Optional[str] = None
    recordatorio: Optional[bool] = None
    comentario: Optional[str] = None


class EstadoTemaReq(BaseModel):
    asignatura: str
    tema_num: int
    tema_titulo: Optional[str] = None
    estado: str  # "Por hacer", "En proceso", "Hecho"
    fecha_dia: Optional[str] = None


ACRONIMOS_DAM_CANONICOS = {
    "ad": "Acceso a Datos",
    "di": "Desarrollo de Interfaces",
    "pmdm": "Programación Multimedia y Dispositivos Móviles",
    "psp": "Programación de Servicios y Procesos",
    "sge": "Sistemas de Gestión Empresarial",
    "nube": "Arquitecturas en la Nube",
    "cloud": "Arquitecturas en la Nube",
    "proy": "Proyecto Intermodular DAM",
    "proyecto": "Proyecto Intermodular DAM",
    "ffe": "Formación en Empresa (FFE)",
    "dig": "Digitalización Aplicada",
    "sost": "Sostenibilidad Aplicada",
    "ing": "Inglés Profesional",
    "ipe": "Itinerario para la Empleabilidad II",
    "ipe2": "Itinerario para la Empleabilidad II",
    "ipe ii": "Itinerario para la Empleabilidad II",
}


def normalizar_etiqueta_asignatura(nombre: str) -> str:
    """Normaliza cualquier nombre, código de grupo (ej: Grupo 3) o acrónimo (AD, PMDM, SGE...) al nombre canónico."""
    if not nombre:
        return ""
    limpio = re.sub(r"\(grupo\s*\d+\)", "", nombre, flags=re.IGNORECASE).strip()
    limpio = re.sub(r"\[.*?\]", "", limpio).strip()
    k = limpio.lower().strip()
    if k in ACRONIMOS_DAM_CANONICOS:
        return ACRONIMOS_DAM_CANONICOS[k]
    res = normalizar_nombre_asignatura(limpio)
    k_res = res.lower().strip()
    if k_res in ACRONIMOS_DAM_CANONICOS:
        return ACRONIMOS_DAM_CANONICOS[k_res]
    if "digitali" in k_res:
        return "Digitalización Aplicada"
    if "formaci" in k_res or "ffe" in k_res:
        return "Formación en Empresa (FFE)"
    if "sostenib" in k_res:
        return "Sostenibilidad Aplicada"
    return res


def son_misma_asignatura(asig1: str, asig2: str) -> bool:
    """Comprueba si dos textos de asignatura corresponden a la misma materia, tolerando variantes y acrónimos."""
    if not asig1 or not asig2:
        return False
    n1 = normalizar_etiqueta_asignatura(asig1).lower().strip()
    n2 = normalizar_etiqueta_asignatura(asig2).lower().strip()
    if n1 == n2:
        return True
    return (n1 in n2) or (n2 in n1)


def _normalizar_titulo_tarea(tit: str) -> str:
    """Normaliza el título de una tarea quitando palabras genéricas y puntuación."""
    t = (tit or "").lower().strip()
    t = re.sub(r"\b(individual|grupal|en grupo|evaluable|obligatoria)\b", "", t)
    t = re.sub(r"[^a-z0-9áéíóúüñ]", " ", t)
    return " ".join(t.split())


def son_misma_tarea_o_entrega(tit1: str, tit2: str, f_dia1: str = "", f_dia2: str = "") -> bool:
    """Comprueba si dos tareas representan exactamente la misma actividad evaluable para evitar duplicados."""
    norm1 = _normalizar_titulo_tarea(tit1)
    norm2 = _normalizar_titulo_tarea(tit2)
    if not norm1 or not norm2:
        return False
    if norm1 == norm2:
        return True

    nums1 = re.findall(r"\d+", norm1)
    nums2 = re.findall(r"\d+", norm2)
    es_test1 = "test" in norm1 or "cuestionario" in norm1
    es_test2 = "test" in norm2 or "cuestionario" in norm2

    # Si uno es test y el otro no, NUNCA son la misma tarea
    if es_test1 != es_test2:
        return False

    # Si ambos tienen números y son diferentes (ej. Test Tema 1 vs Test Tema 2), NUNCA son iguales
    if nums1 and nums2 and nums1 != nums2:
        return False

    if "actividad" in norm1 and "actividad" in norm2:
        return nums1 == nums2 if (nums1 and nums2) else False

    if "entrega" in norm1 and "entrega" in norm2:
        return nums1 == nums2 if (nums1 and nums2) else False

    if f_dia1 and f_dia2 and f_dia1 == f_dia2:
        if norm1 in norm2 or norm2 in norm1:
            return True

    return False


_CACHE_AGENDA_COMBINADA: Dict[str, Tuple[float, List[Dict[str, Any]]]] = {}


def invalidar_cache_agenda(username: Optional[str] = None):
    """Invalida la caché en memoria del calendario combinado cuando hay cambios."""
    if username:
        _CACHE_AGENDA_COMBINADA.pop(username, None)
    else:
        _CACHE_AGENDA_COMBINADA.clear()


def _combinar_agenda_oficial_y_estudiante(username: str) -> List[Dict[str, Any]]:
    """
    Combina el calendario oficial DAM 2026-2027 (clases, tutorías, entregas PAC, temas y tests)
    con el estado personal del estudiante (su organizador Notion privado + su conexión a Canvas).
    Optimizado con indexación por asignatura y caché en memoria para respuesta ultra rápida (< 20ms).
    """
    import time
    from collections import defaultdict
    from src.crawler.agenda_sync import cargar_agenda_cache, obtener_organizador_usuario, calcular_dias_restantes
    from src.crawler.student_canvas import obtener_datos_canvas_estudiante

    ahora = time.time()
    cache_entry = _CACHE_AGENDA_COMBINADA.get(username)
    if cache_entry and (ahora - cache_entry[0] < 15.0):
        return [dict(e) for e in cache_entry[1]]

    base_eventos = cargar_agenda_cache()
    org_u = obtener_organizador_usuario(username)
    overrides = org_u.get("overrides", {})
    items_propios = org_u.get("items_propios", [])

    datos_est = obtener_datos_canvas_estudiante(username)
    entregas_est = datos_est.get("entregas", []) if datos_est.get("config", {}).get("conectado") else []

    # Agrupar entregas de Canvas por asignatura normalizada para búsqueda instantánea
    entregas_est_por_asig = defaultdict(list)
    for e in entregas_est:
        a_norm = normalizar_etiqueta_asignatura(e.get("asignatura") or "").lower().strip()
        entregas_est_por_asig[a_norm].append(e)

    combinados = []
    usados_ids = set()
    combinados_ids = set()
    tareas_combinadas_por_asig = defaultdict(list)

    for ev in base_eventos + items_propios:
        ev_c = dict(ev)
        iid = ev_c.get("id", "")
        if iid:
            combinados_ids.add(iid)
        ov = overrides.get(iid, {})
        if ov.get("oculto"):
            continue

        # Estado base según tipo si no lo trae aún
        cat = ov.get("categoria") or ev_c.get("categoria") or ("Clase" if ev_c.get("tipo") == "clase_directo" else ("Actividad" if ev_c.get("tipo") == "tarea" else "Tema"))
        ev_c["categoria"] = cat
        if not ev_c.get("estado"):
            ev_c["estado"] = "No vista" if cat in ("Clase", "Tutoría") else ("No entregado" if cat in ("Actividad", "Entrega", "Práctica", "Examen", "Proyecto", "FFE", "Formulario") else "No hecho")

        # Aplicar overrides personales de esta cuenta (título, asignatura, categoría, estado, fecha, hora, recordatorio, nota, comentario)
        if ov.get("titulo"):
            ev_c["titulo"] = ov["titulo"]
        if ov.get("asignatura"):
            ev_c["asignatura"] = ov["asignatura"]
        if ov.get("categoria"):
            ev_c["categoria"] = ov["categoria"]
            if ov["categoria"] in ("Clase", "Tutoría"):
                ev_c["tipo"] = "clase_directo"
            elif ov["categoria"] in ("Actividad", "Entrega", "Práctica", "Examen", "Proyecto", "FFE", "Formulario"):
                ev_c["tipo"] = "tarea"
            else:
                ev_c["tipo"] = "tema"
        if ov.get("estado"):
            ev_c["estado"] = ov["estado"]
        if ov.get("hora_inicio"):
            ev_c["hora_inicio"] = ov["hora_inicio"]
        if "recordatorio" in ov and ov["recordatorio"] is not None:
            ev_c["recordatorio"] = bool(ov["recordatorio"])
        if ov.get("fecha_dia"):
            ev_c["fecha_dia"] = ov["fecha_dia"]
            hora_i = ev_c.get("hora_inicio") or "23:59"
            ev_c["fecha_iso"] = f"{ov['fecha_dia']}T{hora_i}:00Z"
            dias_r, plazo_t = calcular_dias_restantes(ev_c["fecha_iso"])
            ev_c["dias_restantes"] = dias_r
            ev_c["plazo_texto"] = plazo_t
        if "nota_personal" in ov and ov["nota_personal"] is not None:
            ev_c["nota_personal"] = ov["nota_personal"]
            ev_c["nota"] = ov["nota_personal"]
        if "comentario" in ov and ov["comentario"] is not None:
            ev_c["comentario"] = ov["comentario"]

        # Asegurar cálculo de días restantes y plazo para todos los ítems (incluidos propios e importados)
        f_iso_calc = ev_c.get("fecha_iso") or (f"{ev_c.get('fecha_dia')}T{ev_c.get('hora_inicio') or '23:59'}:00Z" if ev_c.get("fecha_dia") else "")
        if f_iso_calc and ev_c.get("dias_restantes") is None:
            dias_r, plazo_t = calcular_dias_restantes(f_iso_calc)
            ev_c["dias_restantes"] = dias_r
            if not ev_c.get("plazo_texto"):
                ev_c["plazo_texto"] = plazo_t

        # Si el alumno conectó Canvas y esta entrega figura entregada/calificada en su Canvas
        # IMPORTANTE: Solo enlazar entregas del calendario oficial base; NUNCA sobreescribir ítems propios del alumno
        if ev_c.get("tipo") == "tarea" and not ev_c.get("es_propio"):
            asig_base = ev_c.get("asignatura") or ""
            asig_norm = normalizar_etiqueta_asignatura(asig_base).lower().strip()
            tit_base = ev_c.get("titulo") or ""
            f_dia_base = ev_c.get("fecha_dia") or (ev_c.get("fecha_iso") or "")[:10]

            match = None
            candidatos_asig = entregas_est_por_asig.get(asig_norm, [])
            for e_cand in candidatos_asig:
                if e_cand.get("id") in usados_ids:
                    continue
                f_dia_cand = (e_cand.get("fecha_iso") or "")[:10]
                if son_misma_tarea_o_entrega(tit_base, e_cand.get("titulo") or "", f_dia_base, f_dia_cand):
                    match = e_cand
                    break

            if not match and f_dia_base:
                candidatos_fecha = [
                    e for e in candidatos_asig
                    if e.get("id") not in usados_ids
                    and (e.get("fecha_iso") or "")[:10] == f_dia_base
                ]
                if len(candidatos_fecha) == 1:
                    cand = candidatos_fecha[0]
                    es_test_ev = "test" in tit_base.lower() or "cuestionario" in tit_base.lower()
                    es_test_cand = "test" in (cand.get("titulo") or "").lower() or "cuestionario" in (cand.get("titulo") or "").lower()
                    if es_test_ev == es_test_cand:
                        match = cand

            if match and match.get("id") not in usados_ids:
                if overrides.get(match.get("id"), {}).get("oculto"):
                    continue
                usados_ids.add(match.get("id"))
                ev_c["entregada"] = match.get("entregada", False)
                ev_c["calificada"] = match.get("calificada", False)
                ev_c["nota"] = match.get("nota")
                if match.get("entregada") and not ov.get("estado"):
                    ev_c["estado"] = "Hecho"
                if match.get("nota") is not None and ev_c.get("nota_personal") is None:
                    ev_c["nota_personal"] = match.get("nota")
                if match.get("plazo_texto"):
                    ev_c["plazo_texto"] = match["plazo_texto"]
                if match.get("url"):
                    ev_c["url"] = match["url"]

        if ev_c.get("estado") in ("Hecho", "Vista", "Vista no relevante"):
            ev_c["entregada"] = True
        else:
            ev_c["entregada"] = False

        combinados.append(ev_c)
        if ev_c.get("tipo") == "tarea":
            a_n = normalizar_etiqueta_asignatura(ev_c.get("asignatura") or "").lower().strip()
            tareas_combinadas_por_asig[a_n].append(ev_c)

    # Añadir exclusivamente entregas (tareas/PACs/tests) personales adicionales del alumno en Canvas que no existan en el calendario oficial
    for e_user in entregas_est:
        # NUNCA añadir sesiones lectivas o clases de Canvas como entregas
        if e_user.get("tipo") != "tarea":
            continue
        uid = e_user.get("id")
        ov = overrides.get(uid, {})
        if ov.get("oculto"):
            continue
        if uid in usados_ids or uid in combinados_ids:
            continue

        f_iso = e_user.get("fecha_iso") or ""
        f_dia = f_iso[:10] if len(f_iso) >= 10 else ""
        asig_u = e_user.get("asignatura") or ""
        tit_u = e_user.get("titulo") or ""
        asig_u_norm = normalizar_etiqueta_asignatura(asig_u).lower().strip()

        tareas_asig = tareas_combinadas_por_asig.get(asig_u_norm, [])
        ya_existe = any(
            son_misma_tarea_o_entrega(c.get("titulo") or "", tit_u, c.get("fecha_dia") or "", f_dia)
            for c in tareas_asig
        )
        if ya_existe:
            continue

        e_copia = dict(e_user)
        e_copia["fecha_dia"] = ov.get("fecha_dia") or f_dia
        e_copia["hora_inicio"] = ov.get("hora_inicio") or (f_iso[11:16] if len(f_iso) >= 16 else "23:59")

        # Categoría adecuada: Test si el título o tipo es de test, Actividad si es tarea/PAC
        es_test = "test" in tit_u.lower() or "cuestionario" in tit_u.lower() or "autoevaluaci" in tit_u.lower()
        e_copia["categoria"] = ov.get("categoria") or ("Test" if es_test else "Actividad")

        if ov.get("titulo"):
            e_copia["titulo"] = ov["titulo"]
        if ov.get("estado"):
            e_copia["estado"] = ov["estado"]
        else:
            e_copia["estado"] = "Hecho" if (e_copia.get("entregada") or e_copia.get("calificada")) else "No entregado"

        if "recordatorio" in ov and ov["recordatorio"] is not None:
            e_copia["recordatorio"] = bool(ov["recordatorio"])
        if "nota_personal" in ov and ov["nota_personal"] is not None:
            e_copia["nota_personal"] = ov["nota_personal"]
            e_copia["nota"] = ov["nota_personal"]
        if "comentario" in ov and ov["comentario"] is not None:
            e_copia["comentario"] = ov["comentario"]

        f_iso_calc = e_copia.get("fecha_iso") or (f"{e_copia.get('fecha_dia')}T{e_copia.get('hora_inicio') or '23:59'}:00Z" if e_copia.get("fecha_dia") else "")
        if f_iso_calc:
            dias_r, plazo_t = calcular_dias_restantes(f_iso_calc)
            e_copia["dias_restantes"] = dias_r
            if not e_copia.get("plazo_texto"):
                e_copia["plazo_texto"] = plazo_t

        if e_copia.get("estado") in ("Hecho", "Vista", "Vista no relevante") or e_copia.get("entregada"):
            e_copia["entregada"] = True
        else:
            e_copia["entregada"] = False

        combinados.append(e_copia)
        combinados_ids.add(uid)
        tareas_combinadas_por_asig[asig_u_norm].append(e_copia)

    campos_ui = {
        "id", "titulo", "asignatura", "categoria", "tipo",
        "fecha_dia", "fecha_iso", "hora_inicio", "estado",
        "entregada", "calificada", "nota", "nota_personal",
        "plazo_texto", "dias_restantes", "urgencia", "url",
        "es_propio", "recordatorio", "comentario", "puntos"
    }
    combinados_compactos = [{k: v for k, v in e.items() if k in campos_ui} for e in combinados]
    _CACHE_AGENDA_COMBINADA[username] = (ahora, [dict(e) for e in combinados_compactos])
    return combinados_compactos


@app.get("/api/agenda")
def api_agenda(request: Request, response: Response):
    """Devuelve el calendario completo (clases, entregas, temas, tests) con el estado personal del estudiante."""
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return _combinar_agenda_oficial_y_estudiante(_username_actual(request))


@app.post("/api/agenda/item/actualizar")
def api_actualizar_item_agenda(req: ActualizarItemAgendaReq, request: Request):
    """Guarda el título, asignatura, categoría, estado ('Vista', 'Hecho', etc.), fecha movida, hora, recordatorio, nota o comentario del alumno."""
    from src.crawler.agenda_sync import actualizar_item_organizador_usuario
    username = _username_actual(request)
    cambios = {}
    if req.titulo is not None and req.titulo.strip():
        cambios["titulo"] = req.titulo.strip()
    if req.asignatura is not None and req.asignatura.strip():
        cambios["asignatura"] = req.asignatura.strip()
    if req.categoria is not None and req.categoria.strip():
        cambios["categoria"] = req.categoria.strip()
    if req.estado is not None:
        cambios["estado"] = req.estado
    if req.fecha_dia is not None:
        cambios["fecha_dia"] = req.fecha_dia
    if req.hora_inicio is not None:
        cambios["hora_inicio"] = req.hora_inicio.strip()
    if req.recordatorio is not None:
        cambios["recordatorio"] = bool(req.recordatorio)
    nota_val = req.nota_personal if req.nota_personal is not None else req.nota
    if nota_val is not None:
        cambios["nota_personal"] = nota_val
    if req.comentario is not None:
        cambios["comentario"] = req.comentario
    actualizar_item_organizador_usuario(username, req.item_id, cambios)
    invalidar_cache_agenda(username)
    return {"status": "ok"}


@app.post("/api/agenda/item/crear")
def api_crear_item_agenda(req: CrearItemAgendaReq, request: Request):
    """Crea un nuevo tema, tarea o recordatorio en el calendario personal del estudiante."""
    from src.crawler.agenda_sync import crear_item_organizador_usuario
    username = _username_actual(request)
    item = crear_item_organizador_usuario(username, req.model_dump())
    invalidar_cache_agenda(username)
    return {"status": "ok", "item": item}


@app.delete("/api/agenda/item/{item_id}")
def api_eliminar_item_agenda(item_id: str, request: Request):
    """Elimina un ítem propio creado por el estudiante."""
    from src.crawler.agenda_sync import eliminar_item_organizador_usuario
    username = _username_actual(request)
    ok = eliminar_item_organizador_usuario(username, item_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Solo puedes eliminar ítems creados manualmente por ti.")
    invalidar_cache_agenda(username)
    return {"status": "ok"}


@app.get("/api/temas-estado")
def api_obtener_temas_estado(request: Request):
    """Devuelve los estados personales guardados por el alumno para cada tema de cada asignatura."""
    from src.crawler.agenda_sync import obtener_organizador_usuario
    username = _username_actual(request)
    org = obtener_organizador_usuario(username)
    return org.get("temas_estado", {})


class GenerarEntrenamientoReq(BaseModel):
    asignatura: str
    tema_num: int
    tema_titulo: Optional[str] = ""
    dificultad: Optional[str] = "examen"
    enfoque: Optional[str] = ""
    api_key: Optional[str] = None


class EliminarEntrenamientoReq(BaseModel):
    asignatura: str
    tema_num: int
    reto_id: str


@app.get("/api/asignatura/tema/entrenamientos")
def api_entrenamientos_tema(asignatura: str, tema_num: int, request: Request):
    """Devuelve los entrenamientos prácticos del tema (oficiales de UNIR + generados por IA para el usuario)."""
    from src.extractors.entrenamientos_extractor import obtener_entrenamientos_tema
    username = _username_actual(request)
    return obtener_entrenamientos_tema(asignatura, int(tema_num), username)


@app.post("/api/asignatura/tema/generar-entrenamiento")
def api_generar_entrenamiento_tema(req: GenerarEntrenamientoReq, request: Request):
    """Genera un nuevo reto práctico con IA pedagógicamente adaptado al temario oficial del tema."""
    user_key = _obtener_user_key_o_exigir(request, req.api_key)
    username = _username_actual(request)
    from src.extractors.entrenamientos_extractor import generar_entrenamiento_con_ia
    try:
        nuevo = generar_entrenamiento_con_ia(
            username=username,
            asignatura=req.asignatura,
            tema_num=int(req.tema_num),
            tema_titulo=req.tema_titulo or f"Tema {req.tema_num}",
            dificultad=req.dificultad or "examen",
            enfoque=req.enfoque or "",
            api_key_override=user_key,
        )
        return {"status": "ok", "entrenamiento": nuevo}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error al generar entrenamiento con IA: {str(e)}")


@app.post("/api/asignatura/tema/eliminar-entrenamiento")
def api_eliminar_entrenamiento_tema(req: EliminarEntrenamientoReq, request: Request):
    """Elimina un entrenamiento de IA creado por el usuario."""
    username = _username_actual(request)
    from src.extractors.entrenamientos_extractor import eliminar_entrenamiento_ia_usuario
    ok = eliminar_entrenamiento_ia_usuario(username, req.asignatura, int(req.tema_num), req.reto_id)
    return {"status": "ok" if ok else "not_found"}


@app.post("/api/asignatura/tema/estado")
def api_actualizar_estado_tema(req: EstadoTemaReq, request: Request):
    """Guarda el estado de un tema (Hecho, En proceso, Por hacer) y lo sincroniza con la agenda personal."""
    import re
    from src.crawler.agenda_sync import (
        _archivo_organizador_usuario,
        obtener_organizador_usuario,
        cargar_agenda_cache,
        escribir_json_atomico
    )
    username = _username_actual(request)
    org = obtener_organizador_usuario(username)
    f = _archivo_organizador_usuario(username)

    asig = req.asignatura.strip()
    temas_est = org.setdefault("temas_estado", {})
    asig_temas = temas_est.setdefault(asig, {})
    asig_temas[str(req.tema_num)] = req.estado

    estado_agenda = "Hecho" if req.estado == "Hecho" else ("En proceso" if req.estado == "En proceso" else "No hecho")

    base_eventos = cargar_agenda_cache()
    items_propios = org.setdefault("items_propios", [])
    overrides = org.setdefault("overrides", {})

    encontrado_id = None
    asig_k = asig.lower()

    # 1. Comprobar en items propios del usuario
    for it in items_propios:
        if (it.get("asignatura") or "").lower() == asig_k:
            tit = (it.get("titulo") or "").lower()
            if re.search(rf"(?:tema|tem|t)\s*0*{req.tema_num}(?:\b|[^\d])", tit):
                it["estado"] = estado_agenda
                if req.estado == "Hecho":
                    it["entregada"] = True
                if req.fecha_dia:
                    it["fecha_dia"] = req.fecha_dia
                encontrado_id = it.get("id")
                break

    # 2. Comprobar en eventos base de la agenda
    if not encontrado_id:
        for ev in base_eventos:
            if (ev.get("asignatura") or "").lower() == asig_k:
                tit = (ev.get("titulo") or "").lower()
                if re.search(rf"(?:tema|tem|t)\s*0*{req.tema_num}(?:\b|[^\d])", tit):
                    eid = ev.get("id")
                    ov = overrides.setdefault(eid, {})
                    ov["estado"] = estado_agenda
                    if req.fecha_dia:
                        ov["fecha_dia"] = req.fecha_dia
                    encontrado_id = eid
                    break

    # 3. Si se especificó fecha_dia y no había evento en ningún lado, crear uno propio
    if req.fecha_dia and not encontrado_id:
        from src.crawler.agenda_sync import crear_item_organizador_usuario
        crear_item_organizador_usuario(username, {
            "titulo": f"Tema {req.tema_num}: {req.tema_titulo or ('Tema ' + str(req.tema_num))}",
            "asignatura": asig,
            "categoria": "Tema",
            "estado": estado_agenda,
            "fecha_dia": req.fecha_dia
        })

    escribir_json_atomico(f, org)
    try:
        from src.core.cloud_sync import respaldar_archivo_en_hf_async
        respaldar_archivo_en_hf_async(f)
    except Exception:
        pass
    invalidar_cache_agenda(username)

    return {"status": "ok", "estado": req.estado, "temas_estado": org.get("temas_estado", {})}


@app.post("/api/sincronizar-agenda")
def api_sincronizar_agenda(request: Request):
    """Sincroniza en directo el calendario oficial y el Canvas personal del estudiante."""
    from src.crawler.student_canvas import obtener_config_canvas_usuario, sincronizar_canvas_estudiante
    from src.crawler.agenda_sync import sincronizar_agenda_canvas
    username = _username_actual(request)
    cfg = obtener_config_canvas_usuario(username)
    if cfg.get("conectado"):
        sincronizar_canvas_estudiante(username=username)
    sincronizar_agenda_canvas()
    invalidar_cache_agenda(username)
    eventos = _combinar_agenda_oficial_y_estudiante(username)
    return {"status": "ok", "total": len(eventos), "eventos": eventos}


@app.get("/api/flashcards")
def api_flashcards(request: Request, programa: str = "dam", asig: Optional[str] = None):
    """Devuelve las barajas de flashcards privadas del usuario para memorización y repaso activo."""
    from src.ai.flashcards import GestorFlashcards
    gestor = GestorFlashcards(username=_username_actual(request))
    return gestor.obtener_tarjetas(programa, asignatura=asig)


class CalificarCardReq(BaseModel):
    programa: str
    tarjeta_id: str
    dificultad: str


@app.post("/api/flashcards/calificar")
def api_calificar_flashcard(req: CalificarCardReq, request: Request):
    from src.ai.flashcards import GestorFlashcards
    gestor = GestorFlashcards(username=_username_actual(request))
    return gestor.calificar_tarjeta(req.programa, req.tarjeta_id, req.dificultad)


class GenerarCardsReq(BaseModel):
    asignatura: str
    programa: str
    texto: str
    cantidad: Optional[int] = 4
    api_key: Optional[str] = None


@app.post("/api/flashcards/generar")
def api_generar_flashcards(req: GenerarCardsReq, request: Request):
    user_key = _obtener_user_key_o_exigir(request, req.api_key)
    from src.ai.flashcards import GestorFlashcards
    gestor = GestorFlashcards(username=_username_actual(request))
    nuevas = gestor.generar_flashcards_con_ia(
        texto_tema=req.texto,
        asignatura=req.asignatura,
        programa_id=req.programa,
        cantidad=req.cantidad or 4,
        api_key_override=user_key,
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
    api_key: Optional[str] = None


class ConfigurarProveedoresReq(BaseModel):
    openrouter_key: Optional[str] = None
    groq_key: Optional[str] = None
    gemini_key: Optional[str] = None


@app.get("/api/modelos-ia")
def api_modelos_ia():
    """Devuelve el catálogo de modelos gratuitos (Google AI Studio, Gemma 4, OpenRouter Free y Groq Free)."""
    return tutor_ia.client.obtener_estado_modelos()


@app.get("/api/proveedores-ia")
def api_obtener_proveedores_ia(request: Request):
    """Devuelve las claves actuales guardadas (para pre-rellenar la interfaz y evitar que se pierdan) y su estado."""
    from src.core.config import get_openrouter_api_key, get_groq_api_key, get_gemini_api_key
    u = getattr(request.state, "usuario", None) or {}

    gemini_k = (u.get("gemini_api_key") or "").strip() or get_gemini_api_key()
    openrouter_k = (u.get("openrouter_api_key") or "").strip() or get_openrouter_api_key()
    groq_k = (u.get("groq_api_key") or "").strip() or get_groq_api_key()

    return {
        "gemini_key": gemini_k,
        "openrouter_key": openrouter_k,
        "groq_key": groq_k,
        "configurados": {
            "gemini": bool(gemini_k),
            "openrouter": bool(openrouter_k),
            "groq": bool(groq_k),
        }
    }


@app.post("/api/proveedores-ia")
def api_configurar_proveedores_ia(req: ConfigurarProveedoresReq, request: Request):
    """Guarda claves gratuitas adicionales (OpenRouter / Groq / Gemini) en .env Y en el perfil de SQLite del usuario."""
    from src.core.config import save_openrouter_api_key, save_groq_api_key, save_gemini_api_key
    from src.core.auth import actualizar_api_key_usuario
    u = getattr(request.state, "usuario", None) or {}
    username = u.get("username")

    if req.openrouter_key is not None:
        val = req.openrouter_key.strip()
        save_openrouter_api_key(val)
        if username:
            actualizar_api_key_usuario(username, val, proveedor="openrouter")

    if req.groq_key is not None:
        val = req.groq_key.strip()
        save_groq_api_key(val)
        if username:
            actualizar_api_key_usuario(username, val, proveedor="groq")

    if req.gemini_key is not None:
        val = req.gemini_key.strip()
        save_gemini_api_key(val)
        if username:
            actualizar_api_key_usuario(username, val, proveedor="gemini")
        tutor_ia.client._client = None

    return tutor_ia.client.obtener_estado_modelos()


def _preparar_contexto_chat(msg: ChatMessage, nombre_estudiante: str = "estudiante"):
    """Prepara las instrucciones de sistema especializadas y el historial con inyección RAG inteligente y selectiva."""
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

    # Detección inteligente de preguntas de seguimiento conversacional
    q_limpia = msg.mensaje.strip().lower()
    palabras_pregunta = [w for w in re.findall(r"\b\w+\b", q_limpia) if len(w) > 2]
    
    patrones_seguimiento = [
        "y como", "y cómo", "como se hace", "cómo se hace", "ponme un ejemplo",
        "dame un ejemplo", "explica", "explícamelo", "no lo entiendo", "no entiendo",
        "continua", "continúa", "sigue", "por qué", "porque", "diferencia entre",
        "que significa", "qué significa", "como sería", "cómo sería", "hazlo", "muestramelo"
    ]
    es_pregunta_seguimiento = (len(palabras_pregunta) <= 5 and any(p in q_limpia for p in patrones_seguimiento)) or (len(palabras_pregunta) <= 3)

    peticion_clase_explicita = any(kw in q_limpia for kw in [
        "clase", "videoclase", "profesor", "minuto", "sesion", "sesión", "borja",
        "doc", "pdf", "actividad", "tarea", "enunciado", "temario", "examen"
    ])

    bloques_contexto = []
    # Solo inyectar búsqueda documental pesada si no es un simple seguimiento o si se piden materiales explícitamente
    if peticion_clase_explicita or not es_pregunta_seguimiento:
        contexto_citas = formatear_contexto_para_tutor(msg.mensaje, msg.asignatura)
        contexto_trans_general = obtener_contexto_inteligente_clases(msg.mensaje, msg.asignatura, historial=msg.historial)
        contexto_materiales = buscar_en_materiales(msg.mensaje, msg.asignatura)

        if contexto_citas:
            bloques_contexto.append(contexto_citas)
        if contexto_trans_general:
            bloques_contexto.append(contexto_trans_general)
        if contexto_materiales:
            bloques_contexto.append(contexto_materiales)

    contexto_asig = f"\n[Asignatura / Módulo activo: {msg.asignatura}]" if msg.asignatura else ""

    nombre_alumno_fmt = nombre_estudiante.strip().capitalize() if nombre_estudiante else "estudiante"
    instrucciones_base = (
        f"\nINSTRUCCIONES CLAVE DE MÁXIMA ROBUSTEZ Y NATURALIDAD PARA NOVA:\n"
        f"1. Eres NOVA, la mentora de élite y copiloto académica personal del alumno {nombre_alumno_fmt} en NOVA Study Hub.\n"
        "2. FLUJO CONVERSACIONAL NATURAL (CRUCIAL): NO saludes en cada turno con 'Hola' o presentándote repetidamente. "
        "Ve DIRECTO al grano respondiendo con calidez y rigor, como en una conversación fluida en tiempo real.\n"
        "3. MEMORIA CONVERSACIONAL ACTIVA: Recuerda y apóyate siempre en lo que habéis discutido en los mensajes anteriores. "
        "Si el alumno hace una pregunta de seguimiento ('¿y cómo se hace?', 'ponme un ejemplo', 'explícame el paso 2'), "
        "responde enlazando directamente con el tema previo sin desviar la conversación ni reiniciar el contexto.\n"
        "4. ROBUSTEZ Y RIGOR TÉCNICO: Desarrolla respuestas profundas, completas y fundamentadas en ingeniería de software y arquitectura. "
        "Explica el porqué bajo el capó (JVM, memoria, hilos, SO, red, base de datos). Prohibidas las respuestas telegráficas o superficiales.\n"
        "5. CÓDIGO DIDÁCTICO ADAPTADO AL NIVEL: Adapta siempre los ejemplos al temario y al estilo que enseñan los profesores en clase (Borja, César, etc.). "
        "Prioriza código limpio, pedagógico y directo al grano usando 'System.out.println()' estándar para ejemplos y consola. "
        "NO añadas boilerplate pesado ni sobreingeniería (como java.util.logging.Logger o log4j) a menos que el alumno te pida explícitamente 'código para producción' o 'con logging'. "
        "El código debe ser compilable y completo, pero sin ruido innecesario para estudiar.\n"
        "6. TRAMPAS TÍPICAS DE EXAMEN: Advierte proactivamente sobre preguntas capciosas de examen tipo test, errores conceptuales habituales, casos límite y excepciones típicas del temario.\n"
        "7. ACCESO LOCAL COMPLETO: Tienes ACCESO TOTAL Y LOCAL a la biblioteca oficial de clases grabadas transcritas con minutaje exacto y a todos los temarios y actividades. NUNCA digas que no tienes acceso al campus o a los materiales.\n"
        "8. FORMATO: Emplea Markdown enriquecido con títulos jerárquicos, negritas para conceptos clave, listas didácticas y tablas comparativas.\n"
        "9. FIDELIDAD ABSOLUTA AL MATERIAL OFICIAL (PROHIBIDO INVENTAR): Basa tus explicaciones, guías y respuestas ESTRICTAMENTE en el temario oficial, apuntes y transcripciones reales de clase. "
        "PROHIBIDO inventar conceptos, librerías ajenas al curso, fechas de entrega inventadas o declaraciones ficticias del profesor. "
        "Si una duda técnica concreta o detalle no consta en los materiales oficiales, indícalo claramente con honestidad académica ('Según los materiales oficiales...') sin especular ni alucinar.\n"
    )

    system_instruction_completo = f"{contexto_rol}\n{instrucciones_base}\n{contexto_asig}".strip()

    contexto_inyectado = "\n\n".join(bloques_contexto)
    if contexto_inyectado:
        # Si hay contexto RAG, acotarlo claramente para no canibalizar la pregunta
        mensaje_actual_con_contexto = (
            f"[CONTEXTO DE REFERENCIA OFICIAL UNIR (Usa esto si es relevante para responder)]:\n"
            f"{contexto_inyectado}\n\n"
            f"---\n"
            f"Mensaje del alumno ({nombre_alumno_fmt}): {msg.mensaje}"
        )
    else:
        mensaje_actual_con_contexto = msg.mensaje

    mensajes_chat = []
    if msg.historial:
        for item in msg.historial[-16:]:
            rol = "user" if item.rol in ["user", "alumno", "usuario"] else "model"
            if item.texto and item.texto.strip():
                mensajes_chat.append({"role": rol, "text": item.texto.strip()})

    mensajes_chat.append({"role": "user", "text": mensaje_actual_con_contexto})
    return system_instruction_completo, mensajes_chat, mensaje_actual_con_contexto


@app.post("/api/validar-gemini-key")
def api_validar_gemini_key(req: dict):
    """Comprueba de forma rápida si una clave de Gemini proporcionada por el usuario es válida."""
    key = (req.get("api_key") or "").strip()
    if not key:
        return {"valida": False, "error": "Debes introducir una clave de API."}
    try:
        from google import genai
        from google.genai import types
        c = genai.Client(api_key=key, http_options=types.HttpOptions(timeout=10000))
        # Probar con los modelos modernos admitidos oficialmente para cuentas nuevas
        modelos_test = ["gemini-3.5-flash-lite", "gemini-flash-lite-latest", "gemini-3.5-flash", "gemini-3-flash-preview"]
        ultimo_error = None
        for mod in modelos_test:
            try:
                r = c.models.generate_content(model=mod, contents="hola")
                if r and r.text:
                    return {"valida": True, "mensaje": "Clave de Gemini verificada y operativa."}
            except Exception as e:
                ultimo_error = e
                err_str = str(e)
                if "API_KEY_INVALID" in err_str or "API key not valid" in err_str:
                    return {"valida": False, "error": "La clave de API no es válida o está mal copiada."}
                continue

        if ultimo_error:
            return {"valida": False, "error": str(ultimo_error)}
        return {"valida": False, "error": "No se pudo validar con los modelos disponibles."}
    except Exception as e:
        return {"valida": False, "error": str(e)}


@app.post("/api/chat")
def api_chat(msg: ChatMessage, request: Request):
    """Responde preguntas mediante NOVA con rol especializado, memoria multi-turno e inyección RAG completa."""
    user_key = _obtener_user_key_o_exigir(request, msg.api_key)
    usuario_req = getattr(request.state, "usuario", {}) or {}
    nombre_user = usuario_req.get("nombre") or usuario_req.get("username") or "estudiante"
    system_instruction_completo, mensajes_chat, mensaje_actual_con_contexto = _preparar_contexto_chat(msg, nombre_user)

    respuesta = tutor_ia.client.generar_chat(
        mensajes=mensajes_chat,
        system_instruction=system_instruction_completo,
        temperatura=0.4,
        modelo_preferido=msg.modelo,
        api_key_override=user_key,
    )

    if not respuesta:
        prompt_fallback = f"{system_instruction_completo}\n\n{mensaje_actual_con_contexto}\n\nNOVA:"
        respuesta = tutor_ia.client.generar_contenido(
            prompt_fallback, temperatura=0.4, modelo_preferido=msg.modelo, api_key_override=user_key
        )

    mod_usado = tutor_ia.client.ultimo_modelo_usado
    return {
        "respuesta": respuesta or "No se pudo generar respuesta en este momento. Inténtalo de nuevo.",
        "modelo_usado": mod_usado,
        "modelo_nombre": tutor_ia.client.obtener_nombre_legible(mod_usado),
        "personal_key_used": bool(user_key),
    }


@app.post("/api/chat-stream")
def api_chat_stream(msg: ChatMessage, request: Request):
    """Emite la respuesta de NOVA en tiempo real mediante Server-Sent Events (SSE)."""
    user_key = _obtener_user_key_o_exigir(request, msg.api_key)
    usuario_req = getattr(request.state, "usuario", {}) or {}
    nombre_user = usuario_req.get("nombre") or usuario_req.get("username") or "estudiante"
    system_instruction_completo, mensajes_chat, mensaje_actual_con_contexto = _preparar_contexto_chat(msg, nombre_user)

    def event_generator():
        emitido = False
        try:
            for chunk in tutor_ia.client.generar_chat_stream(
                mensajes=mensajes_chat,
                system_instruction=system_instruction_completo,
                temperatura=0.4,
                modelo_preferido=msg.modelo,
                api_key_override=user_key,
            ):
                if chunk:
                    emitido = True
                    mod_usado = tutor_ia.client.ultimo_modelo_usado
                    yield f"data: {json.dumps({'chunk': chunk, 'modelo_usado': mod_usado, 'modelo_nombre': tutor_ia.client.obtener_nombre_legible(mod_usado), 'personal_key_used': bool(user_key)}, ensure_ascii=False)}\n\n"
        except Exception:
            pass

        if not emitido:
            try:
                prompt_fallback = f"{system_instruction_completo}\n\n{mensaje_actual_con_contexto}\n\nNOVA:"
                res = tutor_ia.client.generar_contenido(
                    prompt_fallback, temperatura=0.4, modelo_preferido=msg.modelo, api_key_override=user_key
                )
                texto_final = res or "No se pudo generar respuesta en este momento. Inténtalo de nuevo."
            except Exception:
                texto_final = "No se pudo generar respuesta en este momento. Inténtalo de nuevo."
            mod_usado = tutor_ia.client.ultimo_modelo_usado
            yield f"data: {json.dumps({'chunk': texto_final, 'modelo_usado': mod_usado, 'modelo_nombre': tutor_ia.client.obtener_nombre_legible(mod_usado), 'personal_key_used': bool(user_key)}, ensure_ascii=False)}\n\n"

        mod_usado = tutor_ia.client.ultimo_modelo_usado
        yield f"data: {json.dumps({'done': True, 'modelo_usado': mod_usado, 'modelo_nombre': tutor_ia.client.obtener_nombre_legible(mod_usado), 'personal_key_used': bool(user_key)}, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


class TtsRequest(BaseModel):
    texto: str
    voz: Optional[str] = "es-ES-ElviraNeural"


def _generar_audio_google_tts(texto: str) -> bytes:
    """Fallback garantizado 100% nativo usando el motor TTS de Google sin dependencias."""
    import urllib.request
    import urllib.parse
    import re
    oraciones = re.split(r"(?<=[.!?,\n])\s+", texto)
    bloques = []
    bloque_actual = ""
    for o in oraciones:
        if len(bloque_actual) + len(o) < 180:
            bloque_actual += (" " if bloque_actual else "") + o
        else:
            if bloque_actual:
                bloques.append(bloque_actual)
            bloque_actual = o[:180]
    if bloque_actual:
        bloques.append(bloque_actual)

    resultado = bytearray()
    for b in bloques[:25]:
        if not b.strip():
            continue
        url = "https://translate.google.com/translate_tts?ie=UTF-8&tl=es&client=tw-ob&q=" + urllib.parse.quote(b.strip())
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
        try:
            with urllib.request.urlopen(req, timeout=8) as resp:
                resultado.extend(resp.read())
        except Exception:
            continue
    return bytes(resultado)


@app.post("/api/tts")
async def api_tts(req: TtsRequest):
    """Genera audio MP3 natural con Edge-TTS y redundancia automática con Google TTS."""
    raw_text = req.texto.strip()
    if not raw_text:
        raise HTTPException(status_code=400, detail="Texto vacío")

    if len(raw_text) > 3500:
        raw_text = raw_text[:3500]

    # 1. Intentar Edge-TTS (Voz neuronal hiperrealista)
    try:
        import edge_tts
        communicate = edge_tts.Communicate(raw_text, req.voz or "es-ES-ElviraNeural")
        buf = bytearray()
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                buf.extend(chunk["data"])
        if len(buf) > 500:
            return Response(
                content=bytes(buf),
                media_type="audio/mpeg",
                headers={
                    "Cache-Control": "public, max-age=86400",
                    "Content-Disposition": 'inline; filename="voz_nova.mp3"',
                }
            )
    except Exception as e:
        console.print(f"[yellow]⚠ Edge-TTS no disponible ({e}). Activando fallback Google TTS...[/yellow]")

    # 2. Fallback de alta disponibilidad con Google TTS
    try:
        audio_google = _generar_audio_google_tts(raw_text)
        if len(audio_google) > 500:
            return Response(
                content=audio_google,
                media_type="audio/mpeg",
                headers={
                    "Cache-Control": "public, max-age=86400",
                    "Content-Disposition": 'inline; filename="voz_nova.mp3"',
                }
            )
    except Exception as e:
        console.print(f"[red]❌ Error en fallback Google TTS:[/red] {e}")

    raise HTTPException(status_code=500, detail="No se pudo generar el audio")


# --- FRONTEND HTML SPA CON SIDEBAR DE SECCIONES ---

@app.get("/", response_class=HTMLResponse)
def dashboard_html():
    return """<!DOCTYPE html>
<html lang="es" class="dark">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>NOVA Study Hub — Plataforma Inteligente Multi-Titulación</title>
  <script>
    /* ===== TEMAS DE COLOR DE NOVA (cada alumno elige el suyo; se guarda en su navegador) ===== */
    window.NOVA_PALETAS = {
      gray:    '#f9fafb #f3f4f6 #e5e7eb #d1d5db #9ca3af #6b7280 #4b5563 #374151 #1f2937 #111827 #030712',
      zinc:    '#fafafa #f4f4f5 #e4e4e7 #d4d4d8 #a1a1aa #71717a #52525b #3f3f46 #27272a #18181b #09090b',
      neutral: '#fafafa #f5f5f5 #e5e5e5 #d4d4d4 #a3a3a3 #737373 #525252 #404040 #262626 #171717 #0a0a0a',
      stone:   '#fafaf9 #f5f5f4 #e7e5e4 #d6d3d1 #a8a29e #78716c #57534e #44403c #292524 #1c1917 #0c0a09',
      slate:   '#f8fafc #f1f5f9 #e2e8f0 #cbd5e1 #94a3b8 #64748b #475569 #334155 #1e293b #0f172a #020617',
      cyan:    '#ecfeff #cffafe #a5f3fc #67e8f9 #22d3ee #06b6d4 #0891b2 #0e7490 #155e75 #164e63 #083344',
      sky:     '#f0f9ff #e0f2fe #bae6fd #7dd3fc #38bdf8 #0ea5e9 #0284c7 #0369a1 #075985 #0c4a6e #082f49',
      indigo:  '#eef2ff #e0e7ff #c7d2fe #a5b4fc #818cf8 #6366f1 #4f46e5 #4338ca #3730a3 #312e81 #1e1b4b',
      violet:  '#f5f3ff #ede9fe #ddd6fe #c4b5fd #a78bfa #8b5cf6 #7c3aed #6d28d9 #5b21b6 #4c1d95 #2e1065',
      purple:  '#faf5ff #f3e8ff #e9d5ff #d8b4fe #c084fc #a855f7 #9333ea #7e22ce #6b21a8 #581c87 #3b0764',
      fuchsia: '#fdf4ff #fae8ff #f5d0fe #f0abfc #e879f9 #d946ef #c026d3 #a21caf #86198f #701a75 #4a044e',
      pink:    '#fdf2f8 #fce7f3 #fbcfe8 #f9a8d4 #f472b6 #ec4899 #db2777 #be185d #9d174d #831843 #500724',
      rose:    '#fff1f2 #ffe4e6 #fecdd3 #fda4af #fb7185 #f43f5e #e11d48 #be123c #9f1239 #881337 #4c0519',
      emerald: '#ecfdf5 #d1fae5 #a7f3d0 #6ee7b7 #34d399 #10b981 #059669 #047857 #065f46 #064e3b #022c22',
      teal:    '#f0fdfa #ccfbf1 #99f6e4 #5eead4 #2dd4bf #14b8a6 #0d9488 #0f766e #115e59 #134e4a #042f2e',
      green:   '#f0fdf4 #dcfce7 #bbf7d0 #86efac #4ade80 #22c55e #16a34a #15803d #166534 #14532d #052e16',
      amber:   '#fffbeb #fef3c7 #fde68a #fcd34d #fbbf24 #f59e0b #d97706 #b45309 #92400e #78350f #451a03',
      orange:  '#fff7ed #ffedd5 #fed7aa #fdba74 #fb923c #f97316 #ea580c #c2410c #9a3412 #7c2d12 #431407',
      blue:    '#eff6ff #dbeafe #bfdbfe #93c5fd #60a5fa #3b82f6 #2563eb #1d4ed8 #1e40af #1e3a8a #172554'
    };
    /* Cada tema reasigna: grises (fondos/textos), acento principal (cyan), secundario (sky) y terciario (indigo).
       Los colores de estado (rojo entregas, verde hecho, ámbar pendiente) NO cambian para no perder su significado. */
    window.NOVA_TEMAS = [
      { id: 'nova',      nombre: 'Nova (Azul)',        desc: 'El estándar: azul cian',      gray: 'gray',    cyan: 'cyan',    sky: 'sky',     indigo: 'indigo',
        surf: '#0b0f19 #0d131f #0d1526 #0f172a #111722 #131722 #18191d #172554 #1a1433' },
      { id: 'nebulosa',  nombre: 'Nebulosa (Violeta)', desc: 'Morado y fucsia',             gray: 'zinc',    cyan: 'fuchsia', sky: 'violet',  indigo: 'purple',
        surf: '#0d0a14 #110d1b #140f22 #170f28 #150f1f #181224 #1b1720 #2e1065 #1f1433' },
      { id: 'bosque',    nombre: 'Bosque (Verde)',     desc: 'Verde esmeralda tranquilo',   gray: 'stone',   cyan: 'teal',    sky: 'emerald', indigo: 'green',
        surf: '#08100d #0a1511 #0b1914 #0c1c16 #0e1814 #101a16 #151a17 #052e16 #0f1f18' },
      { id: 'atardecer', nombre: 'Atardecer (Ámbar)',  desc: 'Tonos cálidos naranja',       gray: 'stone',   cyan: 'orange',  sky: 'amber',   indigo: 'rose',
        surf: '#110c09 #160f0b #19110c #1c130d #1a140f #1c1611 #1d1a17 #431407 #24150f' },
      { id: 'sakura',    nombre: 'Sakura (Rosa)',      desc: 'Rosa suave',                  gray: 'zinc',    cyan: 'pink',    sky: 'rose',    indigo: 'fuchsia',
        surf: '#120a0e #170c12 #1a0e15 #1d0f17 #190f14 #1b1117 #1d181b #500724 #26101c' },
      { id: 'oceano',    nombre: 'Océano (Azul real)', desc: 'Azul profundo',               gray: 'slate',   cyan: 'blue',    sky: 'sky',     indigo: 'violet',
        surf: '#060b16 #08101e #091326 #0b162d #0b1322 #0d1527 #111827 #172554 #141433' },
      { id: 'grafito',   nombre: 'Grafito (Neutro)',   desc: 'Gris minimalista sin color',  gray: 'neutral', cyan: 'zinc',    sky: 'neutral', indigo: 'zinc',
        surf: '#0a0a0a #0f0f0f #121212 #141414 #151515 #171717 #1a1a1a #262626 #1c1c1c' }
    ];
    window.novaHexARgb = function (h) {
      h = h.replace('#', '');
      return parseInt(h.slice(0, 2), 16) + ' ' + parseInt(h.slice(2, 4), 16) + ' ' + parseInt(h.slice(4, 6), 16);
    };
    window.novaAplicarTema = function (id) {
      const tema = window.NOVA_TEMAS.find(t => t.id === id) || window.NOVA_TEMAS[0];
      const raiz = document.documentElement;
      const pasos = ['50', '100', '200', '300', '400', '500', '600', '700', '800', '900', '950'];
      ['gray', 'cyan', 'sky', 'indigo'].forEach(rol => {
        const hexes = window.NOVA_PALETAS[tema[rol]].split(' ');
        pasos.forEach((p, i) => raiz.style.setProperty('--c-' + rol + '-' + p, window.novaHexARgb(hexes[i])));
      });
      tema.surf.split(' ').forEach((hx, i) => raiz.style.setProperty('--surface-' + i, window.novaHexARgb(hx)));
      raiz.setAttribute('data-tema', tema.id);
      const meta = document.querySelector('meta[name=theme-color]');
      if (meta) meta.setAttribute('content', tema.surf.split(' ')[0]);
      return tema.id;
    };
    window.novaAplicarTema(localStorage.getItem('nova_tema') || 'nova');
  </script>
  <script src="https://cdn.tailwindcss.com"></script>
  <script>
    (function () {
      const pasos = ['50', '100', '200', '300', '400', '500', '600', '700', '800', '900', '950'];
      const paleta = rol => Object.fromEntries(pasos.map(p => [p, 'rgb(var(--c-' + rol + '-' + p + ') / <alpha-value>)']));
      const surface = {};
      for (let i = 0; i <= 8; i++) surface[i] = 'rgb(var(--surface-' + i + ') / <alpha-value>)';
      tailwind.config = {
        darkMode: 'class',
        theme: { extend: { colors: { gray: paleta('gray'), cyan: paleta('cyan'), sky: paleta('sky'), indigo: paleta('indigo'), surface: surface } } }
      };
    })();
  </script>
  <script defer src="https://cdn.jsdelivr.net/npm/alpinejs@3.x.x/dist/cdn.min.js"></script>
  <script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
  <script src="https://cdn.jsdelivr.net/npm/dompurify@3.1.7/dist/purify.min.js"></script>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&family=JetBrains+Mono:wght@400;500&display=swap">
  <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
  <style>
    html, body { overflow-x: hidden; max-width: 100vw; width: 100%; }
    body { font-family: 'Inter', sans-serif; background-color: rgb(var(--surface-0)); color: rgb(var(--c-gray-100)); transition: background-color .25s ease; }
    code, pre { font-family: 'JetBrains Mono', monospace; }
    .glass { background: rgb(var(--c-gray-900) / 0.85); backdrop-filter: blur(14px); border: 1px solid rgba(255, 255, 255, 0.08); }
    .glass-card { background: rgb(var(--c-gray-800) / 0.5); backdrop-filter: blur(8px); border: 1px solid rgba(255, 255, 255, 0.05); }

    /* Efecto 3D Flip real de Flashcards */
    .flashcard-perspective {
      perspective: 1200px;
    }
    .flashcard-inner {
      position: relative;
      width: 100%;
      min-height: 320px;
      transition: transform 0.5s cubic-bezier(0.4, 0.2, 0.2, 1);
      transform-style: preserve-3d;
    }
    .flashcard-inner.flipped {
      transform: rotateY(180deg);
    }
    .flashcard-face {
      position: absolute;
      inset: 0;
      width: 100%;
      height: 100%;
      backface-visibility: hidden;
      -webkit-backface-visibility: hidden;
      display: flex;
      flex-direction: column;
      justify-content: space-between;
    }
    .flashcard-back {
      transform: rotateY(180deg);
    }

    /* Ajuste responsivo de texto para evitar cortes y barras horizontales */
    .markdown-content {
      word-break: break-word;
      overflow-wrap: anywhere;
      white-space: normal;
      line-height: 1.7;
    }
    .markdown-content h1 { font-size: 1.45rem; font-weight: 800; color: rgb(var(--c-sky-400)); line-height: 1.4; margin-top: 1.4rem; margin-bottom: 0.8rem; word-break: break-word; }
    .markdown-content h2 { font-size: 1.25rem; font-weight: 700; color: rgb(var(--c-indigo-400)); line-height: 1.4; margin-top: 1.2rem; margin-bottom: 0.6rem; word-break: break-word; }
    .markdown-content h3 { font-size: 1.1rem; font-weight: 600; color: rgb(var(--c-indigo-300)); line-height: 1.35; margin-top: 1rem; margin-bottom: 0.5rem; word-break: break-word; }
    .markdown-content p { margin-bottom: 0.9rem; line-height: 1.75; color: #e2e8f0; word-break: break-word; overflow-wrap: anywhere; }
    .markdown-content ul, .markdown-content ol { margin-bottom: 1rem; padding-left: 1.5rem; line-height: 1.65; word-break: break-word; }
    .markdown-content li { margin-bottom: 0.35rem; word-break: break-word; }
    
    /* Bloques de código y citas: auto-wrap para no forzar scroll horizontal */
    .markdown-content pre {
      background: rgb(var(--c-gray-900));
      padding: 1rem;
      border-radius: 0.5rem;
      margin-bottom: 1rem;
      border: 1px solid rgb(var(--c-gray-700));
      line-height: 1.55;
      white-space: pre-wrap !important;
      word-break: break-word !important;
      overflow-wrap: anywhere !important;
      overflow-x: hidden !important;
    }
    .markdown-content code {
      background: rgb(var(--c-gray-800));
      padding: 0.15rem 0.35rem;
      border-radius: 0.25rem;
      font-size: 0.875rem;
      color: rgb(var(--c-sky-400));
      white-space: pre-wrap !important;
      word-break: break-word !important;
      overflow-wrap: anywhere !important;
    }
    .markdown-content blockquote {
      border-left: 3px solid rgb(var(--c-sky-400));
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
    .markdown-content th, .markdown-content td { border: 1px solid rgb(var(--c-gray-700)); padding: 0.5rem 0.75rem; text-align: left; word-break: break-word; }
    .markdown-content th { background: rgb(var(--c-gray-800)); color: #e5e7eb; }

    /* Cajas interactivas de Active Recall (Cornell Method) */
    .markdown-content details {
      background: rgba(17, 24, 39, 0.7);
      border: 1px solid rgb(var(--c-sky-400) / 0.25);
      border-radius: 0.85rem;
      padding: 0.85rem 1.15rem;
      margin: 1.15rem 0;
      transition: all 0.2s ease;
      box-shadow: 0 4px 12px rgba(0, 0, 0, 0.2);
    }
    .markdown-content details[open] {
      background: rgba(15, 23, 42, 0.95);
      border-color: rgb(var(--c-sky-400) / 0.5);
    }
    .markdown-content summary {
      cursor: pointer;
      font-weight: 600;
      color: rgb(var(--c-sky-400));
      outline: none;
      user-select: none;
      padding: 0.2rem 0;
    }
    .markdown-content summary:hover {
      color: rgb(var(--c-sky-300));
    }
    .markdown-content details[open] summary {
      margin-bottom: 0.75rem;
      border-bottom: 1px dashed rgb(var(--c-sky-400) / 0.2);
      padding-bottom: 0.5rem;
    }

    ::-webkit-scrollbar { width: 6px; height: 6px; }
    ::-webkit-scrollbar-track { background: rgb(var(--surface-0)); }
    ::-webkit-scrollbar-thumb { background: rgb(var(--c-gray-700)); border-radius: 4px; }
    ::-webkit-scrollbar-thumb:hover { background: rgb(var(--c-gray-600)); }

    /* Ajustes específicos de fluidez para móviles y tablets */
    .no-scrollbar::-webkit-scrollbar { display: none; }
    .no-scrollbar { -ms-overflow-style: none; scrollbar-width: none; }
    .safe-area-pb { padding-bottom: max(0.75rem, env(safe-area-inset-bottom, 0.75rem)); }
    @media (max-width: 768px) {
      .glass { backdrop-filter: blur(10px); }
      .markdown-content pre { font-size: 0.78rem; padding: 0.75rem; }
      .markdown-content table { font-size: 0.75rem; display: block; overflow-x: auto; -webkit-overflow-scrolling: touch; }
    }
  </style>
</head>
<body x-data="studyHubApp()" x-init="init()" class="min-h-screen flex bg-surface-0 text-gray-100 overflow-x-hidden">

  <!-- BACKDROP OVERLAY PARA MÓVIL CUANDO EL MENÚ ESTÁ ABIERTO -->
  <div x-show="sidebarVisible" 
       @click="sidebarVisible = false"
       x-transition:enter="transition-opacity ease-out duration-300"
       x-transition:enter-start="opacity-0"
       x-transition:enter-end="opacity-100"
       x-transition:leave="transition-opacity ease-in duration-200"
       x-transition:leave-start="opacity-100"
       x-transition:leave-end="opacity-0"
       class="fixed inset-0 bg-black/75 backdrop-blur-sm z-40 md:hidden"
       style="display: none;"></div>

  <!-- ==================== SIDEBAR PRINCIPAL DE SECCIONES (DRAWER EN MÓVIL, STICKY EN DESKTOP) ==================== -->
  <aside x-show="sidebarVisible"
         x-transition:enter="transition-all ease-out duration-300"
         x-transition:enter-start="-translate-x-full opacity-0"
         x-transition:enter-end="translate-x-0 opacity-100"
         x-transition:leave="transition-all ease-in duration-200"
         x-transition:leave-start="translate-x-0 opacity-100"
         x-transition:leave-end="-translate-x-full opacity-0"
         class="w-72 bg-surface-3/98 border-r border-gray-800 flex flex-col shrink-0 min-h-screen fixed inset-y-0 left-0 z-50 md:sticky md:top-0 md:z-40 select-none shadow-2xl md:shadow-xl">
    
    <!-- BRAND / LOGO -->
    <div class="p-4 sm:p-5 border-b border-gray-800 flex items-center justify-between">
      <div class="flex items-center space-x-3">
        <div class="h-10 w-10 rounded-xl bg-gradient-to-tr from-cyan-500 to-indigo-600 flex items-center justify-center text-white shadow-lg shadow-cyan-500/20 shrink-0">
          <i class="fa-solid fa-graduation-cap text-lg"></i>
        </div>
        <div>
          <h1 class="text-base font-bold bg-clip-text text-transparent bg-gradient-to-r from-cyan-400 to-indigo-400">NOVA Study Hub</h1>
          <p class="text-[11px] text-gray-400 font-medium">Plataforma Multi-Titulación</p>
        </div>
      </div>
      <button @click="sidebarVisible = false" class="p-1.5 text-gray-400 hover:text-white rounded-lg hover:bg-gray-800 transition" title="Ocultar barra lateral">
        <i class="fa-solid fa-xmark text-lg md:hidden"></i>
        <i class="fa-solid fa-angles-left text-sm hidden md:inline"></i>
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

          <!-- BUZÓN DE INCIDENCIAS Y MEJORAS -->
          <div class="flex items-center group/item">
            <button @click="irASeccion('feedback')"
                    :class="tab === 'feedback' ? 'bg-amber-500/10 text-amber-400 border-amber-500/30' : 'text-gray-400 hover:text-gray-200 hover:bg-gray-800/40 border-transparent'"
                    class="flex-1 text-left px-3 py-2 rounded-xl text-xs transition border flex items-center justify-between">
              <div class="flex items-center space-x-2.5">
                <i class="fa-solid fa-bullhorn w-4 text-center text-amber-400"></i>
                <span>Buzón y Mejoras</span>
              </div>
              <span x-show="feedbackPendientesCount > 0" class="text-[9px] bg-amber-500/20 text-amber-300 px-1.5 py-0.5 rounded-full font-bold" x-text="feedbackPendientesCount"></span>
            </button>
            <a href="/?tab=feedback" target="_blank" title="Abrir en pestaña nueva del navegador" class="p-1.5 text-gray-500 hover:text-amber-400 opacity-0 group-hover/item:opacity-100 transition rounded-lg hover:bg-gray-800 ml-1">
              <i class="fa-solid fa-arrow-up-right-from-square text-[10px]"></i>
            </a>
          </div>

          <!-- ENLACE PANEL ADMIN (SOLO VISIBLE PARA ADMINISTRADORES) -->
          <div x-show="usuarioActual && usuarioActual.es_admin" class="flex items-center group/item pt-1">
            <button @click="irASeccion('admin')"
                    :class="tab === 'admin' ? 'bg-amber-500/10 text-amber-400 border-amber-500/30' : 'text-gray-400 hover:text-amber-300 hover:bg-gray-800/40 border-transparent'"
                    class="flex-1 text-left px-3 py-2 rounded-xl text-xs transition border flex items-center justify-between">
              <div class="flex items-center space-x-2.5">
                <i class="fa-solid fa-shield-halved w-4 text-center text-amber-400"></i>
                <span class="font-semibold text-amber-300/90">Panel Admin</span>
              </div>
              <span x-show="adminPendientesCount > 0" class="px-1.5 py-0.5 text-[10px] font-bold rounded-full bg-amber-500 text-black" x-text="adminPendientesCount"></span>
            </button>
            <a href="/?tab=admin" target="_blank" title="Abrir en pestaña nueva del navegador" class="p-1.5 text-gray-500 hover:text-amber-400 opacity-0 group-hover/item:opacity-100 transition rounded-lg hover:bg-gray-800 ml-1">
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
    <header class="glass sticky top-0 z-30 px-2.5 sm:px-6 py-2 sm:py-3.5 flex items-center justify-between border-b border-gray-800/80 gap-1.5 sm:gap-2">
      <div class="flex items-center space-x-1.5 sm:space-x-3 min-w-0 shrink">
        <!-- BOTÓN TOGGLE SIDEBAR -->
        <button @click="toggleSidebar()" 
                class="p-1.5 sm:p-2 text-gray-400 hover:text-cyan-400 hover:bg-gray-800/80 rounded-xl transition flex items-center space-x-1.5 text-xs font-semibold border border-transparent hover:border-gray-700 shrink-0"
                :title="sidebarVisible ? 'Ocultar barra lateral' : 'Mostrar barra lateral'">
          <i :class="sidebarVisible ? 'fa-solid fa-bars-staggered' : 'fa-solid fa-bars text-cyan-400 text-sm'"></i>
          <span x-show="!sidebarVisible" class="text-xs text-gray-300 ml-1 font-semibold hidden sm:inline">Menú</span>
        </button>

        <span class="text-[11px] sm:text-xs font-bold px-2 sm:px-2.5 py-0.5 sm:py-1 rounded-full uppercase tracking-wider truncate max-w-[100px] sm:max-w-none"
              :style="'background: ' + obtenerFondoHex(programaActual?.color) + '; color: ' + obtenerColorHex(programaActual?.color)"
              x-text="programaActual?.nombre"></span>
        <span class="text-xs text-gray-400 hidden lg:inline" x-text="cursoFiltro === 'todos' ? 'Todo el programa' : (cursoFiltro === '2dam' ? '2º Curso' : '1º Curso')"></span>
      </div>

      <!-- BUSCADOR RÁPIDO Y BOTONES SUPERIORES -->
      <div class="flex items-center space-x-1 sm:space-x-3 shrink-0">
        <div class="relative hidden md:block w-36 lg:w-56 focus-within:w-60 transition-all">
          <i class="fa-solid fa-magnifying-glass absolute left-3 top-2.5 text-xs text-gray-500"></i>
          <input type="text" x-model="busquedaTexto" placeholder="Buscar..." class="w-full bg-gray-900/80 text-white placeholder-gray-500 text-xs pl-8 pr-3 py-2 rounded-xl border border-gray-800 focus:outline-none focus:border-cyan-500">
        </div>
        <button @click="abrirChatConAsignatura('')" class="hidden sm:flex px-2.5 sm:px-3.5 py-2 bg-gradient-to-r from-cyan-500 to-indigo-600 rounded-xl text-xs font-semibold hover:shadow-lg hover:shadow-cyan-500/20 transition items-center space-x-1.5 shrink-0">
          <i class="fa-solid fa-robot"></i><span class="hidden sm:inline">Consultar </span><span>NOVA</span>
        </button>

        <!-- BOTÓN CONECTAR MI CAMPUS UNIR (CANVAS PERSONAL) -->
        <button @click="abrirModalMiCampus()"
                :class="miCampusConfig?.token_estado?.estado === 'caducado' ? 'bg-rose-500/20 border-rose-500/50 text-rose-300' : (miCampusConfig?.token_estado?.estado === 'por_caducar' ? 'bg-amber-500/20 border-amber-500/50 text-amber-300' : (miCampusConfig?.conectado ? 'bg-indigo-500/20 border-indigo-500/40 text-indigo-300' : 'bg-gray-800 border-gray-700 text-gray-300 hover:text-indigo-300 hover:border-indigo-500/30'))"
                class="p-2 sm:px-3 sm:py-2 rounded-xl text-xs font-medium border transition flex items-center space-x-1.5 shrink-0 shadow-sm relative"
                :title="miCampusConfig?.token_estado?.estado === 'caducado' ? '¡Tu Token de Canvas ha caducado!' : (miCampusConfig?.token_estado?.estado === 'por_caducar' ? ('¡Atención! Tu Token de Canvas caduca en ' + (miCampusConfig?.token_estado?.dias_restantes ?? 0) + ' días') : (miCampusConfig?.conectado ? ('Campus UNIR: ' + (miCampusConfig?.alumno_nombre || 'Activo')) : 'Conectar mi cuenta personal del Campus UNIR (Canvas)'))">
          <span class="h-2 w-2 rounded-full" :class="miCampusConfig?.token_estado?.estado === 'caducado' ? 'bg-rose-500 animate-ping' : (miCampusConfig?.token_estado?.estado === 'por_caducar' ? 'bg-amber-400 animate-ping' : (miCampusConfig?.conectado ? 'bg-indigo-400 animate-pulse' : 'bg-gray-500'))"></span>
          <i class="fa-solid fa-building-columns text-[10px]" :class="miCampusConfig?.token_estado?.estado === 'caducado' ? 'text-rose-400' : (miCampusConfig?.token_estado?.estado === 'por_caducar' ? 'text-amber-400' : (miCampusConfig?.conectado ? 'text-indigo-400' : 'text-gray-400'))"></i>
          <span class="hidden sm:inline font-medium" x-text="miCampusConfig?.conectado ? 'Mi Campus' : 'Campus'"></span>
          <span x-show="miCampusConfig?.token_estado?.estado === 'por_caducar' || miCampusConfig?.token_estado?.estado === 'caducado'"
                class="absolute -top-1 -right-1 flex h-4 w-4 items-center justify-center rounded-full text-[9px] font-black text-white shadow-md"
                :class="miCampusConfig?.token_estado?.estado === 'caducado' ? 'bg-rose-600' : 'bg-amber-500'">
            !
          </span>
        </button>

        <!-- BOTÓN MI KEY / TOKENS PRIVADOS (BYOK) -->
        <button @click="abrirModalApiKey()" 
                :class="(userGeminiKey || userOpenrouterKey || userGroqKey) ? 'bg-emerald-500/15 border-emerald-500/40 text-emerald-300' : 'bg-gray-800 border-gray-700 text-gray-300 hover:text-cyan-300 hover:border-cyan-500/30'"
                class="p-2 sm:px-3 sm:py-2 rounded-xl text-xs font-medium border transition flex items-center space-x-1.5 shrink-0 shadow-sm"
                :title="(userGeminiKey || userOpenrouterKey || userGroqKey) ? 'Claves de IA configuradas (Gemini, OpenRouter, Groq)' : 'Configurar claves gratuitas de IA (Gemini, OpenRouter, Groq)'">
          <span class="h-2 w-2 rounded-full" :class="(userGeminiKey || userOpenrouterKey || userGroqKey) ? 'bg-emerald-400 animate-pulse' : 'bg-amber-400'"></span>
          <i class="fa-solid fa-key text-[10px]" :class="(userGeminiKey || userOpenrouterKey || userGroqKey) ? 'text-emerald-400' : 'text-amber-400'"></i>
          <span class="hidden sm:inline font-medium" x-text="(userGeminiKey || userOpenrouterKey || userGroqKey) ? 'Claves IA' : 'Claves IA'"></span>
        </button>

        <div class="relative shrink-0" @click.outside="menuTemaAbierto = false" @keydown.escape.window="menuTemaAbierto = false">
          <button @click="menuTemaAbierto = !menuTemaAbierto"
                  :class="menuTemaAbierto ? 'bg-cyan-500/20 border-cyan-500/40 text-cyan-300' : 'bg-gray-800 border-gray-700 text-gray-300 hover:text-cyan-300 hover:border-cyan-500/30'"
                  class="p-2 sm:px-3 sm:py-2 rounded-xl text-xs font-medium border transition flex items-center space-x-1.5 shadow-sm"
                  title="Cambiar el tema de colores de la web">
            <i class="fa-solid fa-palette text-[11px]"></i>
            <span class="hidden lg:inline font-medium">Tema</span>
          </button>
          <div x-show="menuTemaAbierto" x-transition.opacity.duration.150ms style="display: none;"
               class="absolute right-0 mt-2 w-72 p-3 rounded-2xl border border-gray-700 bg-gray-900 shadow-2xl shadow-black/60 z-[60]">
            <div class="flex items-center justify-between mb-2 px-1">
              <p class="text-xs font-extrabold text-white flex items-center gap-1.5"><i class="fa-solid fa-palette text-cyan-400"></i> Tema de colores</p>
              <span class="text-[9px] text-gray-500">Se guarda en tu navegador</span>
            </div>
            <div class="space-y-1">
              <template x-for="t in temasDisponibles" :key="t.id">
                <button @click="cambiarTema(t.id)"
                        :class="temaActual === t.id ? 'border-cyan-500/50 bg-cyan-500/10' : 'border-transparent hover:bg-gray-800/70'"
                        class="w-full flex items-center gap-3 px-2.5 py-2 rounded-xl border transition text-left">
                  <span class="flex shrink-0 rounded-lg overflow-hidden border border-white/10 h-7 w-12">
                    <template x-for="c in previewTema(t)">
                      <span class="flex-1 h-full" :style="'background:' + c"></span>
                    </template>
                  </span>
                  <span class="min-w-0 flex-1">
                    <span class="block text-xs font-bold text-gray-100 truncate" x-text="t.nombre"></span>
                    <span class="block text-[10px] text-gray-400 truncate" x-text="t.desc"></span>
                  </span>
                  <i x-show="temaActual === t.id" class="fa-solid fa-circle-check text-cyan-400 text-sm"></i>
                </button>
              </template>
            </div>
            <p class="text-[9px] text-gray-500 mt-2 px-1 leading-snug">Los colores de estado (rojo = entrega, verde = hecho, ámbar = pendiente) se mantienen en todos los temas.</p>
          </div>
        </div>

        <!-- USUARIO ACTIVO Y BOTÓN CERRAR SESIÓN -->
        <div x-show="usuarioActual && usuarioActual.autenticado" class="flex items-center space-x-2 pl-2 sm:pl-3 border-l border-gray-800 shrink-0">
          <div class="text-right hidden sm:block">
            <p class="text-xs font-bold text-gray-200 truncate max-w-[120px]" x-text="usuarioActual?.nombre || usuarioActual?.username"></p>
            <span class="text-[9px] uppercase tracking-wider font-extrabold px-1.5 py-0.5 rounded-full inline-block"
                  :class="usuarioActual?.es_admin ? 'bg-amber-500/20 text-amber-300 border border-amber-500/30' : 'bg-cyan-500/20 text-cyan-300 border border-cyan-500/30'"
                  x-text="usuarioActual?.es_admin ? 'Admin' : 'Estudiante'"></span>
          </div>
          <button @click="cerrarSesionUsuario()" title="Cerrar sesión" class="p-2 text-gray-400 hover:text-rose-400 hover:bg-rose-500/10 rounded-xl transition">
            <i class="fa-solid fa-arrow-right-from-bracket text-xs"></i>
          </button>
        </div>
      </div>
    </header>

    <!-- CUERPO DE LA APLICACIÓN -->
    <main class="flex-1 p-3 sm:p-5 lg:p-6 space-y-4 sm:space-y-6 overflow-y-auto max-w-[1680px] w-full mx-auto pb-24 md:pb-8">

      <!-- ==================== 1. VISTA DASHBOARD ==================== -->
      <div x-show="tab === 'dashboard' && !asigSeleccionada" x-transition class="space-y-6">
        
        <!-- HERO BANNER DEL PROGRAMA ACTIVO -->
        <div class="glass p-4 sm:p-6 rounded-2xl sm:rounded-3xl relative overflow-hidden border border-gray-800">
          <div class="flex flex-col md:flex-row md:items-center md:justify-between gap-4 sm:gap-6 relative z-10">
            <div class="flex items-center space-x-3 sm:space-x-5">
              <div class="w-12 h-12 sm:w-16 sm:h-16 rounded-2xl flex items-center justify-center text-2xl sm:text-3xl shadow-xl shrink-0"
                   :style="'background: ' + obtenerFondoHex(programaActual?.color) + '; color: ' + obtenerColorHex(programaActual?.color)">
                <i :class="'fa-solid ' + (programaActual?.icono || 'fa-graduation-cap')"></i>
              </div>
              <div class="min-w-0 flex-1">
                <div class="flex flex-wrap items-center gap-2">
                  <h2 class="text-lg sm:text-2xl font-black text-white truncate" x-text="programaActual?.nombre"></h2>
                  <span class="text-[10px] sm:text-xs px-2.5 py-0.5 rounded-full font-bold border shrink-0"
                        :style="'background: ' + obtenerFondoHex(programaActual?.color) + '; color: ' + obtenerColorHex(programaActual?.color) + '; border-color: ' + obtenerColorHex(programaActual?.color) + '40'"
                        x-text="programaActual?.badge"></span>
                </div>
                <p class="text-xs text-gray-400 mt-1 line-clamp-2 sm:line-clamp-none max-w-2xl" x-text="programaActual?.descripcion"></p>
              </div>
            </div>

            <!-- ACCIONES RÁPIDAS (SOLO CANVAS DIRECTO, SIN SATURAR LA CABECERA) -->
            <div class="flex flex-wrap items-center gap-2 pt-2 md:pt-0 border-t md:border-t-0 border-gray-800/80">
              <div class="flex items-center space-x-2">
                <!-- BOTÓN IMPORTAR RESPALDO LOCAL: VISIBLE ÚNICAMENTE PARA ALEX / ADMINISTRADOR -->
                <button x-show="usuarioActual?.es_admin"
                        @click="sincronizarRespaldoLocal()" :disabled="sincronizandoRespaldo"
                        class="px-2.5 sm:px-3.5 py-1.5 rounded-xl text-xs font-bold bg-emerald-500/15 text-emerald-300 border border-emerald-500/30 hover:bg-emerald-500/25 transition flex items-center space-x-1.5 shadow-sm"
                        :title="'Sincronizar carpetas locales de estudio (/home/alexllorca/Respaldo_Estudios)'">
                  <i :class="sincronizandoRespaldo ? 'fa-solid fa-spinner fa-spin' : 'fa-solid fa-folder-tree'"></i>
                  <span class="hidden sm:inline" x-text="sincronizandoRespaldo ? 'Importando...' : ('📥 Importar Respaldo ' + (programaActivoId === 'dam' ? 'DAM' : 'Local'))"></span>
                  <span class="sm:hidden" x-text="sincronizandoRespaldo ? '...' : '📥 Respaldo'"></span>
                </button>
                <button @click="descargarCanvasOnline()" :disabled="descargandoCanvas"
                        class="px-2.5 sm:px-3.5 py-1.5 rounded-xl text-xs font-bold bg-cyan-500/15 text-cyan-300 border border-cyan-500/30 hover:bg-cyan-500/25 transition flex items-center space-x-1.5 shadow-sm"
                        title="Sincronización de Agenda y PACs con Canvas">
                  <i :class="descargandoCanvas ? 'fa-solid fa-spinner fa-spin' : 'fa-solid fa-cloud-arrow-down'"></i>
                  <span x-text="descargandoCanvas ? '...' : '🌐 Canvas'"></span>
                </button>
              </div>
            </div>
          </div>
        </div>

        <!-- BANNER DE ALERTA: TOKEN DE CANVAS POR CADUCAR O CADUCADO -->
        <template x-if="miCampusConfig?.conectado && (miCampusConfig?.token_estado?.estado === 'por_caducar' || miCampusConfig?.token_estado?.estado === 'caducado')">
          <div class="p-4 rounded-2xl border transition shadow-xl flex flex-col md:flex-row md:items-center justify-between gap-4"
               :class="miCampusConfig.token_estado.estado === 'caducado' ? 'bg-rose-950/40 border-rose-500/50 text-rose-200 shadow-rose-950/20' : 'bg-amber-950/40 border-amber-500/50 text-amber-200 shadow-amber-950/20'">
            <div class="flex items-start sm:items-center gap-3.5">
              <div class="w-11 h-11 rounded-2xl flex items-center justify-center shrink-0 text-xl shadow-md"
                   :class="miCampusConfig.token_estado.estado === 'caducado' ? 'bg-rose-500/20 text-rose-400 border border-rose-500/40' : 'bg-amber-500/20 text-amber-400 border border-amber-500/40 animate-pulse'">
                <i :class="miCampusConfig.token_estado.estado === 'caducado' ? 'fa-solid fa-triangle-exclamation' : 'fa-solid fa-clock-rotate-left'"></i>
              </div>
              <div>
                <h4 class="text-sm font-extrabold text-white flex items-center gap-2">
                  <span x-text="miCampusConfig.token_estado.estado === 'caducado' ? '⛔ Tu Token de Canvas UNIR ha caducado' : ('⚠️ Tu Token de Canvas UNIR está por caducar (' + (miCampusConfig.token_estado.dias_restantes ?? 0) + ' días restantes)')"></span>
                </h4>
                <p class="text-xs text-gray-300 mt-1">
                  <span x-text="miCampusConfig.token_estado.estado === 'caducado' ? 'Tus PDFs descargados están seguros, pero necesitas renovar el token para seguir sincronizando notas, PACs y temas.' : 'Renuévalo ahora en 1 minuto para no perder la sincronización nocturna ni la descarga de nuevos materiales de estudio.'"></span>
                </p>
              </div>
            </div>
            <div class="flex items-center gap-2 shrink-0">
              <button @click="abrirModalMiCampus()"
                      class="w-full sm:w-auto px-4 py-2.5 rounded-xl text-xs font-black transition shadow-lg flex items-center justify-center gap-2"
                      :class="miCampusConfig.token_estado.estado === 'caducado' ? 'bg-rose-600 hover:bg-rose-500 text-white' : 'bg-amber-500 hover:bg-amber-400 text-black'">
                <i class="fa-solid fa-key"></i>
                <span>Renovar Token de Canvas</span>
              </button>
            </div>
          </div>
        </template>

        <!-- WORKSPACE ORGANIZADOR ESTILO NOTION (TO-DO HOY + TO-DO SEMANA + CALENDARIO CON TARJETAS DE ESTADO) -->
        <div class="grid grid-cols-1 xl:grid-cols-12 gap-5 items-start">

          <!-- COLUMNA IZQUIERDA (3 COLS): TO-DO HOY & TO-DO SEMANA (COMO EN EL VÍDEO DE NOTION) -->
          <div class="xl:col-span-3 flex flex-col gap-4">

            <!-- 1. TO-DO HOY -->
            <div class="glass-card p-4 rounded-2xl border border-sky-500/30 bg-surface-2/95 flex flex-col">
              <div class="flex items-center justify-between pb-2.5 mb-2.5 border-b border-gray-800">
                <div class="flex items-center space-x-2">
                  <span class="w-2.5 h-2.5 rounded-full bg-sky-400"></span>
                  <h3 class="text-xs sm:text-sm font-extrabold text-sky-300">To-do hoy</h3>
                  <span class="text-[10px] px-1.5 py-0.5 rounded-full bg-sky-500/20 text-sky-300 font-mono font-bold" x-text="todoHoyLista.length"></span>
                </div>
              </div>
              <p class="text-[10px] text-gray-400 mb-2">Eventos y citas de hoy + tareas en proceso</p>

              <div x-show="todoHoyLista.length === 0" class="py-5 text-center text-[11px] text-gray-500">
                <i class="fa-solid fa-check-double text-emerald-400/60 text-base mb-1 block"></i>
                ¡Todo al día por hoy! Usa «+ Añadir al Calendario» para crear una tarea, tema o cita.
              </div>

              <div x-show="todoHoyLista.length > 0" class="space-y-1.5 max-h-[230px] overflow-y-auto pr-1">
                <template x-for="(ev, idx) in todoHoyLista" :key="'hoy-' + (ev.id || 'ev') + '-' + idx">
                  <div @click="abrirModalItemAgenda(ev)"
                       draggable="true"
                       @dragstart="iniciarArrastreItem($event, ev)"
                       @dragend="finalizarArrastreItem()"
                       class="p-2.5 rounded-xl border cursor-grab active:cursor-grabbing transition flex items-center justify-between gap-2 group"
                       :class="claseColorTarjetaNotion(ev)">
                    <div class="min-w-0 flex-1">
                      <div class="flex items-center gap-1.5 flex-wrap">
                        <span class="text-xs font-extrabold text-white break-words" x-text="ev.titulo"></span>
                        <span x-show="ev.hora_inicio && ev.hora_inicio !== '23:59'" class="text-[9px] font-mono text-amber-200 bg-black/30 px-1 rounded" x-text="ev.hora_inicio"></span>
                        <span class="text-[9px] px-1.5 py-0.2 rounded font-bold shrink-0" :class="claseBadgeEstadoNotion(ev.estado)" x-text="ev.estado"></span>
                      </div>
                      <p class="text-[10px] text-gray-200 font-medium leading-snug mt-0.5 flex items-center gap-1 hover:text-cyan-300 cursor-pointer group/asig transition"
                         @click.stop="abrirAsignaturaPorNombre(ev.asignatura)"
                         :title="'Abrir ' + ev.asignatura">
                        <span x-text="iconoAsignatura(ev.asignatura)" class="text-[10px] shrink-0"></span>
                        <span class="break-words group-hover/asig:underline" x-text="ev.asignatura"></span>
                        <i class="fa-solid fa-arrow-up-right-from-square text-[8px] opacity-0 group-hover/asig:opacity-100 transition-opacity ml-0.5 shrink-0"></i>
                      </p>
                    </div>
                    <div class="flex items-center gap-1 shrink-0" @click.stop="">
                      <button @click="moverItemDias(ev, -1)" title="Pasar al día anterior (-1 día)" class="px-1.5 py-1 rounded bg-black/30 hover:bg-black/60 text-[9px] text-gray-300 hover:text-white">◀</button>
                      <button @click="moverItemDias(ev, 1)" title="Pasar al día siguiente (+1 día)" class="px-1.5 py-1 rounded bg-black/30 hover:bg-black/60 text-[9px] text-gray-300 hover:text-white">▶</button>
                      <button @click="marcarItemRapidoHecho(ev)"
                              title="Marcar como Vista / Hecho"
                              class="w-6 h-6 rounded-lg bg-black/30 hover:bg-emerald-500/30 text-gray-300 hover:text-emerald-300 flex items-center justify-center text-[10px] transition">
                        <i class="fa-solid fa-check"></i>
                      </button>
                    </div>
                  </div>
                </template>
              </div>
            </div>

            <!-- 2. TO-DO SEMANA (Y PENDIENTES DE SEMANAS ANTERIORES) -->
            <div class="glass-card p-4 rounded-2xl border border-indigo-500/30 bg-surface-2/95 flex-1 flex flex-col">
              <div class="flex items-center justify-between pb-2 mb-1.5 border-b border-gray-800 gap-1">
                <div class="flex items-center space-x-2">
                  <span class="w-2.5 h-2.5 rounded-full bg-indigo-400"></span>
                  <h3 class="text-xs sm:text-sm font-extrabold text-indigo-300">To-do semana</h3>
                  <span class="text-[10px] px-1.5 py-0.5 rounded-full bg-indigo-500/20 text-indigo-300 font-mono font-bold" x-text="todoSemanaLista.length"></span>
                </div>
                <button @click="incluirAtrasadosTodo = !incluirAtrasadosTodo"
                        :class="incluirAtrasadosTodo ? 'bg-rose-500/20 text-rose-300 border-rose-500/40' : 'bg-gray-900 text-gray-400 border-gray-800 hover:text-gray-200'"
                        class="px-2 py-0.5 rounded-lg border text-[10px] font-bold transition"
                        title="Incluir clases o temas de semanas anteriores que aún no hayas marcado como vistos/hechos">
                  <span x-text="incluirAtrasadosTodo ? 'Ocultar anteriores' : '+ Pendientes anteriores'"></span>
                </button>
              </div>
              <p class="text-[10px] text-gray-400 mb-2">
                <span x-show="!incluirAtrasadosTodo">Clases, entregas, citas y temas pendientes de esta semana</span>
                <span x-show="incluirAtrasadosTodo" class="text-rose-300/90">Mostrando esta semana + pendientes de semanas pasadas sin marcar</span>
              </p>

              <div x-show="todoSemanaLista.length === 0" class="py-6 text-center text-[11px] text-gray-500 my-auto">
                Sin tareas ni clases pendientes esta semana.
              </div>

              <div x-show="todoSemanaLista.length > 0" class="space-y-1.5 max-h-[340px] overflow-y-auto pr-1">
                <template x-for="(ev, idx) in todoSemanaLista" :key="'sem-' + (ev.id || 'ev') + '-' + idx">
                  <div @click="abrirModalItemAgenda(ev)"
                       draggable="true"
                       @dragstart="iniciarArrastreItem($event, ev)"
                       @dragend="finalizarArrastreItem()"
                       class="p-2.5 rounded-xl border cursor-grab active:cursor-grabbing transition flex items-center justify-between gap-2 group"
                       :class="claseColorTarjetaNotion(ev)">
                    <div class="min-w-0 flex-1">
                      <div class="flex items-center justify-between gap-1">
                        <div class="flex items-center gap-1 min-w-0">
                          <span class="text-xs font-extrabold text-white truncate" x-text="ev.titulo"></span>
                        </div>
                        <span class="text-[9px] text-gray-300 font-mono shrink-0" x-text="formatearFechaDDMMAAAA(ev.fecha_dia || ev.fecha_iso)"></span>
                      </div>
                      <p class="text-[10px] text-gray-200 font-medium leading-snug mt-0.5 flex items-center gap-1 hover:text-cyan-300 cursor-pointer group/asig transition"
                         @click.stop="abrirAsignaturaPorNombre(ev.asignatura)"
                         :title="'Abrir ' + ev.asignatura">
                        <span x-text="iconoAsignatura(ev.asignatura)" class="text-[10px] shrink-0"></span>
                        <span class="break-words group-hover/asig:underline" x-text="ev.asignatura"></span>
                        <i class="fa-solid fa-arrow-up-right-from-square text-[8px] opacity-0 group-hover/asig:opacity-100 transition-opacity ml-0.5 shrink-0"></i>
                      </p>
                      <div class="flex items-center justify-between gap-1 mt-1">
                        <span class="text-[9px] px-1.5 py-0.2 rounded font-bold shrink-0" :class="claseBadgeEstadoNotion(ev.estado)" x-text="ev.estado"></span>
                        <div class="flex items-center gap-0.5 opacity-80 group-hover:opacity-100" @click.stop="">
                          <button @click="moverItemDias(ev, -1)" title="Pasar al día anterior (-1 día)" class="px-1.5 py-0.5 rounded bg-black/30 hover:bg-black/60 text-[9px] text-gray-300 hover:text-white">◀</button>
                          <button @click="moverItemDias(ev, 1)" title="Pasar al día siguiente (+1 día)" class="px-1.5 py-0.5 rounded bg-black/30 hover:bg-black/60 text-[9px] text-gray-300 hover:text-white">▶</button>
                        </div>
                      </div>
                    </div>
                    <div class="flex flex-col items-center gap-1 shrink-0" @click.stop="">
                      <button @click="marcarItemRapidoHecho(ev)"
                              title="Marcar Vista / Hecho"
                              class="w-6 h-6 rounded-lg bg-black/30 hover:bg-emerald-500/30 text-gray-300 hover:text-emerald-300 flex items-center justify-center text-[10px] transition">
                        <i class="fa-solid fa-check"></i>
                      </button>
                      <button x-show="ev.categoria === 'Clase'"
                              @click="cambiarEstadoItemDirecto(ev, 'Vista no relevante')"
                              title="Marcar como Vista no relevante"
                              class="w-6 h-6 rounded-lg bg-black/30 hover:bg-amber-500/30 text-gray-400 hover:text-amber-300 flex items-center justify-center text-[10px] transition">
                        <i class="fa-solid fa-eye-slash"></i>
                      </button>
                    </div>
                  </div>
                </template>
              </div>
            </div>

          </div>

          <!-- COLUMNA DERECHA (9 COLS): CALENDARIO INTERACTIVO CON TARJETAS DE ESTADO (SEMANAL / MENSUAL) -->
          <div class="xl:col-span-9 glass-card p-4 sm:p-5 rounded-2xl sm:rounded-3xl border border-gray-800/90 bg-surface-1/95 flex flex-col justify-start space-y-4">
            <!-- CABECERA DEL CALENDARIO (TODO EN LÍNEA / RESPONSIVE) -->
            <div class="flex flex-col md:flex-row md:items-center md:justify-between gap-3 pb-3.5 border-b border-gray-800">
              <div class="min-w-0">
                <div class="flex flex-wrap items-center gap-2">
                  <div class="flex items-center gap-2">
                    <i class="fa-solid fa-calendar-days text-cyan-400 text-base"></i>
                    <h3 class="text-sm sm:text-base font-extrabold text-white capitalize whitespace-nowrap" x-text="nombreMesCalendario"></h3>
                  </div>
                  <button @click="verEntregasDelMes()"
                          class="text-[10px] px-2.5 py-0.5 rounded-full bg-rose-500/20 hover:bg-rose-500/30 text-rose-300 border border-rose-500/40 hover:border-rose-500/60 font-bold whitespace-nowrap transition cursor-pointer flex items-center gap-1.5 shadow-sm active:scale-95 group"
                          :title="'Ver las ' + entregasMesActual.length + ' entregas de ' + nombreMesCalendario">
                    <span class="w-1.5 h-1.5 rounded-full bg-rose-400 group-hover:animate-ping"></span>
                    <span x-text="entregasMesActual.length + (entregasMesActual.length === 1 ? ' entrega este mes' : ' entregas este mes')"></span>
                    <i class="fa-solid fa-arrow-up-right-from-square text-[8px] opacity-70 group-hover:opacity-100"></i>
                  </button>
                  <button @click="abrirModalNuevoItemAgenda('Tema', '', '', 'calendario')"
                          class="px-2.5 sm:px-3 py-1 rounded-xl bg-cyan-500/20 hover:bg-cyan-500/30 border border-cyan-500/40 text-cyan-300 text-[10px] sm:text-[11px] font-bold transition flex items-center gap-1.5 whitespace-nowrap">
                    <i class="fa-solid fa-plus text-[9px]"></i>
                    <span class="hidden sm:inline">Añadir al Calendario</span>
                    <span class="sm:hidden">Añadir</span>
                  </button>
                </div>
                <p class="text-[10px] text-gray-400 mt-1 hidden sm:block">💡 Arrastra cualquier etiqueta a otro día, usa el botón «+» de cada día o haz clic para editarla</p>
              </div>

              <!-- FILTROS POR TIPO + SELECTOR DE VISTA + NAVEGACIÓN DE SEMANA/MES -->
              <div class="flex flex-wrap items-center gap-2 w-full md:w-auto">
                <!-- PÍLDORAS DE TIPO (CLASE, ACTIVIDAD, TEMA, TEST) -->
                <div class="flex items-center bg-gray-950/90 p-1 rounded-xl border border-gray-800 text-[10px] sm:text-[11px] overflow-x-auto no-scrollbar shrink-0">
                  <button @click="calFiltroTipo = 'todos'"
                          :class="calFiltroTipo === 'todos' ? 'bg-gray-800 text-white font-bold' : 'text-gray-400 hover:text-gray-200'"
                          class="px-2 py-1 rounded-lg transition">Todo</button>
                  <button @click="calFiltroTipo = 'clase_directo'"
                          :class="calFiltroTipo === 'clase_directo' ? 'bg-sky-500/25 text-sky-300 font-bold border border-sky-500/40' : 'text-gray-400 hover:text-sky-300'"
                          class="px-2 py-1 rounded-lg transition">Clases</button>
                  <button @click="calFiltroTipo = 'tarea'"
                          :class="calFiltroTipo === 'tarea' ? 'bg-rose-500/25 text-rose-300 font-bold border border-rose-500/40' : 'text-gray-400 hover:text-rose-300'"
                          class="px-2 py-1 rounded-lg transition">Entregas</button>
                  <button @click="calFiltroTipo = 'tema'"
                          :class="calFiltroTipo === 'tema' ? 'bg-amber-500/25 text-amber-300 font-bold border border-amber-500/40' : 'text-gray-400 hover:text-amber-300'"
                          class="px-2 py-1 rounded-lg transition whitespace-nowrap">Temas / Citas</button>
                </div>


                <!-- VISTA: 2 SEMANAS (DETALLE NOTION) vs MES COMPLETO -->
                <div class="flex items-center bg-gray-950/90 p-1 rounded-xl border border-gray-800 text-[10px] sm:text-[11px] shrink-0">
                  <button @click="calModoVista = 'quincena'"
                          :class="calModoVista === 'quincena' ? 'bg-cyan-500/20 text-cyan-300 font-bold' : 'text-gray-400 hover:text-white'"
                          class="px-2.5 py-1 rounded-lg transition whitespace-nowrap">Lun-Vie (Detalle)</button>
                  <button @click="calModoVista = 'mes'"
                          :class="calModoVista === 'mes' ? 'bg-cyan-500/20 text-cyan-300 font-bold' : 'text-gray-400 hover:text-white'"
                          class="px-2.5 py-1 rounded-lg transition">Mes</button>
                </div>

                <!-- NAVEGACIÓN < RANGO ACTUAL / SEMANA / MES > EN LÍNEA -->
                <div class="flex items-center space-x-1 bg-gray-950/90 p-1 rounded-xl border border-gray-800 shrink-0">
                  <button @click="navegarCalendario(-1)" title="Anterior" class="p-1 px-2 rounded-lg text-gray-400 hover:text-white hover:bg-gray-800 transition text-xs">
                    <i class="fa-solid fa-chevron-left"></i>
                  </button>
                  <button @click="irAHoyCalendario()"
                          :title="esPeriodoActualCalendario ? 'Estás viendo el periodo actual' : 'Pulsa para volver a Hoy'"
                          :class="esPeriodoActualCalendario ? 'text-cyan-400' : 'text-amber-300 bg-amber-500/15 border border-amber-500/30'"
                          class="px-2.5 py-1 rounded-lg text-[11px] font-bold hover:bg-cyan-500/10 transition whitespace-nowrap">
                    <span x-text="etiquetaPeriodoCalendario"></span>
                  </button>
                  <button @click="navegarCalendario(1)" title="Siguiente" class="p-1 px-2 rounded-lg text-gray-400 hover:text-white hover:bg-gray-800 transition text-xs">
                    <i class="fa-solid fa-chevron-right"></i>
                  </button>
                </div>
              </div>
            </div>

            <!-- REJILLA DEL CALENDARIO ESTILO NOTION (CON SOPORTE DRAG & DROP ENTRE DÍAS) -->
            <div class="mt-3 overflow-x-auto no-scrollbar">
              <div :class="calModoVista === 'mes' ? 'min-w-[580px] sm:min-w-0' : ''">
                <!-- CABECERA DÍAS DE LA SEMANA (SOLO EN ESCRITORIO PARA QUINCENA O EN MES) -->
                <div class="gap-1.5 text-center text-[10px] sm:text-[11px] font-bold lowercase tracking-wider text-gray-400 mb-1.5"
                     :class="calModoVista === 'quincena' ? 'hidden sm:grid grid-cols-5' : 'grid grid-cols-7'">
                  <div>lun</div><div>mar</div><div>mié</div><div>jue</div><div>vie</div>
                  <template x-if="calModoVista === 'mes'"><div>sáb</div></template>
                  <template x-if="calModoVista === 'mes'"><div>dom</div></template>
                </div>

                <!-- CELDAS CON TARJETAS DE COLORES ESTILO NOTION -->
                <div class="grid gap-1.5" :class="calModoVista === 'quincena' ? 'grid-cols-1 sm:grid-cols-5' : 'grid-cols-7'">
                  <template x-for="celda in celdasCalendarioVisibles" :key="celda.key">
                    <div @dragover.prevent="if (celda.fechaStr) diaArrastreSobre = celda.fechaStr"
                         @dragleave.prevent="if ($event.relatedTarget && !$el.contains($event.relatedTarget)) diaArrastreSobre = ''"
                         @drop.prevent="soltarItemEnDia(celda.fechaStr)"
                         :class="[
                           !celda.esMesActual && calModoVista === 'mes' ? 'opacity-30 bg-gray-950/20 border-gray-900' : 'bg-surface-4 border-gray-800/90',
                           celda.esHoy ? 'ring-2 ring-rose-500/80 border-rose-500' : '',
                           celda.tieneEntregaRoja ? 'border-rose-500/60' : '',
                           diaArrastreSobre === celda.fechaStr && celda.fechaStr ? 'ring-2 ring-cyan-400 bg-cyan-950/60 border-cyan-400 scale-[1.01] shadow-lg shadow-cyan-500/20' : ''
                         ]"
                         class="min-h-[110px] sm:min-h-[165px] p-2 sm:p-1.5 rounded-xl border flex flex-col justify-between transition-all duration-150">
                      <div>
                        <!-- NÚMERO DE DÍA + DÍA SEMANA EN MÓVIL + BOTÓN + -->
                        <div class="flex items-center justify-between mb-1.5 px-0.5 group/day">
                          <button @click="abrirModalNuevoItemAgenda('Tema', '', celda.fechaStr, 'calendario')"
                                  x-show="celda.fechaStr"
                                  title="Añadir tema, tarea o cita en este día"
                                  class="opacity-70 group-hover/day:opacity-100 w-5 h-5 rounded bg-gray-800 hover:bg-cyan-500 hover:text-black text-gray-300 flex items-center justify-center text-[10px] transition">
                            <i class="fa-solid fa-plus"></i>
                          </button>
                          <div class="flex items-center gap-1.5 ml-auto">
                            <span x-show="calModoVista === 'quincena'" class="sm:hidden text-xs font-bold text-cyan-300 capitalize" x-text="nombreDiaDeFecha(celda.fechaStr)"></span>
                            <span class="text-[11px] font-bold px-2 py-0.5 rounded-full"
                                  :class="celda.esHoy ? 'bg-rose-500 text-white font-extrabold' : 'bg-gray-800/80 text-gray-300'"
                                  x-text="celda.dia"></span>
                          </div>
                        </div>

                      <!-- LISTA DE TARJETAS NOTION DENTRO DEL DÍA -->
                      <div class="space-y-1.5 max-h-[250px] overflow-y-auto pr-0.5">
                        <template x-for="(ev, idx) in celda.eventos" :key="'cal-' + (ev.id || 'ev') + '-' + idx">
                          <div @click="abrirModalItemAgenda(ev)"
                               draggable="true"
                               @dragstart="iniciarArrastreItem($event, ev)"
                               @dragend="finalizarArrastreItem()"
                               title="Arrastra a otro día para moverlo o haz clic para editar nombre, asignatura, fecha y estado"
                               class="p-2 rounded-lg border cursor-grab active:cursor-grabbing transition-all duration-150 hover:brightness-110 text-left space-y-1 shadow-sm group/card"
                               :class="[
                                 claseColorTarjetaNotion(ev),
                                 itemArrastrando && itemArrastrando.id === ev.id ? 'opacity-30 scale-95 border-dashed border-cyan-400' : 'hover:scale-[1.01]'
                               ]">
                            <div class="flex items-start justify-between gap-1">
                              <p class="text-[11px] font-extrabold text-white leading-tight break-words flex items-center gap-1">
                                <span x-text="ev.titulo"></span>
                              </p>
                              <span x-show="ev.hora_inicio && ev.hora_inicio !== '23:59'"
                                    class="text-[9px] text-gray-200 font-mono shrink-0 bg-black/25 px-1 rounded"
                                    x-text="ev.hora_inicio"></span>
                            </div>
                            <div class="flex items-start gap-1 text-[10px] text-gray-200/95 leading-snug hover:text-cyan-300 cursor-pointer group/asig transition"
                                 @click.stop="abrirAsignaturaPorNombre(ev.asignatura)"
                                 :title="'Abrir ' + ev.asignatura">
                              <span x-text="iconoAsignatura(ev.asignatura)" class="text-[10px] shrink-0"></span>
                              <span class="break-words font-medium group-hover/asig:underline" x-text="ev.asignatura"></span>
                              <i class="fa-solid fa-arrow-up-right-from-square text-[8px] opacity-0 group-hover/asig:opacity-100 transition-opacity ml-0.5 mt-0.5 shrink-0"></i>
                            </div>
                            <div class="flex items-center justify-between gap-1 pt-0.5">
                              <span class="text-[9px] px-1.5 py-0.2 rounded font-bold inline-block"
                                    :class="claseBadgeEstadoNotion(ev.estado)"
                                    x-text="ev.estado"></span>
                              <div class="flex items-center gap-1">
                                <span x-show="ev.nota_personal !== null && ev.nota_personal !== undefined"
                                      class="text-[9px] font-mono font-bold text-emerald-300"
                                      x-text="ev.nota_personal + '/10'"></span>
                                <!-- Botones rápidos para pasar de un día a otro en 1 clic -->
                                <div class="flex items-center gap-0.5 opacity-60 sm:opacity-0 group-hover/card:opacity-100 transition-opacity" @click.stop="">
                                  <button @click="moverItemDias(ev, -1)" title="Pasar al día anterior (-1 día)" class="w-4 h-4 rounded flex items-center justify-center bg-black/50 hover:bg-cyan-500 hover:text-black text-[9px] text-gray-300 transition active:scale-90">◀</button>
                                  <button @click="moverItemDias(ev, 1)" title="Pasar al día siguiente (+1 día)" class="w-4 h-4 rounded flex items-center justify-center bg-black/50 hover:bg-cyan-500 hover:text-black text-[9px] text-gray-300 transition active:scale-90">▶</button>
                                </div>
                              </div>
                            </div>
                          </div>
                        </template>
                      </div>
                    </div>
                  </div>
                </template>
              </div>
            </div>
          </div>

            <!-- BARRA INFERIOR DE PRÓXIMAS ENTREGAS EN ROJO -->
            <div x-show="proximasEntregasRojas.length > 0" class="mt-4 pt-3 border-t border-gray-800 space-y-2">
              <div class="flex flex-wrap items-center justify-between gap-2">
                <div class="flex items-center gap-2 text-xs font-extrabold text-rose-400">
                  <span class="w-2 h-2 rounded-full bg-rose-500 animate-ping"></span>
                  <span>Próximas entregas PAC en rojo:</span>
                </div>
                <div class="flex flex-wrap items-center gap-2">
                  <template x-for="(ev, idx) in proximasEntregasRojas.slice(0, 4)" :key="'red-' + (ev.id || 'ev') + '-' + idx">
                    <button @click="abrirModalItemAgenda(ev)"
                            class="px-2.5 py-1 rounded-xl bg-rose-500/20 hover:bg-rose-500/30 border border-rose-500/50 text-rose-200 text-[11px] font-bold transition flex items-center gap-1.5">
                      <span x-text="ev.asignatura + ': ' + ev.titulo"></span>
                      <span class="px-1.5 py-0.2 rounded bg-rose-500 text-white text-[9px] font-extrabold" x-text="ev.plazo_texto"></span>
                    </button>
                  </template>
                </div>
              </div>
            </div>
          </div>

        </div>

        <!-- ACCESO RÁPIDO: CONTINUAR DONDE LO DEJÉ -->
        <div x-show="actividadReciente.length > 0" class="glass p-4 sm:p-5 rounded-2xl sm:rounded-3xl border border-cyan-500/20">
          <div class="flex items-center justify-between mb-3">
            <div class="flex items-center space-x-2">
              <i class="fa-solid fa-clock-rotate-left text-cyan-400 text-xs"></i>
              <h3 class="text-xs sm:text-sm font-bold text-white">Continuar donde lo dejé</h3>
            </div>
            <button @click="limpiarActividadReciente()" class="text-[10px] text-gray-500 hover:text-gray-300 transition">Limpiar historial</button>
          </div>
          <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-2.5">
            <template x-for="item in actividadReciente.slice(0, 4)" :key="item.id">
              <div @click="abrirItemReciente(item)" class="p-3 rounded-xl bg-gray-900/70 border border-gray-800 hover:border-cyan-500/40 cursor-pointer transition flex items-center space-x-3 group">
                <div class="w-8 h-8 rounded-lg flex items-center justify-center shrink-0 text-xs"
                     :class="item.tipo === 'pdf' ? 'bg-rose-500/15 text-rose-400' : (item.tipo === 'guia' ? 'bg-cyan-500/15 text-cyan-400' : 'bg-emerald-500/15 text-emerald-400')">
                  <i :class="item.tipo === 'pdf' ? 'fa-regular fa-file-pdf' : (item.tipo === 'guia' ? 'fa-solid fa-book-open' : 'fa-solid fa-bullseye')"></i>
                </div>
                <div class="min-w-0 flex-1">
                  <p class="text-[10px] text-gray-400 truncate" x-text="item.asignatura"></p>
                  <p class="text-xs font-bold text-white truncate group-hover:text-cyan-300 transition" x-text="item.titulo"></p>
                </div>
                <i class="fa-solid fa-chevron-right text-[10px] text-gray-600 group-hover:text-cyan-400"></i>
              </div>
            </template>
          </div>
        </div>

        <!-- SEMÁFORO Y CATÁLOGO DE ASIGNATURAS (ESTILO GALERÍA VISUAL CON PORTADAS) -->
        <div class="glass rounded-3xl p-6 border border-gray-800/80">
          <div class="flex flex-col lg:flex-row lg:items-center lg:justify-between gap-4 mb-6 pb-4 border-b border-gray-800/80">
            <div>
              <h3 class="text-xl font-extrabold text-white flex items-center space-x-2.5">
                <i class="fa-solid fa-layer-group text-cyan-400"></i>
                <span>Materias y Módulos de <span x-text="programaActual?.nombre"></span></span>
              </h3>
              <p class="text-xs text-gray-400 mt-1">Usa la estrella ⭐ para fijar tus asignaturas matriculadas o filtra por curso para encontrar todo al instante</p>
            </div>

            <div class="flex flex-wrap items-center gap-2">
              <!-- Buscador rápido de asignaturas -->
              <div class="relative">
                <i class="fa-solid fa-magnifying-glass text-gray-500 text-xs absolute left-3 top-1/2 -translate-y-1/2"></i>
                <input type="text"
                       x-model="busquedaTexto"
                       @input="aplicarFiltroCatalogo()"
                       placeholder="Buscar asignatura..."
                       class="bg-gray-950/90 border border-gray-800 focus:border-cyan-500/60 rounded-xl pl-8 pr-3 py-1.5 text-xs text-white placeholder-gray-500 outline-none w-44 sm:w-52 transition">
              </div>

              <!-- Filtro por Curso (Todos / 2º Curso / 1º Curso) -->
              <div x-show="programaActivoId === 'dam'" class="flex items-center bg-gray-950 p-1 rounded-xl border border-gray-800 text-xs">
                <button @click="filtrarCurso('todos')"
                        :class="cursoFiltro === 'todos' ? 'bg-gray-800 text-white font-bold shadow' : 'text-gray-400 hover:text-gray-200'"
                        class="px-2.5 py-1 rounded-lg transition">Todos</button>
                <button @click="filtrarCurso('2dam')"
                        :class="cursoFiltro === '2dam' ? 'bg-cyan-500/20 text-cyan-300 border border-cyan-500/30 font-bold' : 'text-gray-400 hover:text-gray-200'"
                        class="px-2.5 py-1 rounded-lg transition">2º Curso</button>
                <button @click="filtrarCurso('1dam')"
                        :class="cursoFiltro === '1dam' ? 'bg-indigo-500/20 text-indigo-300 border border-indigo-500/30 font-bold' : 'text-gray-400 hover:text-gray-200'"
                        class="px-2.5 py-1 rounded-lg transition">1º Curso</button>
              </div>

              <button @click="toggleSoloFavoritas()"
                      :class="mostrarSoloFavoritas ? 'bg-amber-500/20 text-amber-300 border-amber-500/40' : 'bg-gray-950 text-gray-400 border-gray-800 hover:text-gray-200'"
                      class="text-xs px-3 py-1.5 rounded-xl border font-bold transition flex items-center space-x-1.5">
                <i class="fa-solid fa-star text-[11px]" :class="mostrarSoloFavoritas ? 'text-amber-400' : 'text-gray-500'"></i>
                <span x-text="mostrarSoloFavoritas ? ('Mis Asignaturas (' + asignaturasFavoritas.length + ')') : 'Filtrar Mis Asignaturas'"></span>
              </button>
              <span class="text-xs bg-gray-950 text-gray-300 font-semibold px-3 py-1.5 rounded-xl border border-gray-800" x-text="catalogoFiltrado.length + ' materias'"></span>
            </div>
          </div>

          <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-5">
            <template x-for="asig in catalogoFiltrado" :key="asig.nombre">
              <div @click="abrirAsignatura(asig)"
                   class="group rounded-2xl overflow-hidden bg-surface-5 border border-gray-800/90 hover:border-cyan-500/50 hover:shadow-xl hover:shadow-cyan-950/30 transition-all duration-200 cursor-pointer flex flex-col justify-between relative">

                <!-- PORTADA ILUSTRADA / IMAGEN DE LA ASIGNATURA -->
                <div>
                  <div class="h-28 relative overflow-hidden">
                    <!-- Imagen temática de alta resolución de la asignatura -->
                    <img :src="temaVisualAsignatura(asig.nombre).imagen"
                         :alt="asig.nombre"
                         loading="lazy"
                         class="w-full h-full object-cover object-center group-hover:scale-105 transition-transform duration-500 opacity-65">
                    <!-- Gradiente de color propio de la asignatura para legibilidad perfecta -->
                    <div class="absolute inset-0 bg-gradient-to-t from-surface-5 via-surface-5/45 to-transparent"></div>
                    <div class="absolute inset-0 opacity-45 mix-blend-overlay" :class="temaVisualAsignatura(asig.nombre).gradiente"></div>

                    <!-- Barra superior sobre la imagen: Estrella + Curso + Estado/Nota -->
                    <div class="absolute top-3 inset-x-3 flex items-center justify-between gap-2 z-10">
                      <div class="flex items-center gap-1.5">
                        <button @click.stop="toggleAsignaturaFavorita(asig.nombre)"
                                :title="esAsignaturaFavorita(asig.nombre) ? 'Quitar de Mis Asignaturas' : 'Fijar en Mis Asignaturas'"
                                class="w-7 h-7 rounded-lg bg-gray-950/80 backdrop-blur-md border border-white/10 flex items-center justify-center hover:bg-gray-900 transition">
                          <i class="fa-star text-xs transition"
                             :class="esAsignaturaFavorita(asig.nombre) ? 'fa-solid text-amber-400' : 'fa-regular text-gray-300 hover:text-amber-300'"></i>
                        </button>
                        <span class="text-[10px] font-extrabold px-2.5 py-1 rounded-lg bg-gray-950/80 backdrop-blur-md text-gray-100 border border-white/10" x-text="asig.curso"></span>
                      </div>

                      <div class="flex items-center gap-1.5">
                        <template x-if="notaCampusDeAsignatura(asig.nombre) !== null">
                          <span class="px-2 py-0.5 rounded-lg text-[10px] font-extrabold bg-indigo-950/90 backdrop-blur-md text-indigo-200 border border-indigo-400/40"
                                x-text="'🏛️ ' + notaCampusDeAsignatura(asig.nombre) + '%'"></span>
                        </template>
                        <span class="px-2.5 py-1 rounded-lg text-[10px] font-bold backdrop-blur-md"
                              :class="asig.progreso.total_evaluados > 0 ? 'bg-emerald-950/90 text-emerald-300 border border-emerald-500/40' : 'bg-gray-950/80 text-gray-300 border border-white/10'"
                              x-text="asig.progreso.total_evaluados > 0 ? asig.progreso.estado : 'Sin evaluar'">
                        </span>
                        <a :href="'/?asig=' + encodeURIComponent(asig.nombre)" target="_blank" @click.stop="" title="Abrir en pestaña nueva" class="w-7 h-7 rounded-lg bg-gray-950/80 backdrop-blur-md text-gray-300 hover:text-cyan-300 flex items-center justify-center border border-white/10 opacity-0 group-hover:opacity-100 transition">
                          <i class="fa-solid fa-arrow-up-right-from-square text-[10px]"></i>
                        </a>
                      </div>
                    </div>

                    <!-- Icono 3D y Siglas de la Asignatura sobre el borde inferior de la portada -->
                    <div class="absolute bottom-2 left-4 right-4 flex items-end justify-between z-10">
                      <div class="flex items-center gap-2.5">
                        <div class="w-10 h-10 rounded-xl flex items-center justify-center shadow-lg border border-white/20 backdrop-blur-md text-white text-base"
                             :class="temaVisualAsignatura(asig.nombre).badgeBg">
                          <i :class="temaVisualAsignatura(asig.nombre).icono"></i>
                        </div>
                        <span class="text-[11px] font-black uppercase tracking-wider px-2 py-0.5 rounded-md border backdrop-blur-md"
                              :class="temaVisualAsignatura(asig.nombre).chipClase"
                              x-text="temaVisualAsignatura(asig.nombre).siglas"></span>
                      </div>
                    </div>
                  </div>

                  <!-- TÍTULO GRANDE Y SUBTÍTULO DESCRIPTIVO CLARO -->
                  <div class="px-5 pt-2 pb-1">
                    <h4 class="font-extrabold text-white text-base leading-snug group-hover:text-cyan-300 transition" x-text="asig.nombre"></h4>
                    <p class="text-[11px] text-gray-400 mt-0.5 line-clamp-1" x-text="temaVisualAsignatura(asig.nombre).subtitulo"></p>
                  </div>
                </div>

                <!-- CONTADORES CON ETIQUETAS CLARAS + ACCIONES RÁPIDAS -->
                <div class="px-5 pb-4 pt-3 space-y-3">
                  <div class="grid grid-cols-4 gap-1.5 text-center">
                    <div class="bg-gray-900/90 border border-gray-800/90 rounded-xl py-1.5 px-1">
                      <div class="text-xs font-extrabold text-white flex items-center justify-center gap-1">
                        <i class="fa-regular fa-file-pdf text-rose-400 text-[11px]"></i>
                        <span x-text="asig.total_materiales"></span>
                      </div>
                      <div class="text-[9px] text-gray-400 font-semibold uppercase tracking-tight">PDFs</div>
                    </div>
                    <div class="bg-gray-900/90 border border-gray-800/90 rounded-xl py-1.5 px-1">
                      <div class="text-xs font-extrabold text-white flex items-center justify-center gap-1">
                        <i class="fa-solid fa-video text-amber-400 text-[11px]"></i>
                        <span x-text="asig.total_clases"></span>
                      </div>
                      <div class="text-[9px] text-gray-400 font-semibold uppercase tracking-tight">Clases</div>
                    </div>
                    <div class="bg-gray-900/90 border border-gray-800/90 rounded-xl py-1.5 px-1">
                      <div class="text-xs font-extrabold text-white flex items-center justify-center gap-1">
                        <i class="fa-solid fa-book-open text-cyan-400 text-[11px]"></i>
                        <span x-text="(asig.guias?.length || 0)"></span>
                      </div>
                      <div class="text-[9px] text-gray-400 font-semibold uppercase tracking-tight">Guías</div>
                    </div>
                    <div class="bg-gray-900/90 border border-gray-800/90 rounded-xl py-1.5 px-1">
                      <div class="text-xs font-extrabold text-white flex items-center justify-center gap-1">
                        <i class="fa-solid fa-bolt text-emerald-400 text-[11px]"></i>
                        <span x-text="(asig.tests?.length || 0)"></span>
                      </div>
                      <div class="text-[9px] text-gray-400 font-semibold uppercase tracking-tight">Tests</div>
                    </div>
                  </div>

                  <!-- ACCIONES RÁPIDAS EN 1 CLIC -->
                  <div class="flex items-center justify-between gap-2 pt-0.5" @click.stop="">
                    <button @click="abrirAsignatura(asig)" class="flex-1 py-2 px-2.5 rounded-xl bg-gray-900 hover:bg-gray-800 text-gray-200 hover:text-white text-xs font-bold transition border border-gray-700/80 flex items-center justify-center space-x-1.5">
                      <i class="fa-solid fa-folder-open text-cyan-400 text-[11px]"></i>
                      <span>Temario</span>
                    </button>
                    <button @click="abrirGeneradorDirecto(asig)" class="flex-1 py-2 px-2.5 rounded-xl bg-emerald-500/15 hover:bg-emerald-500/25 text-emerald-300 text-xs font-bold transition border border-emerald-500/30 flex items-center justify-center space-x-1.5" title="Generar Guía o Test IA directamente">
                      <i class="fa-solid fa-wand-magic-sparkles text-[11px]"></i>
                      <span>Crear IA</span>
                    </button>
                    <button @click="abrirChatConAsignatura(asig.nombre)" class="py-2 px-3 rounded-xl bg-indigo-500/15 hover:bg-indigo-500/25 text-indigo-300 text-xs font-bold transition border border-indigo-500/30 flex items-center justify-center" title="Consultar dudas de esta materia con NOVA">
                      <i class="fa-solid fa-robot text-xs"></i>
                    </button>
                  </div>
                </div>
              </div>
            </template>
          </div>
        </div>

      </div>

      <!-- ==================== 2. VISTA EXPLORADOR DE TEMARIOS ==================== -->
      <div x-show="tab === 'asignaturas' || asigSeleccionada" x-transition class="space-y-6">
        
        <!-- DETALLE DE ASIGNATURA SELECCIONADA (CON PORTADA VISUAL) -->
        <div x-show="asigSeleccionada" class="space-y-6">
          <div class="rounded-3xl overflow-hidden border border-gray-800 bg-surface-5 relative shadow-xl">
            <div class="h-32 relative overflow-hidden">
              <img :src="temaVisualAsignatura(asigSeleccionada?.nombre).imagen"
                   :alt="asigSeleccionada?.nombre"
                   class="w-full h-full object-cover object-center opacity-55">
              <div class="absolute inset-0 bg-gradient-to-t from-surface-5 via-surface-5/60 to-transparent"></div>
              <div class="absolute inset-0 opacity-40 mix-blend-overlay" :class="temaVisualAsignatura(asigSeleccionada?.nombre).gradiente"></div>
            </div>
            <div class="px-6 pb-6 -mt-12 relative z-10 flex flex-wrap items-end justify-between gap-4">
              <div class="flex items-center space-x-4">
                <button @click="asigSeleccionada = null; irASeccion('dashboard')" class="w-11 h-11 rounded-2xl bg-gray-900/90 hover:bg-gray-800 text-gray-200 border border-gray-700 flex items-center justify-center transition shadow-lg">
                  <i class="fa-solid fa-arrow-left"></i>
                </button>
                <div class="w-12 h-12 rounded-2xl flex items-center justify-center shadow-xl border border-white/20 text-white text-xl"
                     :class="temaVisualAsignatura(asigSeleccionada?.nombre).badgeBg">
                  <i :class="temaVisualAsignatura(asigSeleccionada?.nombre).icono"></i>
                </div>
                <div>
                  <div class="flex flex-wrap items-center gap-2">
                    <span class="text-[11px] font-black uppercase px-2 py-0.5 rounded-md border"
                          :class="temaVisualAsignatura(asigSeleccionada?.nombre).chipClase"
                          x-text="temaVisualAsignatura(asigSeleccionada?.nombre).siglas"></span>
                    <h2 class="text-2xl font-extrabold text-white" x-text="asigSeleccionada?.nombre"></h2>
                    <span class="text-xs px-2.5 py-0.5 rounded-full font-semibold bg-cyan-500/15 text-cyan-300 border border-cyan-500/30" x-text="asigSeleccionada?.curso"></span>
                  </div>
                  <p class="text-xs text-gray-300 mt-1" x-text="temaVisualAsignatura(asigSeleccionada?.nombre).subtitulo"></p>
                </div>
              </div>
              
              <div class="flex flex-wrap items-center gap-2">
                <button @click="abrirModalNuevoItemAgenda('Tema', asigSeleccionada?.nombre, '', 'calendario')" class="px-3.5 py-2 bg-gray-900 hover:bg-gray-800 border border-gray-700 rounded-xl text-xs font-semibold text-gray-200 transition flex items-center space-x-1.5" title="Añadir clase, tema o tarea a esta asignatura">
                  <i class="fa-solid fa-calendar-plus text-cyan-400"></i><span>+ Añadir al Calendario</span>
                </button>
                <button @click="modalGenerador = true" class="px-4 py-2 bg-gradient-to-r from-emerald-500 to-teal-600 rounded-xl text-xs font-bold text-gray-900 hover:shadow-lg hover:shadow-emerald-500/20 transition flex items-center space-x-2">
                  <i class="fa-solid fa-wand-magic-sparkles"></i><span>Generar Guía / Test IA</span>
                </button>
                <button @click="abrirChatConAsignatura(asigSeleccionada?.nombre)" class="px-4 py-2 bg-gradient-to-r from-cyan-500 to-indigo-600 rounded-xl text-xs font-semibold hover:shadow-lg hover:shadow-cyan-500/20 transition flex items-center space-x-2">
                  <i class="fa-solid fa-robot"></i><span>Consultar con NOVA</span>
                </button>
              </div>
            </div>
          </div>

          <!-- ORGANIZADOR NOTION EXCLUSIVO DE ESTA ASIGNATURA: CALENDARIO DE LA ASIGNATURA + ACTIVIDADES PAC + TEMAS CON ESTADO -->
          <div class="grid grid-cols-1 xl:grid-cols-12 gap-5">
            <!-- MINI-CALENDARIO EXCLUSIVO DE ESTA ASIGNATURA (8 COLS) -->
            <div class="xl:col-span-8 glass p-5 rounded-3xl border border-gray-800 space-y-3">
              <div class="flex flex-wrap items-center justify-between gap-2 pb-3 border-b border-gray-800">
                <div>
                  <h3 class="font-extrabold text-white text-sm flex items-center gap-2">
                    <i class="fa-solid fa-calendar-week text-cyan-400"></i>
                    <span>Calendario de <span x-text="asigSeleccionada?.nombre"></span></span>
                  </h3>
                  <p class="text-[11px] text-gray-400">Arrastra cualquier Tema o Clase a otro día, usa ◀ ▶ o haz clic para editarlo a tu gusto</p>
                </div>
                <div class="flex items-center space-x-1 bg-gray-950/90 p-1 rounded-xl border border-gray-800">
                  <button @click="navegarCalendario(-1)" class="p-1 px-2 rounded-lg text-gray-400 hover:text-white hover:bg-gray-800 text-xs">
                    <i class="fa-solid fa-chevron-left"></i>
                  </button>
                  <button @click="irAHoyCalendario()"
                          :class="esPeriodoActualCalendario ? 'text-cyan-400' : 'text-amber-300 bg-amber-500/15 border border-amber-500/30'"
                          class="px-2.5 py-0.5 rounded-lg text-[11px] font-bold hover:bg-cyan-500/10 whitespace-nowrap">
                    <span x-text="etiquetaPeriodoCalendario"></span>
                  </button>
                  <button @click="navegarCalendario(1)" class="p-1 px-2 rounded-lg text-gray-400 hover:text-white hover:bg-gray-800 text-xs">
                    <i class="fa-solid fa-chevron-right"></i>
                  </button>
                </div>
              </div>

              <div class="hidden sm:grid grid-cols-5 gap-1.5 text-center text-[10px] font-bold lowercase text-gray-400">
                <div>lun</div><div>mar</div><div>mié</div><div>jue</div><div>vie</div>
              </div>
              <div class="grid grid-cols-1 sm:grid-cols-5 gap-1.5 pb-1">
                <template x-for="celda in diasCalendarioAsignaturaActual" :key="'asig-cal-' + celda.key">
                  <div @dragover.prevent="if (celda.fechaStr) diaArrastreSobre = celda.fechaStr"
                       @dragleave.prevent="if ($event.relatedTarget && !$el.contains($event.relatedTarget)) diaArrastreSobre = ''"
                       @drop.prevent="soltarItemEnDia(celda.fechaStr)"
                       :class="[
                         'bg-surface-4 border-gray-800',
                         celda.esHoy ? 'ring-1 ring-rose-500 border-rose-500' : '',
                         diaArrastreSobre === celda.fechaStr && celda.fechaStr ? 'ring-2 ring-cyan-400 bg-cyan-950/60 border-cyan-400 scale-[1.01]' : ''
                       ]"
                       class="min-h-[96px] p-1.5 rounded-xl border flex flex-col justify-between transition-all duration-150">
                    <div class="flex items-center justify-between text-[10px] text-gray-400 mb-1">
                      <div class="flex items-center gap-1.5">
                        <button x-show="celda.fechaStr" @click="abrirModalNuevoItemAgenda('Tema', asigSeleccionada?.nombre, celda.fechaStr, 'calendario')" class="text-gray-500 hover:text-cyan-400 text-[10px] font-bold px-1 rounded hover:bg-gray-800" title="Planificar tema o tarea aquí">+</button>
                        <span class="sm:hidden text-xs font-bold text-cyan-300 capitalize" x-text="nombreDiaDeFecha(celda.fechaStr)"></span>
                      </div>
                      <span class="font-bold ml-auto" :class="celda.esHoy ? 'text-rose-400 font-extrabold' : ''" x-text="celda.dia"></span>
                    </div>
                    <div class="space-y-1">
                      <template x-for="(ev, idx) in celda.eventos" :key="'ac-' + (ev.id || 'ev') + '-' + idx">
                        <div @click="abrirModalItemAgenda(ev)"
                             draggable="true"
                             @dragstart="iniciarArrastreItem($event, ev)"
                             @dragend="finalizarArrastreItem()"
                             class="p-1.5 rounded-lg border cursor-grab active:cursor-grabbing text-[10px] leading-tight transition-all duration-150 hover:brightness-110 group/acard"
                             :class="[
                               claseColorTarjetaNotion(ev),
                               itemArrastrando && itemArrastrando.id === ev.id ? 'opacity-30 scale-95 border-dashed border-cyan-400' : 'hover:scale-[1.01]'
                             ]">
                          <p class="font-bold text-white break-words" x-text="ev.titulo"></p>
                          <div class="flex items-center justify-between gap-1 mt-1">
                            <span class="text-[8px] px-1 py-0.2 rounded font-bold inline-block" :class="claseBadgeEstadoNotion(ev.estado)" x-text="ev.estado"></span>
                            <div class="flex items-center gap-0.5 opacity-60 sm:opacity-0 group-hover/acard:opacity-100 transition-opacity" @click.stop="">
                              <button @click="moverItemDias(ev, -1)" title="Mover -1 día" class="w-3.5 h-3.5 rounded flex items-center justify-center bg-black/50 hover:bg-cyan-500 hover:text-black text-[8px] text-gray-200 transition active:scale-90">◀</button>
                              <button @click="moverItemDias(ev, 1)" title="Mover +1 día" class="w-3.5 h-3.5 rounded flex items-center justify-center bg-black/50 hover:bg-cyan-500 hover:text-black text-[8px] text-gray-200 transition active:scale-90">▶</button>
                            </div>
                          </div>
                        </div>
                      </template>
                    </div>
                  </div>
                </template>
              </div>
            </div>

            <!-- ACTIVIDADES EVALUABLES (PACs) Y ESTADO DE TEMAS DE ESTA ASIGNATURA (4 COLS) -->
            <div class="xl:col-span-4 glass p-5 rounded-3xl border border-gray-800 flex flex-col justify-between space-y-4">
              <!-- ACTIVIDADES (PACs) DE LA ASIGNATURA -->
              <div>
                <div class="flex flex-wrap items-center justify-between pb-2.5 border-b border-gray-800 gap-2">
                  <h3 class="font-extrabold text-white text-sm flex items-center gap-2">
                    <i class="fa-solid fa-flag-checkered text-rose-400"></i>
                    <span>Actividades Evaluables (PACs)</span>
                  </h3>
                  <!-- Selector rápido: Todas / Actividades / Tests -->
                  <div class="flex items-center bg-gray-950/90 p-0.5 rounded-lg border border-gray-800 text-[10px]">
                    <button @click="filtroPacTipo = 'todas'"
                            :class="filtroPacTipo === 'todas' ? 'bg-gray-800 text-white font-bold' : 'text-gray-400 hover:text-white'"
                            class="px-2 py-0.5 rounded transition">
                      Todas (<span x-text="actividadesAsignaturaActual.length"></span>)
                    </button>
                    <button @click="filtroPacTipo = 'actividades'"
                            :class="filtroPacTipo === 'actividades' ? 'bg-rose-500/25 text-rose-300 font-bold border border-rose-500/40' : 'text-gray-400 hover:text-rose-300'"
                            class="px-2 py-0.5 rounded transition">
                      Actividades (<span x-text="actividadesPurasAsignaturaActual.length"></span>)
                    </button>
                    <button @click="filtroPacTipo = 'tests'"
                            :class="filtroPacTipo === 'tests' ? 'bg-emerald-500/25 text-emerald-300 font-bold border border-emerald-500/40' : 'text-gray-400 hover:text-emerald-300'"
                            class="px-2 py-0.5 rounded transition">
                      Tests (<span x-text="testsPurosAsignaturaActual.length"></span>)
                    </button>
                  </div>
                </div>

                <div class="space-y-2 mt-2.5 max-h-[190px] overflow-y-auto pr-1">
                  <!-- SECCIÓN 1: ACTIVIDADES Y PACs (PRIMERO) -->
                  <div class="space-y-1.5" x-show="(filtroPacTipo === 'todas' || filtroPacTipo === 'actividades') && actividadesPurasAsignaturaActual.length > 0">
                    <div x-show="filtroPacTipo === 'todas'" class="flex items-center gap-1.5 text-[10px] font-bold uppercase tracking-wider text-rose-400 pt-0.5 pb-0.5">
                      <i class="fa-solid fa-file-pen text-[9px]"></i>
                      <span>Actividades y PACs (<span x-text="actividadesPurasAsignaturaActual.length"></span>)</span>
                    </div>
                    <template x-for="act in actividadesPurasAsignaturaActual" :key="'act-' + act.id">
                      <div @click="abrirModalItemAgenda(act)"
                           draggable="true"
                           @dragstart="iniciarArrastreItem($event, act)"
                           @dragend="finalizarArrastreItem()"
                           class="p-2.5 rounded-xl border cursor-grab active:cursor-grabbing transition flex items-center justify-between gap-2"
                           :class="claseColorTarjetaNotion(act)">
                        <div class="min-w-0 flex-1">
                          <p class="text-xs font-extrabold text-white break-words" x-text="act.titulo"></p>
                          <p class="text-[10px] text-gray-300 font-mono mt-0.5" x-text="'Entrega: ' + (formatearFechaDDMMAAAA(act.fecha_dia) || 'Pendiente') + ' • ' + (formatearPlazoTexto(act.plazo_texto, act.fecha_dia) || '')"></p>
                        </div>
                        <div class="text-right shrink-0">
                          <span class="text-[9px] px-1.5 py-0.5 rounded font-bold block" :class="claseBadgeEstadoNotion(act.estado)" x-text="act.estado"></span>
                          <span x-show="act.nota_personal !== null && act.nota_personal !== undefined" class="text-[10px] font-bold text-emerald-300 font-mono mt-0.5 block" x-text="act.nota_personal + '/10'"></span>
                        </div>
                      </div>
                    </template>
                  </div>

                  <!-- SECCIÓN 2: TESTS DE EVALUACIÓN (LUEGO) -->
                  <div class="space-y-1.5 pt-1" x-show="(filtroPacTipo === 'todas' || filtroPacTipo === 'tests') && testsPurosAsignaturaActual.length > 0">
                    <div x-show="filtroPacTipo === 'todas' && actividadesPurasAsignaturaActual.length > 0" class="flex items-center gap-1.5 text-[10px] font-bold uppercase tracking-wider text-emerald-400 pt-2 pb-0.5 border-t border-gray-800/80">
                      <i class="fa-solid fa-bullseye text-[9px]"></i>
                      <span>Tests de Autoevaluación (<span x-text="testsPurosAsignaturaActual.length"></span>)</span>
                    </div>
                    <template x-for="act in testsPurosAsignaturaActual" :key="'test-' + act.id">
                      <div @click="abrirModalItemAgenda(act)"
                           draggable="true"
                           @dragstart="iniciarArrastreItem($event, act)"
                           @dragend="finalizarArrastreItem()"
                           class="p-2.5 rounded-xl border cursor-grab active:cursor-grabbing transition flex items-center justify-between gap-2"
                           :class="claseColorTarjetaNotion(act)">
                        <div class="min-w-0 flex-1">
                          <p class="text-xs font-extrabold text-white break-words" x-text="act.titulo"></p>
                          <p class="text-[10px] text-gray-300 font-mono mt-0.5" x-text="'Entrega: ' + (formatearFechaDDMMAAAA(act.fecha_dia) || 'Pendiente') + ' • ' + (formatearPlazoTexto(act.plazo_texto, act.fecha_dia) || '')"></p>
                        </div>
                        <div class="text-right shrink-0">
                          <span class="text-[9px] px-1.5 py-0.5 rounded font-bold block" :class="claseBadgeEstadoNotion(act.estado)" x-text="act.estado"></span>
                          <span x-show="act.nota_personal !== null && act.nota_personal !== undefined" class="text-[10px] font-bold text-emerald-300 font-mono mt-0.5 block" x-text="act.nota_personal + '/10'"></span>
                        </div>
                      </div>
                    </template>
                  </div>

                  <div x-show="actividadesAsignaturaActual.length === 0" class="text-xs text-gray-500 py-3 text-center">
                    Sin entregas PAC registradas en esta materia.
                  </div>
                </div>
              </div>

              <!-- SEGUIMIENTO DE TEMAS (TO-DO TEMARIO ESTILO NOTION) -->
              <div class="pt-3 border-t border-gray-800 flex-1 flex flex-col">
                <h3 class="font-extrabold text-white text-sm flex items-center justify-between pb-2">
                  <span class="flex items-center gap-2"><i class="fa-solid fa-list-check text-amber-400"></i><span>Progreso por Temas</span></span>
                  <span class="text-[10px] text-gray-400" x-text="temasAsignaturaActual.length + ' temas (arrástralos al calendario)'"></span>
                </h3>
                <div class="space-y-1.5 max-h-[175px] overflow-y-auto pr-1">
                  <template x-for="tm in temasAsignaturaActual" :key="'tm-' + tm.id">
                    <div @click="abrirModalItemAgenda(tm)"
                         draggable="true"
                         @dragstart="iniciarArrastreItem($event, tm)"
                         @dragend="finalizarArrastreItem()"
                         class="p-2 rounded-xl border cursor-grab active:cursor-grabbing transition flex items-center justify-between gap-2 group/tm"
                         :class="claseColorTarjetaNotion(tm)">
                      <div class="min-w-0 flex-1">
                        <p class="text-xs font-bold text-white break-words" x-text="tm.titulo"></p>
                        <p class="text-[9px] text-gray-300 font-mono" x-text="tm.fecha_dia ? ('Día: ' + formatearFechaDDMMAAAA(tm.fecha_dia)) : 'Arrástralo a un día o haz clic'"></p>
                      </div>
                      <div class="flex items-center gap-1 shrink-0">
                        <div class="hidden group-hover/tm:flex items-center gap-0.5 mr-1" @click.stop="">
                          <button @click="moverItemDias(tm, -1)" title="-1 día" class="px-1 py-0.5 rounded bg-black/40 hover:bg-black/70 text-[9px] text-gray-200">◀</button>
                          <button @click="moverItemDias(tm, 1)" title="+1 día" class="px-1 py-0.5 rounded bg-black/40 hover:bg-black/70 text-[9px] text-gray-200">▶</button>
                        </div>
                        <span x-show="tm.nota_personal !== null && tm.nota_personal !== undefined" class="text-[10px] font-mono font-bold text-emerald-300" x-text="tm.nota_personal + '/10'"></span>
                        <span class="text-[9px] px-1.5 py-0.5 rounded font-bold" :class="claseBadgeEstadoNotion(tm.estado)" x-text="tm.estado"></span>
                      </div>
                    </div>
                  </template>
                </div>
              </div>
            </div>
          </div>

          <!-- SECCIÓN PRINCIPAL: UNIDADES Y TEMARIOS UNIFICADOS (PDF + GUÍA IA + SIMULACRO EN CADA TEMA) -->
          <div class="space-y-6">
            <div class="glass p-5 sm:p-6 rounded-3xl border border-gray-800 space-y-4">
              <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-3 pb-4 border-b border-gray-800">
                <div class="flex items-center space-x-3">
                  <div class="w-10 h-10 rounded-2xl bg-gradient-to-tr from-cyan-500 to-indigo-600 text-white flex items-center justify-center text-lg shadow-lg shadow-cyan-500/20 shrink-0">
                    <i class="fa-solid fa-layer-group"></i>
                  </div>
                  <div>
                    <div class="flex items-center gap-2">
                      <h3 class="font-extrabold text-white text-base">Unidades de Estudio y Temario</h3>
                      <span class="text-[10px] px-2 py-0.5 rounded-full bg-cyan-500/15 text-cyan-300 border border-cyan-500/30 font-bold"
                            x-text="(unidadesUnificadasAsignaturaActual.temas?.length || 0) + ' temas'"></span>
                    </div>
                    <p class="text-xs text-gray-400 mt-0.5">Cada tema reúne en un solo lugar su temario oficial, su guía resumen con IA y su simulacro tipo test</p>
                  </div>
                </div>

                <!-- BANCO DE FALLOS RÁPIDO (SI TIENE PREGUNTAS PENDIENTES) -->
                <div x-show="fallosDeAsignatura(asigSeleccionada?.nombre).length > 0">
                  <button @click="iniciarRepasoFallos(asigSeleccionada.nombre)"
                          class="px-3.5 py-2 bg-rose-500/20 hover:bg-rose-500/30 border border-rose-500/40 text-rose-300 font-bold rounded-xl text-xs transition flex items-center gap-2 shadow-sm">
                    <i class="fa-solid fa-fire-flame-curved text-rose-400"></i>
                    <span x-text="'Repasar ' + fallosDeAsignatura(asigSeleccionada?.nombre).length + ' fallos'"></span>
                  </button>
                </div>
              </div>

              <!-- LISTADO DE TEMAS UNIFICADOS (TARJETA MODULAR POR TEMA) -->
              <div class="grid grid-cols-1 md:grid-cols-2 gap-3.5">
                <template x-for="tm in unidadesUnificadasAsignaturaActual.temas" :key="'unidad-' + tm.numero">
                  <div draggable="true"
                       @dragstart="iniciarArrastreTema($event, tm)"
                       @dragend="finalizarArrastreItem()"
                       :class="[
                         tm.estado === 'Hecho' ? 'border-emerald-500/40 bg-emerald-950/15 shadow-sm shadow-emerald-950/30' :
                         (tm.estado === 'En proceso' ? 'border-cyan-500/40 bg-cyan-950/15 shadow-sm shadow-cyan-950/30' : 'border-gray-800/80 bg-gray-900/40'),
                         'hover:border-cyan-500/50'
                       ]"
                       class="glass-card p-4 rounded-2xl border transition flex flex-col justify-between space-y-3 group cursor-default">
                    <div>
                      <div class="flex items-center justify-between gap-2 text-[10px] mb-2">
                        <div class="flex items-center gap-2 flex-wrap">
                          <span class="font-black px-2 py-0.5 rounded-md bg-cyan-500/15 text-cyan-300 border border-cyan-500/30 font-mono uppercase tracking-wider"
                                x-text="'Tema ' + tm.numero"></span>

                          <!-- SELECTOR DE ESTADO INTERACTIVO: Por hacer / En proceso / Hecho -->
                          <div class="relative" x-data="{ openMenu: false }" @click.outside="openMenu = false">
                            <button @click.stop="openMenu = !openMenu"
                                    :class="claseBadgeEstadoTema(tm.estado)"
                                    class="px-2 py-0.5 rounded-md text-[10px] font-bold flex items-center gap-1.5 transition cursor-pointer border shadow-sm"
                                    title="Cambiar estado: Por hacer, En proceso o Hecho">
                              <i :class="iconoEstadoTema(tm.estado)" class="text-[9px]"></i>
                              <span x-text="tm.estado"></span>
                              <i class="fa-solid fa-chevron-down text-[8px] opacity-60"></i>
                            </button>

                            <!-- Menú de selección rápida -->
                            <div x-show="openMenu"
                                 x-transition:enter="transition ease-out duration-100"
                                 x-transition:enter-start="opacity-0 scale-95"
                                 x-transition:enter-end="opacity-100 scale-100"
                                 class="absolute left-0 top-full mt-1.5 bg-surface-6 border border-gray-700/90 rounded-xl shadow-2xl p-1.5 z-30 min-w-[135px] space-y-1"
                                 style="display: none;">
                              <button @click.stop="cambiarEstadoTema(tm, 'Por hacer'); openMenu = false"
                                      class="w-full text-left px-2.5 py-1 rounded-lg text-[11px] font-semibold flex items-center gap-2 hover:bg-gray-800 text-gray-300 transition"
                                      :class="tm.estado === 'Por hacer' ? 'bg-gray-800 font-bold text-white' : ''">
                                <span class="w-2 h-2 rounded-full bg-gray-400"></span>
                                <span>Por hacer</span>
                              </button>
                              <button @click.stop="cambiarEstadoTema(tm, 'En proceso'); openMenu = false"
                                      class="w-full text-left px-2.5 py-1 rounded-lg text-[11px] font-semibold flex items-center gap-2 hover:bg-cyan-500/20 text-cyan-300 transition"
                                      :class="tm.estado === 'En proceso' ? 'bg-cyan-500/15 font-bold' : ''">
                                <span class="w-2 h-2 rounded-full bg-cyan-400"></span>
                                <span>En proceso</span>
                              </button>
                              <button @click.stop="cambiarEstadoTema(tm, 'Hecho'); openMenu = false"
                                      class="w-full text-left px-2.5 py-1 rounded-lg text-[11px] font-semibold flex items-center gap-2 hover:bg-emerald-500/20 text-emerald-300 transition"
                                      :class="tm.estado === 'Hecho' ? 'bg-emerald-500/15 font-bold' : ''">
                                <span class="w-2 h-2 rounded-full bg-emerald-400"></span>
                                <span>Hecho</span>
                              </button>
                            </div>
                          </div>
                        </div>

                        <!-- RECURSOS DISPONIBLES (PDF / GUÍA IA / TEST) -->
                        <div class="flex items-center gap-1.5 font-bold shrink-0">
                          <span x-show="tm.pdf" class="text-[9px] px-1.5 py-0.2 rounded bg-black/40 text-gray-400">PDF</span>
                          <span x-show="tm.guia" class="text-[9px] px-1.5 py-0.2 rounded bg-sky-500/20 text-sky-300">Guía IA</span>
                          <span x-show="tm.test" class="text-[9px] px-1.5 py-0.2 rounded bg-emerald-500/20 text-emerald-300">Test</span>
                        </div>
                      </div>
                      <h4 class="text-sm font-bold text-white group-hover:text-cyan-200 transition break-words" x-text="tm.titulo"></h4>
                    </div>

                    <!-- BOTONES UNIFICADOS DEL TEMA (PDF | GUÍA IA | CALENDARIO | SIMULACRO TEST) -->
                    <div class="pt-2.5 border-t border-gray-800/80 flex flex-wrap items-center gap-2">
                      <!-- 1. VER PDF OFICIAL -->
                      <button x-show="tm.pdf"
                              @click="abrirPdf('data/materiales/' + asigSeleccionada.nombre + '/' + tm.pdf, tm.pdf)"
                              class="px-2.5 py-1.5 rounded-xl bg-gray-800 hover:bg-gray-700 text-gray-200 hover:text-white text-xs font-semibold transition flex items-center gap-1.5">
                        <i class="fa-regular fa-file-pdf text-rose-400 text-xs"></i>
                        <span>PDF Oficial</span>
                      </button>

                      <!-- 2. GUÍA DE ESTUDIO IA -->
                      <template x-if="tm.guia">
                        <button @click="verContenidoArchivo('data/procesado/guias/' + asigSeleccionada.nombre + '/' + tm.guia)"
                                class="px-2.5 py-1.5 rounded-xl bg-sky-500/15 hover:bg-sky-500/25 border border-sky-500/30 text-sky-300 text-xs font-bold transition flex items-center gap-1.5">
                          <i class="fa-solid fa-book-open text-sky-400 text-xs"></i>
                          <span>Guía IA</span>
                        </button>
                      </template>
                      <template x-if="!tm.guia && tm.pdf">
                        <button @click="generarGuiaDesdePdf(tm.pdf)"
                                class="px-2.5 py-1.5 rounded-xl bg-gray-800/80 hover:bg-sky-500/20 text-gray-400 hover:text-sky-300 text-xs font-medium transition flex items-center gap-1.5"
                                title="Crear guía resumen con Inteligencia Artificial">
                          <i class="fa-solid fa-wand-magic-sparkles text-[10px]"></i>
                          <span>Crear Guía</span>
                        </button>
                      </template>

                      <!-- 3. ENTRENAMIENTOS DE CÓDIGO (EJERCICIOS OFICIALES DEL PDF) -->
                      <button x-show="tm.pdf"
                              @click="abrirModalEntrenamientos(asigSeleccionada.nombre, tm.numero, tm.titulo)"
                              class="px-2.5 py-1.5 rounded-xl bg-purple-500/15 hover:bg-purple-500/25 border border-purple-500/35 text-purple-300 text-xs font-bold transition flex items-center gap-1.5 shadow-sm"
                              title="Ver los 5 ejercicios prácticos oficiales de programación de este tema (con enunciados y soluciones paso a paso)">
                        <i class="fa-solid fa-code text-purple-400 text-xs"></i>
                        <span>Entrenamientos</span>
                      </button>

                      <!-- 3. CALENDARIO / PLANIFICACIÓN -->
                      <template x-if="tm.fecha_dia">
                        <button @click="tm.eventoAgenda ? abrirModalItemAgenda(tm.eventoAgenda) : planificarTemaEnCalendario(tm)"
                                class="px-2.5 py-1.5 rounded-xl bg-amber-500/15 hover:bg-amber-500/25 border border-amber-500/35 text-amber-300 text-xs font-bold transition flex items-center gap-1.5 group/cal"
                                :title="'Planificado para el ' + formatearFechaDDMMAAAA(tm.fecha_dia) + '. Clic para editar o mover en el calendario'">
                          <i class="fa-regular fa-calendar-check text-amber-400 text-xs"></i>
                          <span x-text="formatearFechaDDMMAAAA(tm.fecha_dia)"></span>
                          <i class="fa-solid fa-pen text-[9px] opacity-60 group-hover/cal:opacity-100"></i>
                        </button>
                      </template>
                      <template x-if="!tm.fecha_dia">
                        <button @click="planificarTemaEnCalendario(tm)"
                                class="px-2.5 py-1.5 rounded-xl bg-gray-800 hover:bg-amber-500/20 border border-gray-700/80 hover:border-amber-500/40 text-gray-300 hover:text-amber-300 text-xs font-semibold transition flex items-center gap-1.5 shadow-sm"
                                title="Añadir a mi calendario / planificar fecha de estudio (o arrastra la tarjeta al día del calendario arriba)">
                          <i class="fa-regular fa-calendar-plus text-amber-400 text-xs"></i>
                          <span>+ Calendario</span>
                        </button>
                      </template>

                      <!-- 4. SIMULACRO DE EXAMEN (TEST) -->
                      <template x-if="tm.test">
                        <button @click="iniciarTestWeb(asigSeleccionada.nombre, tm.test)"
                                class="px-3 py-1.5 rounded-xl bg-emerald-500/20 hover:bg-emerald-500/30 border border-emerald-500/40 text-emerald-300 text-xs font-bold transition flex items-center gap-1.5 shadow-sm ml-auto">
                          <i class="fa-solid fa-bullseye text-emerald-400 text-xs"></i>
                          <span>Hacer Test</span>
                        </button>
                      </template>
                      <template x-if="!tm.test && tm.pdf">
                        <button @click="generarGuiaDesdePdf(tm.pdf)"
                                class="px-2.5 py-1.5 rounded-xl bg-gray-800/80 hover:bg-emerald-500/20 text-gray-400 hover:text-emerald-300 text-xs font-medium transition flex items-center gap-1.5 ml-auto"
                                title="Crear test tipo examen con Inteligencia Artificial">
                          <i class="fa-solid fa-plus text-[10px]"></i>
                          <span>Crear Test</span>
                        </button>
                      </template>
                    </div>
                  </div>
                </template>
              </div>

              <!-- OTROS DOCUMENTOS ADJUNTOS (PRÁCTICAS, GUÍA DOCENTE, CRITERIOS...) -->
              <div x-show="unidadesUnificadasAsignaturaActual.otrosDocumentos?.length > 0" class="pt-4 border-t border-gray-800">
                <p class="text-[11px] uppercase font-bold text-gray-400 mb-2 flex items-center gap-1.5">
                  <i class="fa-solid fa-paperclip text-xs"></i>
                  <span>Otros Documentos, Prácticas y Enunciados</span>
                </p>
                <div class="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 gap-2">
                  <template x-for="doc in unidadesUnificadasAsignaturaActual.otrosDocumentos" :key="'otro-doc-' + doc">
                    <div @click="abrirPdf('data/materiales/' + asigSeleccionada.nombre + '/' + doc, doc)"
                         class="p-2.5 rounded-xl bg-gray-900/60 border border-gray-800/90 hover:border-gray-700 text-gray-300 hover:text-white transition flex items-center justify-between cursor-pointer gap-2 group">
                      <div class="flex items-center space-x-2 truncate">
                        <i class="fa-regular fa-file-lines text-gray-400 group-hover:text-cyan-400 text-xs shrink-0"></i>
                        <span class="text-xs truncate" x-text="doc"></span>
                      </div>
                      <i class="fa-solid fa-arrow-up-right-from-square text-[9px] text-gray-500 group-hover:text-cyan-300 shrink-0"></i>
                    </div>
                  </template>
                </div>
              </div>
            </div>

            <!-- VIDEOCLASES OFICIALES (SI TIENE CLASES GRABADAS LA ASIGNATURA) -->
            <div x-show="asigSeleccionada?.clases && asigSeleccionada?.clases.length > 0" class="glass p-5 rounded-3xl border border-gray-800 space-y-4">
              <div class="flex items-center justify-between border-b border-gray-800 pb-3">
                <h3 class="font-bold text-white flex items-center space-x-2 text-sm">
                  <i class="fa-solid fa-video text-amber-400"></i>
                  <span>Videoclases Oficiales y Transcripciones</span>
                </h3>
                <span class="text-[11px] bg-amber-500/10 text-amber-400 px-2 py-0.5 rounded-full font-semibold" x-text="asigSeleccionada?.clases.length + ' clases'"></span>
              </div>
              <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
                <template x-for="clase in asigSeleccionada?.clases" :key="clase.nombre">
                  <div class="glass-card p-3 rounded-xl hover:border-amber-500/30 transition flex items-center justify-between">
                    <div class="overflow-hidden pr-2 flex-1">
                      <p class="text-[10px] text-amber-400 font-bold font-mono" x-text="formatearFechaDDMMAAAA(clase.fecha)"></p>
                      <h5 class="text-xs font-medium text-white break-words line-clamp-2" x-text="clase.titulo"></h5>
                    </div>
                    <div class="flex items-center space-x-1 shrink-0 ml-2">
                      <button @click="verContenidoArchivo(clase.archivo)" class="px-2.5 py-1 bg-gray-800 hover:bg-amber-500/20 hover:text-amber-300 text-gray-300 rounded-lg text-xs font-semibold transition">
                        Leer
                      </button>
                      <a :href="'/?guia=' + encodeURIComponent(clase.archivo) + '&asig=' + encodeURIComponent(asigSeleccionada.nombre)" target="_blank" title="Abrir en pestaña nueva" class="p-1.5 text-gray-500 hover:text-amber-400 hover:bg-gray-800 rounded-lg transition text-xs">
                        <i class="fa-solid fa-arrow-up-right-from-square text-[10px]"></i>
                      </a>
                    </div>
                  </div>
                </template>
              </div>
            </div>
          </div>
        </div>

        <!-- ==================== CATÁLOGO DE MATERIAS (CUANDO NO HAY ASIGNATURA SELECCIONADA) ==================== -->
        <div x-show="!asigSeleccionada" class="space-y-6">
          <div class="glass rounded-3xl p-6 border border-gray-800/80">
            <div class="flex flex-col lg:flex-row lg:items-center lg:justify-between gap-4 mb-6 pb-4 border-b border-gray-800/80">
              <div>
                <h3 class="text-xl font-extrabold text-white flex items-center space-x-2.5">
                  <i class="fa-solid fa-layer-group text-cyan-400"></i>
                  <span>Materias y Módulos de <span x-text="programaActual?.nombre"></span></span>
                </h3>
                <p class="text-xs text-gray-400 mt-1">Explora tus asignaturas, guías oficiales, videoclases y bancos de preguntas</p>
              </div>

              <div class="flex flex-wrap items-center gap-2">
                <!-- Buscador rápido de asignaturas -->
                <div class="relative">
                  <i class="fa-solid fa-magnifying-glass text-gray-500 text-xs absolute left-3 top-1/2 -translate-y-1/2"></i>
                  <input type="text"
                         x-model="busquedaTexto"
                         @input="aplicarFiltroCatalogo()"
                         placeholder="Buscar asignatura..."
                         class="bg-gray-950/90 border border-gray-800 focus:border-cyan-500/60 rounded-xl pl-8 pr-3 py-1.5 text-xs text-white placeholder-gray-500 outline-none w-44 sm:w-52 transition">
                </div>

                <!-- Filtro por Curso (Todos / 2º Curso / 1º Curso) -->
                <div x-show="programaActivoId === 'dam'" class="flex items-center bg-gray-950 p-1 rounded-xl border border-gray-800 text-xs">
                  <button @click="filtrarCurso('todos')"
                          :class="cursoFiltro === 'todos' ? 'bg-gray-800 text-white font-bold shadow' : 'text-gray-400 hover:text-gray-200'"
                          class="px-2.5 py-1 rounded-lg transition">Todos</button>
                  <button @click="filtrarCurso('2dam')"
                          :class="cursoFiltro === '2dam' ? 'bg-cyan-500/20 text-cyan-300 border border-cyan-500/30 font-bold' : 'text-gray-400 hover:text-gray-200'"
                          class="px-2.5 py-1 rounded-lg transition">2º Curso</button>
                  <button @click="filtrarCurso('1dam')"
                          :class="cursoFiltro === '1dam' ? 'bg-indigo-500/20 text-indigo-300 border border-indigo-500/30 font-bold' : 'text-gray-400 hover:text-gray-200'"
                          class="px-2.5 py-1 rounded-lg transition">1º Curso</button>
                </div>

                <button @click="toggleSoloFavoritas()"
                        :class="mostrarSoloFavoritas ? 'bg-amber-500/20 text-amber-300 border-amber-500/40' : 'bg-gray-950 text-gray-400 border-gray-800 hover:text-gray-200'"
                        class="text-xs px-3 py-1.5 rounded-xl border font-bold transition flex items-center space-x-1.5">
                  <i class="fa-solid fa-star text-[11px]" :class="mostrarSoloFavoritas ? 'text-amber-400' : 'text-gray-500'"></i>
                  <span x-text="mostrarSoloFavoritas ? ('Mis Asignaturas (' + asignaturasFavoritas.length + ')') : 'Filtrar Mis Asignaturas'"></span>
                </button>
                <span class="text-xs bg-gray-950 text-gray-300 font-semibold px-3 py-1.5 rounded-xl border border-gray-800" x-text="catalogoFiltrado.length + ' materias'"></span>
              </div>
            </div>

            <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-5">
              <template x-for="asig in catalogoFiltrado" :key="'cat-asig-' + asig.nombre">
                <div @click="abrirAsignatura(asig)"
                     class="group rounded-2xl overflow-hidden bg-surface-5 border border-gray-800/90 hover:border-cyan-500/50 hover:shadow-xl hover:shadow-cyan-950/30 transition-all duration-200 cursor-pointer flex flex-col justify-between relative">

                  <!-- PORTADA ILUSTRADA / IMAGEN DE LA ASIGNATURA -->
                  <div>
                    <div class="h-28 relative overflow-hidden">
                      <img :src="temaVisualAsignatura(asig.nombre).imagen"
                           :alt="asig.nombre"
                           loading="lazy"
                           class="w-full h-full object-cover object-center group-hover:scale-105 transition-transform duration-500 opacity-65">
                      <div class="absolute inset-0 bg-gradient-to-t from-surface-5 via-surface-5/45 to-transparent"></div>
                      <div class="absolute inset-0 opacity-45 mix-blend-overlay" :class="temaVisualAsignatura(asig.nombre).gradiente"></div>

                      <div class="absolute top-3 inset-x-3 flex items-center justify-between gap-2 z-10">
                        <div class="flex items-center gap-1.5">
                          <button @click.stop="toggleAsignaturaFavorita(asig.nombre)"
                                  :title="esAsignaturaFavorita(asig.nombre) ? 'Quitar de Mis Asignaturas' : 'Fijar en Mis Asignaturas'"
                                  class="w-7 h-7 rounded-lg bg-gray-950/80 backdrop-blur-md border border-white/10 flex items-center justify-center hover:bg-gray-900 transition">
                            <i class="fa-star text-xs transition"
                               :class="esAsignaturaFavorita(asig.nombre) ? 'fa-solid text-amber-400' : 'fa-regular text-gray-300 hover:text-amber-300'"></i>
                          </button>
                          <span class="text-[10px] font-extrabold px-2.5 py-1 rounded-lg bg-gray-950/80 backdrop-blur-md text-gray-100 border border-white/10" x-text="asig.curso"></span>
                        </div>

                        <div class="flex items-center gap-1.5">
                          <template x-if="notaCampusDeAsignatura(asig.nombre) !== null">
                            <span class="px-2 py-0.5 rounded-lg text-[10px] font-extrabold bg-indigo-950/90 backdrop-blur-md text-indigo-200 border border-indigo-400/40"
                                  x-text="'🏛️ ' + notaCampusDeAsignatura(asig.nombre) + '%'"></span>
                          </template>
                        </div>
                      </div>

                      <div class="absolute bottom-2.5 left-4 flex items-center space-x-2 z-10">
                        <div class="w-8 h-8 rounded-xl flex items-center justify-center shadow-lg border border-white/20 text-white text-xs"
                             :class="temaVisualAsignatura(asig.nombre).badgeBg">
                          <i :class="temaVisualAsignatura(asig.nombre).icono"></i>
                        </div>
                        <span class="text-[11px] font-black uppercase tracking-wider px-2 py-0.5 rounded-md border backdrop-blur-md"
                              :class="temaVisualAsignatura(asig.nombre).chipClase"
                              x-text="temaVisualAsignatura(asig.nombre).siglas"></span>
                      </div>
                    </div>

                    <div class="px-5 pt-2 pb-1">
                      <h4 class="font-extrabold text-white text-base leading-snug group-hover:text-cyan-300 transition" x-text="asig.nombre"></h4>
                      <p class="text-[11px] text-gray-400 mt-0.5 line-clamp-1" x-text="temaVisualAsignatura(asig.nombre).subtitulo"></p>
                    </div>
                  </div>

                  <div class="px-5 pb-4 pt-3 space-y-3">
                    <div class="grid grid-cols-4 gap-1.5 text-center">
                      <div class="bg-gray-900/90 border border-gray-800/90 rounded-xl py-1.5 px-1">
                        <div class="text-xs font-extrabold text-white flex items-center justify-center gap-1">
                          <i class="fa-regular fa-file-pdf text-rose-400 text-[11px]"></i>
                          <span x-text="asig.total_materiales"></span>
                        </div>
                        <div class="text-[9px] text-gray-400 font-semibold uppercase tracking-tight">PDFs</div>
                      </div>
                      <div class="bg-gray-900/90 border border-gray-800/90 rounded-xl py-1.5 px-1">
                        <div class="text-xs font-extrabold text-white flex items-center justify-center gap-1">
                          <i class="fa-solid fa-video text-amber-400 text-[11px]"></i>
                          <span x-text="asig.total_clases"></span>
                        </div>
                        <div class="text-[9px] text-gray-400 font-semibold uppercase tracking-tight">Clases</div>
                      </div>
                      <div class="bg-gray-900/90 border border-gray-800/90 rounded-xl py-1.5 px-1">
                        <div class="text-xs font-extrabold text-white flex items-center justify-center gap-1">
                          <i class="fa-solid fa-book-open text-cyan-400 text-[11px]"></i>
                          <span x-text="(asig.guias?.length || 0)"></span>
                        </div>
                        <div class="text-[9px] text-gray-400 font-semibold uppercase tracking-tight">Guías</div>
                      </div>
                      <div class="bg-gray-900/90 border border-gray-800/90 rounded-xl py-1.5 px-1">
                        <div class="text-xs font-extrabold text-white flex items-center justify-center gap-1">
                          <i class="fa-solid fa-bolt text-emerald-400 text-[11px]"></i>
                          <span x-text="(asig.tests?.length || 0)"></span>
                        </div>
                        <div class="text-[9px] text-gray-400 font-semibold uppercase tracking-tight">Tests</div>
                      </div>
                    </div>

                    <div class="flex items-center justify-between gap-2 pt-0.5" @click.stop="">
                      <button @click="abrirAsignatura(asig)" class="flex-1 py-2 px-2.5 rounded-xl bg-gray-900 hover:bg-gray-800 text-gray-200 hover:text-white text-xs font-bold transition border border-gray-700/80 flex items-center justify-center space-x-1.5">
                        <i class="fa-solid fa-folder-open text-cyan-400 text-[11px]"></i>
                        <span>Temario</span>
                      </button>
                      <button @click="abrirGeneradorDirecto(asig)" class="flex-1 py-2 px-2.5 rounded-xl bg-emerald-500/15 hover:bg-emerald-500/25 text-emerald-300 text-xs font-bold transition border border-emerald-500/30 flex items-center justify-center space-x-1.5" title="Generar Guía o Test IA directamente">
                        <i class="fa-solid fa-wand-magic-sparkles text-[11px]"></i>
                        <span>Crear IA</span>
                      </button>
                      <button @click="abrirChatConAsignatura(asig.nombre)" class="py-2 px-3 rounded-xl bg-indigo-500/15 hover:bg-indigo-500/25 text-indigo-300 text-xs font-bold transition border border-indigo-500/30 flex items-center justify-center" title="Consultar dudas de esta materia con NOVA">
                        <i class="fa-solid fa-robot text-xs"></i>
                      </button>
                    </div>
                  </div>
                </div>
              </template>
            </div>
          </div>
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
                    <p class="text-xs text-gray-400" x-text="formatearFechaDDMMAAAA(item.fecha) + ' • ' + item.aciertos + ' de ' + (item.total_preguntas || item.total) + ' aciertos'"></p>
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

          <!-- CALIFICACIONES OFICIALES DEL CAMPUS UNIR (SI EL ALUMNO CONECTÓ SU CUENTA CANVAS) -->
          <div class="mt-8 pt-6 border-t border-gray-800">
            <div class="flex flex-col md:flex-row md:items-center justify-between gap-3 mb-4">
              <div>
                <h4 class="text-sm font-bold text-white flex items-center space-x-2">
                  <i class="fa-solid fa-building-columns text-indigo-400"></i>
                  <span>Calificaciones Oficiales en Mi Campus UNIR (Canvas)</span>
                  <span class="text-[10px] px-2 py-0.5 rounded-full bg-indigo-500/20 text-indigo-300 font-bold ml-1"
                        x-text="calificacionesFiltradas.length + ' notas'"></span>
                </h4>
                <p class="text-xs text-gray-400 mt-0.5">Notas reales publicadas por tus profesores en PACs y actividades evaluables</p>
              </div>

              <!-- CONTROLES Y FILTROS DE CALIFICACIONES -->
              <div class="flex flex-wrap items-center gap-2">
                <!-- BUSCADOR POR TÍTULO/ACTIVIDAD -->
                <div class="relative w-40 sm:w-48">
                  <i class="fa-solid fa-magnifying-glass absolute left-3 top-2.5 text-[11px] text-gray-500"></i>
                  <input type="text"
                         x-model="filtroCalTexto"
                         placeholder="Buscar actividad..."
                         class="w-full bg-gray-900/90 text-white placeholder-gray-500 text-xs pl-8 pr-2.5 py-1.5 rounded-xl border border-gray-800 focus:outline-none focus:border-indigo-500">
                </div>

                <!-- FILTRO POR ASIGNATURA -->
                <select x-model="filtroCalAsignatura"
                        class="bg-gray-900/90 border border-gray-800 rounded-xl px-2.5 py-1.5 text-xs text-indigo-300 outline-none focus:border-indigo-500 max-w-[180px] truncate">
                  <option value="todas">Todas las asignaturas</option>
                  <template x-for="asig in asignaturasConCalificaciones" :key="asig">
                    <option :value="asig" x-text="asig"></option>
                  </template>
                </select>

                <!-- FILTRO POR CURSO (TODOS / 2º / 1º) -->
                <div class="flex items-center bg-gray-900/90 p-0.5 rounded-xl border border-gray-800 text-[11px]">
                  <button @click="filtroCalCurso = 'todos'"
                          :class="filtroCalCurso === 'todos' ? 'bg-indigo-500/20 text-indigo-300 font-bold' : 'text-gray-400 hover:text-white'"
                          class="px-2 py-1 rounded-lg transition">Todos</button>
                  <button @click="filtroCalCurso = '2dam'"
                          :class="filtroCalCurso === '2dam' ? 'bg-indigo-500/20 text-indigo-300 font-bold' : 'text-gray-400 hover:text-white'"
                          class="px-2 py-1 rounded-lg transition">2º</button>
                  <button @click="filtroCalCurso = '1dam'"
                          :class="filtroCalCurso === '1dam' ? 'bg-indigo-500/20 text-indigo-300 font-bold' : 'text-gray-400 hover:text-white'"
                          class="px-2 py-1 rounded-lg transition">1º</button>
                </div>

                <!-- BOTÓN ACTUALIZAR CAMPUS -->
                <button @click="abrirModalMiCampus()" class="px-3 py-1.5 rounded-xl bg-indigo-500/20 hover:bg-indigo-500/30 text-indigo-300 text-xs font-semibold transition border border-indigo-500/30 shrink-0">
                  <span x-text="miCampusConfig?.conectado ? '🔄 Actualizar' : '🔗 Conectar'"></span>
                </button>
              </div>
            </div>

            <!-- CONTENEDOR POR ASIGNATURAS (AGRUPADO, LIMPIO Y ORDENADO) -->
            <div x-show="calificacionesAgrupadasPorAsignatura.length > 0" class="space-y-3.5 max-h-[640px] overflow-y-auto pr-1">
              <template x-for="grupo in calificacionesAgrupadasPorAsignatura" :key="grupo.asignatura">
                <div class="glass-card rounded-2xl border border-gray-800/80 bg-gray-900/60 overflow-hidden transition hover:border-indigo-500/30">
                  <!-- CABECERA DE LA ASIGNATURA -->
                  <div @click="toggleExpandirAsignaturaCal(grupo.asignatura)"
                       class="p-3.5 sm:p-4 flex items-center justify-between cursor-pointer select-none bg-gray-900/80 hover:bg-gray-850 transition">
                    <div class="flex items-center space-x-3 min-w-0">
                      <span class="w-8 h-8 rounded-xl flex items-center justify-center text-xs font-bold shrink-0"
                            :class="grupo.es_primero ? 'bg-indigo-500/15 text-indigo-300 border border-indigo-500/30' : 'bg-cyan-500/15 text-cyan-300 border border-cyan-500/30'"
                            x-text="iconoAsignatura(grupo.asignatura)"></span>
                      <div class="min-w-0">
                        <div class="flex items-center gap-2">
                          <h5 class="text-xs sm:text-sm font-bold text-white truncate" x-text="grupo.asignatura"></h5>
                          <span class="text-[9px] px-2 py-0.2 rounded-full font-bold uppercase tracking-wider"
                                :class="grupo.es_primero ? 'bg-indigo-500/10 text-indigo-400 border border-indigo-500/20' : 'bg-cyan-500/10 text-cyan-400 border border-cyan-500/20'"
                                x-text="grupo.es_primero ? '1º Curso' : '2º Curso'"></span>
                        </div>
                        <p class="text-[11px] text-gray-400 mt-0.5" x-text="grupo.notas.length + (grupo.notas.length === 1 ? ' actividad calificada' : ' actividades calificadas')"></p>
                      </div>
                    </div>

                    <div class="flex items-center space-x-3 shrink-0">
                      <!-- MEDIA DE LA ASIGNATURA -->
                      <div class="text-right" x-show="grupo.media !== null">
                        <span class="text-[10px] text-gray-400 block font-medium">Media</span>
                        <span class="text-xs sm:text-sm font-black px-2.5 py-0.5 rounded-lg border font-mono"
                              :class="grupo.media >= 9 ? 'bg-emerald-500/20 text-emerald-300 border-emerald-500/40' : (grupo.media >= 5 ? 'bg-sky-500/20 text-sky-300 border-sky-500/40' : 'bg-rose-500/20 text-rose-300 border-rose-500/40')"
                              x-text="grupo.media.toFixed(1) + ' / 10'"></span>
                      </div>
                      <i class="fa-solid fa-chevron-down text-gray-400 text-xs transition-transform duration-200"
                         :class="(asignaturaExpandidaCal === grupo.asignatura || calificacionesAgrupadasPorAsignatura.length <= 4) ? 'rotate-180 text-cyan-400' : ''"></i>
                    </div>
                  </div>

                  <!-- LISTADO DE ACTIVIDADES DE LA ASIGNATURA -->
                  <div x-show="asignaturaExpandidaCal === grupo.asignatura || calificacionesAgrupadasPorAsignatura.length <= 4"
                       x-transition.opacity.duration.200ms
                       class="p-3 sm:p-4 pt-1 border-t border-gray-800/60 bg-black/20">
                    <div class="grid grid-cols-1 md:grid-cols-2 gap-2.5">
                      <template x-for="cal in grupo.notas" :key="cal.titulo">
                        <div class="p-3 rounded-xl bg-gray-900/90 border border-gray-800 flex items-center justify-between gap-3 hover:border-gray-700 transition">
                          <div class="min-w-0 flex-1">
                            <h6 class="text-xs font-semibold text-gray-200 truncate" x-text="cal.titulo"></h6>
                            <p class="text-[10px] text-gray-400 mt-0.5 flex items-center gap-1.5">
                              <i class="fa-regular fa-calendar text-[10px] text-gray-500"></i>
                              <span x-text="'Calificada: ' + (formatearFechaDDMMAAAA(cal.fecha_calificacion) || 'Reciente')"></span>
                            </p>
                          </div>
                          <div class="text-right shrink-0">
                            <span class="text-xs sm:text-sm font-bold px-2.5 py-1 rounded-lg border font-mono"
                                  :class="(cal.nota_sobre_10 !== null ? cal.nota_sobre_10 : cal.score) >= 9 ? 'bg-emerald-500/15 text-emerald-300 border-emerald-500/30' : ((cal.nota_sobre_10 !== null ? cal.nota_sobre_10 : cal.score) >= 5 ? 'bg-sky-500/15 text-sky-300 border-sky-500/30' : 'bg-rose-500/15 text-rose-300 border-rose-500/30')"
                                  x-text="(cal.nota_sobre_10 !== null ? cal.nota_sobre_10 : cal.score) + ' / 10'"></span>
                          </div>
                        </div>
                      </template>
                    </div>
                  </div>
                </div>
              </template>
            </div>

            <div x-show="miCampusCalificaciones.length > 0 && calificacionesFiltradas.length === 0" class="text-center py-8 text-gray-400 text-xs bg-gray-900/40 rounded-2xl border border-gray-800/60">
              <i class="fa-solid fa-filter-circle-xmark text-lg text-gray-500 mb-1 block"></i>
              No hay calificaciones que coincidan con los filtros seleccionados.
            </div>

            <div x-show="miCampusCalificaciones.length === 0" class="text-center py-6 text-gray-500 text-xs bg-gray-900/40 rounded-2xl border border-gray-800/60">
              <p x-text="miCampusConfig?.conectado ? 'Todavía no hay actividades calificadas en tu cuenta de Canvas.' : 'Conecta tu cuenta de Canvas UNIR para consultar tus notas oficiales de PACs aquí.'"></p>
            </div>
          </div>
        </div>
      </div>

      <!-- ==================== VISTA FLASHCARDS / REPASO ACTIVO PROFESIONAL ==================== -->
      <div x-show="tab === 'flashcards'"
           x-transition
           @keydown.window="
             if (tab === 'flashcards' && !modalGenerarFlashcards) {
               if ($event.code === 'Space' && !['INPUT', 'TEXTAREA', 'SELECT'].includes($event.target.tagName)) {
                 $event.preventDefault();
                 flashcardVolteada = !flashcardVolteada;
               } else if ($event.key === 'ArrowRight') {
                 siguienteFlashcard();
               } else if ($event.key === 'ArrowLeft') {
                 anteriorFlashcard();
               } else if (flashcardVolteada) {
                 if ($event.key === '1') calificarTarjeta('dificil');
                 else if ($event.key === '2') calificarTarjeta('medio');
                 else if ($event.key === '3') calificarTarjeta('facil');
               }
             }
           "
           class="space-y-6">

        <!-- CABECERA PRINCIPAL CON CONTROLES Y PESTAÑAS -->
        <div class="glass p-5 sm:p-6 rounded-3xl border border-gray-800 flex flex-col md:flex-row md:items-center justify-between gap-4 shadow-xl">
          <div class="flex items-center space-x-3.5">
            <div class="w-12 h-12 rounded-2xl bg-gradient-to-tr from-purple-500 to-indigo-600 text-white flex items-center justify-center text-xl shadow-lg shadow-purple-500/25 shrink-0">
              <i class="fa-solid fa-clone"></i>
            </div>
            <div>
              <div class="flex items-center gap-2">
                <h2 class="text-lg sm:text-xl font-black text-white">Mazo de Repaso Activo (Flashcards)</h2>
                <span class="text-[10px] px-2 py-0.5 rounded-full bg-purple-500/20 text-purple-300 border border-purple-500/30 font-bold"
                      x-text="flashcardsFiltradas.length + ' tarjetas'"></span>
              </div>
              <p class="text-xs text-gray-400 mt-0.5">Técnica de memorización activa espaciada para preparar los exámenes oficiales</p>
            </div>
          </div>

          <!-- SELECTORES Y ACCIONES -->
          <div class="flex flex-wrap items-center gap-2.5">
            <!-- PESTAÑAS: MODO ESTUDIO vs VER TODAS EN CUADRÍCULA -->
            <div class="flex items-center bg-gray-900/90 p-1 rounded-2xl border border-gray-800 text-xs">
              <button @click="flashcardVistaModo = 'estudio'"
                      :class="flashcardVistaModo === 'estudio' ? 'bg-purple-500/25 text-purple-300 font-bold border border-purple-500/40 shadow-sm' : 'text-gray-400 hover:text-white'"
                      class="px-3 py-1.5 rounded-xl transition flex items-center gap-1.5">
                <i class="fa-solid fa-play text-[10px]"></i>
                <span>Estudiar</span>
              </button>
              <button @click="flashcardVistaModo = 'catalogo'"
                      :class="flashcardVistaModo === 'catalogo' ? 'bg-purple-500/25 text-purple-300 font-bold border border-purple-500/40 shadow-sm' : 'text-gray-400 hover:text-white'"
                      class="px-3 py-1.5 rounded-xl transition flex items-center gap-1.5">
                <i class="fa-solid fa-border-all text-[10px]"></i>
                <span>Ver Mazo</span>
              </button>
            </div>

            <!-- SELECTOR DE ASIGNATURA -->
            <select x-model="flashcardFiltroAsig" @change="filtrarFlashcards()"
                    class="bg-gray-900 text-gray-200 text-xs px-3.5 py-2 rounded-xl border border-gray-800 outline-none focus:border-purple-500 max-w-[200px] truncate">
              <option value="">Todas las materias</option>
              <template x-for="asig in asignaturasConFlashcards" :key="asig">
                <option :value="asig" x-text="asig"></option>
              </template>
            </select>

            <!-- BOTÓN GENERAR CON IA -->
            <button @click="abrirModalGenerarCards()"
                    class="px-3.5 py-2 bg-gradient-to-r from-purple-500 to-indigo-600 hover:from-purple-400 hover:to-indigo-500 text-white font-bold rounded-xl text-xs transition flex items-center space-x-1.5 shadow-lg shadow-purple-500/25 shrink-0">
              <i class="fa-solid fa-wand-magic-sparkles text-xs"></i>
              <span>Crear con IA</span>
            </button>
          </div>
        </div>

        <!-- ==================== MODO 1: SESIÓN DE ESTUDIO (1 A 1) ==================== -->
        <div x-show="flashcardVistaModo === 'estudio' && flashcardsFiltradas.length > 0 && !flashcardSesionCompletada"
             class="max-w-3xl mx-auto space-y-4">

          <!-- BARRA DE PROGRESO DE LA SESIÓN -->
          <div class="glass-card p-3 rounded-2xl border border-gray-800/90 flex items-center justify-between gap-4 text-xs">
            <div class="flex items-center space-x-2 shrink-0">
              <span class="font-extrabold text-white text-xs" x-text="'Tarjeta ' + (flashcardIndice + 1) + ' / ' + flashcardsFiltradas.length"></span>
              <span class="text-[10px] px-2 py-0.2 rounded-full bg-gray-800 text-gray-300 font-mono" x-text="flashcardsPorcentajeProgreso + '%'"></span>
            </div>

            <!-- BARRA VISUAL -->
            <div class="flex-1 h-2 bg-gray-950 rounded-full overflow-hidden border border-gray-800">
              <div class="h-full bg-gradient-to-r from-purple-500 to-indigo-500 transition-all duration-300 rounded-full"
                   :style="'width: ' + flashcardsPorcentajeProgreso + '%'"></div>
            </div>

            <!-- CONTADORES DE ESTUDIO -->
            <div class="flex items-center gap-1.5 shrink-0 text-[11px] font-bold">
              <span class="px-1.5 py-0.5 rounded-lg bg-emerald-500/15 text-emerald-300 border border-emerald-500/30" title="Dominadas" x-text="'🟢 ' + flashcardStatsSesion.facil"></span>
              <span class="px-1.5 py-0.5 rounded-lg bg-amber-500/15 text-amber-300 border border-amber-500/30" title="En repaso" x-text="'🟡 ' + flashcardStatsSesion.medio"></span>
              <span class="px-1.5 py-0.5 rounded-lg bg-rose-500/15 text-rose-300 border border-rose-500/30" title="Difíciles" x-text="'🔴 ' + flashcardStatsSesion.dificil"></span>
            </div>
          </div>

          <!-- TARJETA INTERACTIVA CON GIRO 3D REAL (FLIP) -->
          <div class="flashcard-perspective cursor-pointer select-none"
               @click="flashcardVolteada = !flashcardVolteada">
            <div class="flashcard-inner rounded-3xl" :class="flashcardVolteada ? 'flipped' : ''">

              <!-- CARA FRONTAL (ANVERSO - PREGUNTA) -->
              <div class="flashcard-face p-7 sm:p-9 rounded-3xl border border-gray-700/80 bg-gradient-to-b from-gray-900/98 to-gray-950/95 shadow-2xl backdrop-blur-xl">
                <div>
                  <div class="flex items-center justify-between pb-3.5 mb-4 border-b border-gray-800">
                    <div class="flex items-center space-x-2">
                      <span class="w-2.5 h-2.5 rounded-full bg-cyan-400 animate-pulse"></span>
                      <span class="font-extrabold uppercase tracking-wider text-[11px] text-cyan-300 flex items-center gap-1.5">
                        <i class="fa-solid fa-circle-question text-xs"></i>
                        <span>Pregunta de Concepto</span>
                      </span>
                    </div>
                    <span class="text-[10px] font-bold px-2.5 py-0.5 rounded-full bg-gray-800 text-gray-300 uppercase tracking-wider border border-gray-700"
                          x-text="tarjetaActual?.nivel || 'Examen'"></span>
                  </div>

                  <p class="text-xs font-semibold text-purple-400 mb-2 flex items-center gap-1.5">
                    <i class="fa-solid fa-book-bookmark text-[10px]"></i>
                    <span x-text="tarjetaActual?.asignatura"></span>
                  </p>

                  <h3 class="text-base sm:text-xl font-bold text-white leading-relaxed mt-2" x-text="tarjetaActual?.anverso"></h3>
                </div>

                <div class="pt-4 border-t border-gray-800 flex items-center justify-between text-xs text-gray-400">
                  <span class="text-[11px] text-gray-400 flex items-center gap-1.5">
                    <i class="fa-solid fa-rotate text-cyan-400"></i>
                    <span>Haz clic o pulsa <kbd class="px-1.5 py-0.5 rounded bg-gray-800 border border-gray-700 text-gray-200 font-mono text-[10px]">Espacio</kbd> para voltear</span>
                  </span>
                  <span class="text-xs font-bold text-cyan-300 flex items-center gap-1">
                    <span>Ver respuesta</span>
                    <i class="fa-solid fa-arrow-right text-[10px]"></i>
                  </span>
                </div>
              </div>

              <!-- CARA TRASERA (REVERSO - RESPUESTA TÉCNICA) -->
              <div class="flashcard-face flashcard-back p-7 sm:p-9 rounded-3xl border border-purple-500/50 bg-gradient-to-b from-[#181126]/98 to-gray-950/95 shadow-2xl backdrop-blur-xl">
                <div>
                  <div class="flex items-center justify-between pb-3.5 mb-4 border-b border-purple-500/20">
                    <div class="flex items-center space-x-2">
                      <span class="w-2.5 h-2.5 rounded-full bg-emerald-400"></span>
                      <span class="font-extrabold uppercase tracking-wider text-[11px] text-emerald-300 flex items-center gap-1.5">
                        <i class="fa-solid fa-lightbulb text-xs"></i>
                        <span>Respuesta Técnica Oficial</span>
                      </span>
                    </div>
                    <span class="text-[10px] font-bold px-2.5 py-0.5 rounded-full bg-purple-500/20 text-purple-300 border border-purple-500/30 font-mono"
                          x-text="'ID: ' + (tarjetaActual?.id || '')"></span>
                  </div>

                  <div class="markdown-content text-sm sm:text-base leading-relaxed text-gray-100 max-h-[220px] overflow-y-auto pr-1"
                       x-html="renderMarkdown(tarjetaActual?.reverso || '')"></div>
                </div>

                <div class="pt-3 border-t border-purple-500/20 flex items-center justify-between text-xs text-purple-300/80">
                  <span class="text-[11px]">Califica con <kbd class="px-1 rounded bg-black/40 font-mono">1</kbd>, <kbd class="px-1 rounded bg-black/40 font-mono">2</kbd>, <kbd class="px-1 rounded bg-black/40 font-mono">3</kbd></span>
                  <span class="text-[11px] text-gray-400">Clic para volver a la pregunta</span>
                </div>
              </div>

            </div>
          </div>

          <!-- BOTONES DE CALIFICACIÓN ESPACIADA (ESTILO ANKI) -->
          <div class="space-y-2 pt-1" x-show="flashcardVolteada" x-transition.opacity.duration.200ms>
            <p class="text-center text-[11px] text-gray-400 font-medium">¿Qué tal te la sabías?</p>
            <div class="grid grid-cols-3 gap-3">
              <button @click="calificarTarjeta('dificil')"
                      class="py-3 px-3 rounded-2xl bg-rose-500/15 hover:bg-rose-500/25 text-rose-300 font-bold text-xs transition border border-rose-500/30 flex flex-col items-center justify-center gap-1 shadow-sm group">
                <span class="text-base group-hover:scale-110 transition">🔴 Difícil</span>
                <span class="text-[10px] text-rose-300/70 font-normal">Repetir pronto <kbd class="text-[9px] bg-rose-950/60 px-1 rounded font-mono">[1]</kbd></span>
              </button>
              <button @click="calificarTarjeta('medio')"
                      class="py-3 px-3 rounded-2xl bg-amber-500/15 hover:bg-amber-500/25 text-amber-300 font-bold text-xs transition border border-amber-500/30 flex flex-col items-center justify-center gap-1 shadow-sm group">
                <span class="text-base group-hover:scale-110 transition">🟡 Regular</span>
                <span class="text-[10px] text-amber-300/70 font-normal">En repaso <kbd class="text-[9px] bg-amber-950/60 px-1 rounded font-mono">[2]</kbd></span>
              </button>
              <button @click="calificarTarjeta('facil')"
                      class="py-3 px-3 rounded-2xl bg-emerald-500/15 hover:bg-emerald-500/25 text-emerald-300 font-bold text-xs transition border border-emerald-500/30 flex flex-col items-center justify-center gap-1 shadow-sm group">
                <span class="text-base group-hover:scale-110 transition">🟢 Fácil</span>
                <span class="text-[10px] text-emerald-300/70 font-normal">Dominada <kbd class="text-[9px] bg-emerald-950/60 px-1 rounded font-mono">[3]</kbd></span>
              </button>
            </div>
          </div>

          <!-- NAVEGACIÓN Y ATAJOS -->
          <div class="flex items-center justify-between pt-2">
            <button @click="anteriorFlashcard()" :disabled="flashcardIndice === 0"
                    class="px-4 py-2 bg-gray-900 hover:bg-gray-800 border border-gray-800 rounded-xl text-xs text-gray-300 font-semibold disabled:opacity-30 transition flex items-center gap-1.5">
              <i class="fa-solid fa-arrow-left text-[10px]"></i>
              <span>Anterior</span>
            </button>
            <div class="hidden sm:flex items-center space-x-2 text-[10px] text-gray-500">
              <span>Atajos:</span>
              <kbd class="px-1.5 py-0.5 rounded bg-gray-900 border border-gray-800 text-gray-400 font-mono">Espacio</kbd>
              <kbd class="px-1.5 py-0.5 rounded bg-gray-900 border border-gray-800 text-gray-400 font-mono">←</kbd>
              <kbd class="px-1.5 py-0.5 rounded bg-gray-900 border border-gray-800 text-gray-400 font-mono">→</kbd>
            </div>
            <button @click="siguienteFlashcard()"
                    class="px-5 py-2 bg-purple-600/30 hover:bg-purple-600/40 text-purple-200 border border-purple-500/40 rounded-xl text-xs font-bold transition flex items-center gap-1.5">
              <span>Siguiente</span>
              <i class="fa-solid fa-arrow-right text-[10px]"></i>
            </button>
          </div>
        </div>

        <!-- ==================== PANTALLA RESUMEN DE SESIÓN COMPLETADA ==================== -->
        <div x-show="flashcardVistaModo === 'estudio' && flashcardSesionCompletada"
             class="max-w-xl mx-auto glass p-8 rounded-3xl border border-purple-500/30 text-center space-y-5 shadow-2xl">
          <div class="w-16 h-16 rounded-full bg-emerald-500/20 text-emerald-400 flex items-center justify-center text-3xl mx-auto shadow-lg shadow-emerald-500/20 animate-bounce">
            <i class="fa-solid fa-trophy"></i>
          </div>
          <div>
            <h3 class="text-xl font-black text-white">¡Mazo Completado con Éxito!</h3>
            <p class="text-xs text-gray-400 mt-1">Has repasado todas las tarjetas disponibles de esta sesión.</p>
          </div>

          <!-- RESUMEN DE RETENCIÓN -->
          <div class="grid grid-cols-3 gap-3 p-4 bg-gray-950/80 rounded-2xl border border-gray-800 text-center">
            <div>
              <p class="text-xl font-black text-emerald-400 font-mono" x-text="flashcardStatsSesion.facil"></p>
              <p class="text-[10px] text-gray-400 mt-0.5">Dominadas</p>
            </div>
            <div>
              <p class="text-xl font-black text-amber-400 font-mono" x-text="flashcardStatsSesion.medio"></p>
              <p class="text-[10px] text-gray-400 mt-0.5">En repaso</p>
            </div>
            <div>
              <p class="text-xl font-black text-rose-400 font-mono" x-text="flashcardStatsSesion.dificil"></p>
              <p class="text-[10px] text-gray-400 mt-0.5">Difíciles</p>
            </div>
          </div>

          <div class="flex items-center justify-center gap-3 pt-2">
            <button @click="reiniciarMazoFlashcards()"
                    class="px-5 py-2.5 rounded-xl bg-purple-600 hover:bg-purple-500 text-white font-bold text-xs transition flex items-center gap-2 shadow-lg shadow-purple-500/20">
              <i class="fa-solid fa-rotate-left"></i>
              <span>Repasar el Mazo de Nuevo</span>
            </button>
            <button @click="flashcardVistaModo = 'catalogo'"
                    class="px-5 py-2.5 rounded-xl bg-gray-800 hover:bg-gray-700 text-gray-200 font-semibold text-xs transition">
              Ver Todas las Tarjetas
            </button>
          </div>
        </div>

        <!-- ==================== MODO 2: CATÁLOGO / VISTA DE MAZO COMPLETO EN CUADRÍCULA ==================== -->
        <div x-show="flashcardVistaModo === 'catalogo' && flashcardsFiltradas.length > 0" class="space-y-4">
          <!-- BUSCADOR INTERNO DENTRO DEL MAZO -->
          <div class="flex items-center justify-between gap-3">
            <div class="relative flex-1 max-w-md">
              <i class="fa-solid fa-magnifying-glass absolute left-3 top-2.5 text-xs text-gray-500"></i>
              <input type="text"
                     x-model="flashcardBusqueda"
                     placeholder="Buscar en preguntas o respuestas del mazo..."
                     class="w-full bg-gray-900 text-white placeholder-gray-500 text-xs pl-8 pr-3 py-2 rounded-xl border border-gray-800 focus:outline-none focus:border-purple-500">
            </div>
            <span class="text-xs text-gray-400" x-text="flashcardsFiltradas.length + ' tarjetas encontradas'"></span>
          </div>

          <!-- GRID DE TARJETAS -->
          <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            <template x-for="(card, idx) in flashcardsFiltradas" :key="card.id">
              <div class="glass-card p-5 rounded-2xl border border-gray-800/80 hover:border-purple-500/40 transition flex flex-col justify-between group space-y-3">
                <div>
                  <div class="flex items-center justify-between text-[10px] text-gray-400 mb-2">
                    <span class="font-extrabold uppercase text-purple-400 tracking-wider" x-text="card.asignatura"></span>
                    <span class="px-2 py-0.5 rounded-full bg-gray-800 text-gray-300 font-mono" x-text="'#' + (idx + 1)"></span>
                  </div>
                  <h4 class="text-xs sm:text-sm font-bold text-white leading-snug group-hover:text-purple-200 transition" x-text="card.anverso"></h4>
                  <div class="mt-2.5 pt-2.5 border-t border-gray-800/80 text-xs text-gray-300 leading-relaxed markdown-content max-h-36 overflow-y-auto pr-1"
                       x-html="renderMarkdown(card.reverso)"></div>
                </div>
                <div class="flex items-center justify-between pt-2 border-t border-gray-800/60 text-[10px] text-gray-400">
                  <span class="px-2 py-0.5 rounded-md bg-gray-950 font-mono uppercase" x-text="card.nivel || 'Básico'"></span>
                  <button @click="flashcardIndice = idx; flashcardVolteada = false; flashcardSesionCompletada = false; flashcardVistaModo = 'estudio'"
                          class="text-purple-400 hover:text-purple-300 font-bold flex items-center gap-1 transition">
                    <span>Estudiar esta</span>
                    <i class="fa-solid fa-arrow-right text-[9px]"></i>
                  </button>
                </div>
              </div>
            </template>
          </div>
        </div>

        <!-- ESTADO VACÍO -->
        <div x-show="flashcardsFiltradas.length === 0" class="text-center py-20 text-gray-500 text-xs bg-gray-900/30 rounded-3xl border border-gray-800/60 max-w-lg mx-auto space-y-3">
          <div class="w-14 h-14 rounded-2xl bg-purple-500/10 text-purple-400 flex items-center justify-center text-2xl mx-auto">
            <i class="fa-solid fa-clone"></i>
          </div>
          <div>
            <p class="text-sm font-bold text-gray-300">No hay tarjetas de repaso para este filtro</p>
            <p class="text-xs text-gray-500 mt-1">Genera nuevas preguntas tipo examen al instante con inteligencia artificial.</p>
          </div>
          <button @click="abrirModalGenerarCards()"
                  class="px-4 py-2 bg-purple-600 hover:bg-purple-500 text-white font-bold rounded-xl text-xs transition inline-flex items-center gap-2">
            <i class="fa-solid fa-wand-magic-sparkles text-xs"></i>
            <span>Generar Flashcards con IA</span>
          </button>
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
                <span x-text="(chatModeloActualNombre || 'Gemini 3.5 Flash') + ' · Memoria Conversacional Activa'"></span>
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
                <i class="fa-solid fa-sliders text-[11px]"></i>
              </button>
            </div>

            <!-- BOTÓN AUTO-LECTURA DE VOZ ON/OFF -->
            <button @click="autoVozActiva = !autoVozActiva; localStorage.setItem('unir_auto_voz', autoVozActiva)"
                    :class="autoVozActiva ? 'bg-cyan-500/20 border-cyan-500/40 text-cyan-300' : 'bg-gray-800 border-gray-700 text-gray-400 hover:text-gray-200'"
                    class="text-xs px-2.5 py-1.5 rounded-xl border transition flex items-center space-x-1.5 shadow-sm"
                    :title="autoVozActiva ? 'Lectura por voz activada: NOVA leerá sus respuestas automáticamente (haz clic para desactivar)' : 'Activar lectura por voz automática de las respuestas de NOVA'">
              <i class="fa-solid text-[11px]" :class="autoVozActiva ? 'fa-volume-high text-cyan-400' : 'fa-volume-xmark text-gray-500'"></i>
              <span class="hidden sm:inline font-medium" x-text="autoVozActiva ? 'Voz: ON' : 'Voz: OFF'"></span>
            </button>

            <!-- BOTÓN BYOK PARA USAR CLAVES PROPIAS DE IA -->
            <button @click="abrirModalApiKey()"
                    :class="(userGeminiKey || userOpenrouterKey || userGroqKey) ? 'bg-emerald-500/15 border-emerald-500/40 text-emerald-300' : 'bg-gray-800 border-gray-700 text-gray-300 hover:text-cyan-300 hover:border-cyan-500/30'"
                    class="text-xs px-2.5 py-1.5 rounded-xl border transition flex items-center space-x-1.5 shadow-sm"
                    :title="(userGeminiKey || userOpenrouterKey || userGroqKey) ? 'Claves de IA configuradas y activas' : 'Configurar claves gratuitas de IA (Gemini, OpenRouter, Groq)'">
              <span class="h-2 w-2 rounded-full" :class="(userGeminiKey || userOpenrouterKey || userGroqKey) ? 'bg-emerald-400 animate-pulse' : 'bg-gray-500'"></span>
              <span class="hidden sm:inline font-medium" x-text="(userGeminiKey || userOpenrouterKey || userGroqKey) ? 'Claves IA' : 'Claves IA'"></span>
              <i class="fa-solid fa-key text-[10px]" :class="(userGeminiKey || userOpenrouterKey || userGroqKey) ? 'text-emerald-400' : 'text-gray-400'"></i>
            </button>

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
                    <!-- BOTÓN REPRODUCIR / DETENER VOZ -->
                    <button @click="alternarVozMensaje(idx, msg.texto)"
                            class="px-2 py-0.5 rounded-lg transition flex items-center space-x-1"
                            :class="vozMensajeActivoIdx === idx ? 'bg-cyan-500/30 text-cyan-300 border border-cyan-500/40 animate-pulse' : 'bg-gray-700/60 hover:bg-gray-700 text-gray-300'"
                            :title="vozMensajeActivoIdx === idx ? 'Detener lectura en voz alta' : 'Escuchar respuesta de NOVA en voz alta'">
                      <i :class="vozMensajeActivoIdx === idx ? 'fa-solid fa-square text-[9px] text-cyan-400' : 'fa-solid fa-volume-high text-[10px]'"></i>
                      <span class="text-[10px]" x-text="vozMensajeActivoIdx === idx ? 'Detener' : 'Escuchar'"></span>
                    </button>
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
            <div class="bg-gray-800 p-4 rounded-2xl rounded-tl-none text-gray-400 text-sm flex items-center justify-between gap-3 shadow-md border border-gray-700/60">
              <div class="flex items-center space-x-2.5">
                <i class="fa-solid fa-spinner fa-spin text-cyan-400"></i>
                <span class="text-gray-300">NOVA está razonando la respuesta técnica...</span>
              </div>
              <button @click="detenerConsultaChat()"
                      class="px-2.5 py-1 bg-rose-500/20 hover:bg-rose-500/30 text-rose-300 border border-rose-500/30 rounded-lg text-xs font-semibold transition flex items-center space-x-1.5"
                      title="Detener respuesta en curso">
                <i class="fa-solid fa-stop text-[10px]"></i>
                <span>Detener</span>
              </button>
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
          <input type="text" x-model="chatInput" @keydown.enter="if(!chatCargando) enviarMensajeChat()"
                 :placeholder="'Haz cualquier pregunta técnica o sobre lo que dijo el docente en las videoclases...'"
                 class="flex-1 bg-gray-800 text-white placeholder-gray-500 text-xs px-4 py-3 rounded-xl border border-gray-700 focus:outline-none focus:border-cyan-500">
          <!-- BOTÓN DETENER (activo mientras chatCargando) -->
          <button x-show="chatCargando" @click="detenerConsultaChat()"
                  class="px-5 py-3 bg-rose-600 hover:bg-rose-500 text-white font-bold rounded-xl text-xs hover:shadow-lg hover:shadow-rose-500/20 transition flex items-center space-x-1.5 shadow"
                  title="Detener la respuesta en curso">
            <i class="fa-solid fa-stop text-[11px]"></i>
            <span>Detener</span>
          </button>
          <!-- BOTÓN ENVIAR (activo cuando NO está cargando) -->
          <button x-show="!chatCargando" @click="enviarMensajeChat()" :disabled="!chatInput.trim()"
                  class="px-5 py-3 bg-gradient-to-r from-cyan-500 to-indigo-600 text-white font-semibold rounded-xl text-xs hover:shadow-lg hover:shadow-cyan-500/20 disabled:opacity-50 transition flex items-center space-x-1.5">
            <i class="fa-solid fa-paper-plane mr-1.5"></i>
            <span>Enviar</span>
          </button>
        </div>
      </div>

      <!-- ==================== 5. VISTA VIDEOCLASES Y TRANSCRIPCIONES ==================== -->
      <template x-if="tab === 'clases'">
        <div class="space-y-6">
          
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
                      <span x-text="formatearFechaDDMMAAAA(clase.fecha)"></span>
                    </span>
                  </div>
                  <h4 class="font-bold text-white text-sm mt-3 group-hover:text-amber-300 transition break-words" x-text="clase.titulo"></h4>
                  <p class="text-xs text-gray-400 mt-2 line-clamp-3 leading-relaxed break-words" x-text="clase.summary || 'Transcripción oficial completa disponible con registro detallado de la intervención del profesor y dudas de los estudiantes.'"></p>
                </div>

                  <div class="mt-4 pt-3 border-t border-gray-800/60 flex flex-wrap items-center justify-between gap-2">
                    <div class="flex items-center space-x-1.5 flex-wrap gap-1.5">
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
                      <button @click="verContenidoArchivo(clase.archivo)" class="px-2.5 py-1.5 bg-gray-800 hover:bg-gray-700 text-gray-300 rounded-xl text-xs font-semibold transition flex items-center space-x-1 border border-gray-700" title="Ver transcripción literal en texto">
                        <i class="fa-solid fa-file-lines text-xs text-gray-400"></i>
                        <span>Texto</span>
                      </button>
                    </div>
                    <button @click="preguntarSobreClase(clase)" class="px-3 py-1.5 bg-cyan-500/20 hover:bg-cyan-500/30 text-cyan-300 border border-cyan-500/30 rounded-xl text-xs font-semibold transition flex items-center space-x-1.5 shadow-sm" title="Preguntar dudas a NOVA en el chat">
                      <i class="fa-solid fa-robot text-xs"></i>
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
      </template>

      <!-- ==================== 8. PANEL DE ADMINISTRACIÓN ==================== -->
      <div x-show="tab === 'admin' && usuarioActual?.es_admin" x-transition class="space-y-6">
        <!-- HEADER PANEL ADMIN -->
        <div class="glass p-6 rounded-3xl flex flex-wrap items-center justify-between gap-4 border border-amber-500/30 bg-gradient-to-r from-amber-500/10 via-transparent to-transparent">
          <div class="flex items-center space-x-4">
            <div class="w-12 h-12 rounded-2xl bg-amber-500/20 border border-amber-500/40 flex items-center justify-center text-amber-400 text-xl shadow-lg shadow-amber-500/10">
              <i class="fa-solid fa-shield-halved"></i>
            </div>
            <div>
              <h2 class="text-2xl font-black text-white">Panel de Administración</h2>
              <p class="text-xs text-gray-400 mt-0.5">Control de acceso, usuarios registrados y permisos del Hub</p>
            </div>
          </div>
          <button @click="cargarAdminUsuarios()" class="px-3.5 py-2 rounded-xl bg-gray-800 hover:bg-gray-700 text-gray-200 text-xs font-semibold flex items-center space-x-2 transition border border-gray-700">
            <i class="fa-solid fa-rotate" :class="adminCargando ? 'fa-spin text-amber-400' : ''"></i>
            <span>Actualizar</span>
          </button>
        </div>

        <!-- TARJETAS DE MÉTRICAS -->
        <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
          <div class="glass p-5 rounded-2xl border border-gray-800 flex items-center justify-between">
            <div>
              <p class="text-xs text-gray-400 font-medium">Total Usuarios</p>
              <h3 class="text-2xl font-black text-white mt-1" x-text="adminUsuarios.length"></h3>
            </div>
            <div class="w-10 h-10 rounded-xl bg-gray-800/80 text-gray-300 flex items-center justify-center text-base">
              <i class="fa-solid fa-users"></i>
            </div>
          </div>

          <div class="glass p-5 rounded-2xl border border-emerald-500/20 bg-emerald-500/5 flex items-center justify-between">
            <div>
              <p class="text-xs text-emerald-400 font-medium">Usuarios Activos</p>
              <h3 class="text-2xl font-black text-emerald-300 mt-1" x-text="adminUsuarios.filter(u => u.activo === 1).length"></h3>
            </div>
            <div class="w-10 h-10 rounded-xl bg-emerald-500/10 text-emerald-400 flex items-center justify-center text-base border border-emerald-500/20">
              <i class="fa-solid fa-user-check"></i>
            </div>
          </div>

          <div class="glass p-5 rounded-2xl border border-amber-500/20 bg-amber-500/5 flex items-center justify-between">
            <div>
              <p class="text-xs text-amber-400 font-medium">Pendientes Acceso</p>
              <h3 class="text-2xl font-black text-amber-300 mt-1" x-text="adminPendientesCount"></h3>
            </div>
            <div class="w-10 h-10 rounded-xl bg-amber-500/10 text-amber-400 flex items-center justify-center text-base border border-amber-500/20">
              <i class="fa-solid fa-user-clock"></i>
            </div>
          </div>

          <div @click="irASeccion('feedback')" class="glass p-5 rounded-2xl border border-cyan-500/20 bg-cyan-500/5 flex items-center justify-between cursor-pointer hover:border-cyan-500/50 hover:bg-cyan-500/10 transition">
            <div>
              <p class="text-xs text-cyan-400 font-medium">Buzón Incidencias</p>
              <h3 class="text-2xl font-black text-cyan-300 mt-1" x-text="feedbackPendientesCount + ' pendientes'"></h3>
            </div>
            <div class="w-10 h-10 rounded-xl bg-cyan-500/10 text-cyan-400 flex items-center justify-center text-base border border-cyan-500/20">
              <i class="fa-solid fa-bullhorn"></i>
            </div>
          </div>
        </div>

        <!-- AVISO SI HAY PENDIENTES -->
        <div x-show="adminPendientesCount > 0" class="p-4 rounded-2xl bg-amber-500/10 border border-amber-500/30 flex items-center justify-between gap-4">
          <div class="flex items-center space-x-3 text-xs text-amber-200">
            <i class="fa-solid fa-triangle-exclamation text-amber-400 text-base"></i>
            <span>Hay <strong x-text="adminPendientesCount"></strong> usuario(s) esperando tu aprobación para poder entrar al Hub.</span>
          </div>
        </div>

        <!-- TABLA DE USUARIOS -->
        <div class="glass rounded-3xl border border-gray-800 overflow-hidden">
          <div class="p-4 sm:p-5 border-b border-gray-800 flex items-center justify-between">
            <h3 class="font-bold text-white text-sm flex items-center space-x-2">
              <i class="fa-solid fa-address-book text-amber-400"></i>
              <span>Cuentas Registradas</span>
            </h3>
            <span class="text-xs text-gray-500" x-text="adminUsuarios.length + ' usuario(s)'"></span>
          </div>

          <div class="overflow-x-auto">
            <table class="w-full text-left text-xs">
              <thead class="bg-gray-900/60 text-gray-400 uppercase text-[10px] tracking-wider border-b border-gray-800/80">
                <tr>
                  <th class="py-3 px-4">Usuario</th>
                  <th class="py-3 px-4">Nombre</th>
                  <th class="py-3 px-4">Rol</th>
                  <th class="py-3 px-4">Estado</th>
                  <th class="py-3 px-4">Registro / Acceso</th>
                  <th class="py-3 px-4 text-right">Acciones</th>
                </tr>
              </thead>
              <tbody class="divide-y divide-gray-800/60 text-gray-300">
                <template x-for="u in adminUsuarios" :key="u.id">
                  <tr class="hover:bg-gray-800/30 transition">
                    <td class="py-3 px-4 font-bold text-white flex items-center space-x-2">
                      <div class="w-7 h-7 rounded-lg bg-gray-800 text-gray-300 flex items-center justify-center text-xs font-bold shrink-0 uppercase"
                           x-text="u.username.substring(0, 2)"></div>
                      <span x-text="u.username"></span>
                    </td>
                    <td class="py-3 px-4 text-gray-300 font-medium" x-text="u.nombre"></td>
                    <td class="py-3 px-4">
                      <span class="px-2 py-0.5 rounded-full text-[10px] font-bold uppercase tracking-wider"
                            :class="u.rol === 'admin' ? 'bg-amber-500/20 text-amber-300 border border-amber-500/30' : 'bg-cyan-500/10 text-cyan-400 border border-cyan-500/20'"
                            x-text="u.rol"></span>
                      <span x-show="u.tiene_key" class="ml-1.5 inline-flex items-center gap-1 px-1.5 py-0.5 rounded-full text-[9px] font-medium bg-cyan-500/15 text-cyan-300 border border-cyan-500/30" title="Tiene su propia API Key configurada">
                        <i class="fa-solid fa-key text-[8px]"></i> BYOK
                      </span>
                    </td>
                    <td class="py-3 px-4">
                      <span x-show="u.activo === 1" class="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-[10px] font-semibold bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">
                        <span class="w-1.5 h-1.5 rounded-full bg-emerald-400"></span> Activo
                      </span>
                      <span x-show="u.activo === 0" class="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-[10px] font-semibold bg-amber-500/10 text-amber-400 border border-amber-500/30 animate-pulse">
                        <span class="w-1.5 h-1.5 rounded-full bg-amber-400"></span> Pendiente
                      </span>
                      <span x-show="u.activo === -1" class="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-[10px] font-semibold bg-rose-500/10 text-rose-400 border border-rose-500/20">
                        <span class="w-1.5 h-1.5 rounded-full bg-rose-400"></span> Bloqueado
                      </span>
                    </td>
                    <td class="py-3 px-4 text-gray-400 text-[11px]">
                      <div x-text="'Alta: ' + u.creado_en.split(' ')[0]"></div>
                      <div class="text-[10px] text-gray-500" x-text="'Último: ' + u.ultimo_acceso"></div>
                    </td>
                    <td class="py-3 px-4 text-right space-x-1 whitespace-nowrap">
                      <!-- Botón Aprobar / Activar -->
                      <button x-show="u.activo !== 1" @click="cambiarEstadoUsuario(u.id, 1)"
                              title="Aprobar y activar acceso"
                              class="px-2.5 py-1 rounded-lg bg-emerald-500/20 hover:bg-emerald-500/30 text-emerald-300 border border-emerald-500/30 font-semibold transition">
                        <i class="fa-solid fa-check mr-1 text-[10px]"></i> Aprobar
                      </button>

                      <!-- Botón Bloquear -->
                      <button x-show="u.activo === 1 && u.rol !== 'admin'" @click="cambiarEstadoUsuario(u.id, -1)"
                              title="Bloquear acceso"
                              class="px-2 py-1 rounded-lg bg-gray-800 hover:bg-rose-500/20 text-gray-400 hover:text-rose-300 border border-gray-700 hover:border-rose-500/30 transition">
                        <i class="fa-solid fa-ban text-[10px]"></i>
                      </button>

                      <!-- Botón Eliminar -->
                      <button x-show="u.rol !== 'admin'" @click="eliminarUsuario(u.id, u.username)"
                              title="Eliminar usuario"
                              class="px-2 py-1 rounded-lg bg-gray-800 hover:bg-rose-600/30 text-gray-400 hover:text-rose-300 border border-gray-700 hover:border-rose-600/40 transition">
                        <i class="fa-solid fa-trash text-[10px]"></i>
                      </button>
                    </td>
                  </tr>
                </template>
              </tbody>
            </table>
          </div>
        </div>
      </div>

      <!-- ==================== BUZÓN DE INCIDENCIAS Y MEJORAS ==================== -->
      <div x-show="tab === 'feedback'" x-transition class="space-y-6">
        <!-- HEADER -->
        <div class="glass p-6 rounded-3xl flex flex-wrap items-center justify-between gap-4 border border-amber-500/30 bg-gradient-to-r from-amber-500/10 via-transparent to-transparent">
          <div class="flex items-center space-x-4">
            <div class="w-12 h-12 rounded-2xl bg-amber-500/20 border border-amber-500/40 flex items-center justify-center text-amber-400 text-xl shadow-lg shadow-amber-500/10">
              <i class="fa-solid fa-bullhorn"></i>
            </div>
            <div>
              <h2 class="text-2xl font-black text-white">Buzón de Incidencias y Mejoras</h2>
              <p class="text-xs text-gray-400 mt-0.5">Reporta fallos o propón mejoras para NOVA Study Hub. La administración revisa y actualiza cada reporte.</p>
            </div>
          </div>
          <button @click="cargarFeedback()" class="px-3.5 py-2 rounded-xl bg-gray-800 hover:bg-gray-700 text-gray-200 text-xs font-semibold flex items-center space-x-2 transition border border-gray-700">
            <i class="fa-solid fa-rotate" :class="feedbackCargando ? 'fa-spin text-amber-400' : ''"></i>
            <span>Actualizar</span>
          </button>
        </div>

        <!-- FORMULARIO NUEVO REPORTE -->
        <div class="glass p-6 rounded-3xl border border-gray-800 space-y-4">
          <div class="flex items-center justify-between border-b border-gray-800/80 pb-3">
            <h3 class="font-bold text-white text-sm flex items-center space-x-2">
              <i class="fa-solid fa-pen-to-square text-amber-400"></i>
              <span>Enviar un Reporte o Sugerencia</span>
            </h3>
            <span class="text-[11px] text-gray-400">Tu usuario: <strong class="text-gray-200" x-text="usuarioActual?.nombre || usuarioActual?.username || 'Estudiante'"></strong></span>
          </div>

          <!-- Selector de tipo (Fallo vs Mejora) -->
          <div class="flex gap-3">
            <button type="button" @click="feedbackNuevoTipo = 'fallo'"
                    :class="feedbackNuevoTipo === 'fallo' ? 'bg-rose-500/20 text-rose-300 border-rose-500/40 font-bold' : 'bg-gray-900/60 text-gray-400 border-gray-800 hover:text-gray-200'"
                    class="flex-1 py-2.5 px-4 rounded-xl border text-xs flex items-center justify-center space-x-2 transition">
              <i class="fa-solid fa-bug text-rose-400"></i>
              <span>Reportar un Fallo / Error</span>
            </button>
            <button type="button" @click="feedbackNuevoTipo = 'mejora'"
                    :class="feedbackNuevoTipo === 'mejora' ? 'bg-cyan-500/20 text-cyan-300 border-cyan-500/40 font-bold' : 'bg-gray-900/60 text-gray-400 border-gray-800 hover:text-gray-200'"
                    class="flex-1 py-2.5 px-4 rounded-xl border text-xs flex items-center justify-center space-x-2 transition">
              <i class="fa-solid fa-lightbulb text-cyan-400"></i>
              <span>Proponer una Mejora / Idea</span>
            </button>
          </div>

          <div class="grid grid-cols-1 sm:grid-cols-3 gap-4">
            <div class="sm:col-span-2">
              <label class="block text-xs font-semibold text-gray-300 mb-1.5">Título del reporte *</label>
              <input type="text" x-model="feedbackNuevoTitulo"
                     :placeholder="feedbackNuevoTipo === 'fallo' ? 'Ej: Error al reproducir audio en videoclase' : 'Ej: Añadir temporizador Pomodoro para tests'"
                     class="w-full bg-gray-900/90 text-gray-100 text-xs px-3.5 py-2.5 rounded-xl border border-gray-700 focus:outline-none focus:border-amber-500">
            </div>
            <div>
              <label class="block text-xs font-semibold text-gray-300 mb-1.5">Materia / Módulo (Opcional)</label>
              <select x-model="feedbackNuevoAsig" class="w-full bg-gray-900/90 text-gray-200 text-xs px-3 py-2.5 rounded-xl border border-gray-700 focus:outline-none focus:border-amber-500">
                <option value="">General / Todo el Hub</option>
                <template x-for="item in catalogoTotal" :key="item.nombre">
                  <option :value="item.nombre" x-text="item.nombre"></option>
                </template>
              </select>
            </div>
          </div>

          <div>
            <label class="block text-xs font-semibold text-gray-300 mb-1.5">Descripción detallada *</label>
            <textarea x-model="feedbackNuevoDesc" rows="3"
                      :placeholder="feedbackNuevoTipo === 'fallo' ? 'Explica qué ocurrió, qué botón pulsaste o qué esperabas...' : 'Describe cómo te imaginas esta nueva función o cambio...'"
                      class="w-full bg-gray-900/90 text-gray-100 text-xs p-3 rounded-xl border border-gray-700 focus:outline-none focus:border-amber-500 leading-relaxed"></textarea>
          </div>

          <!-- Mensajes de feedback -->
          <div x-show="feedbackMensajeExito" x-transition class="p-3 rounded-xl bg-emerald-500/15 border border-emerald-500/30 text-emerald-300 text-xs flex items-center space-x-2">
            <i class="fa-solid fa-circle-check text-emerald-400"></i>
            <span x-text="feedbackMensajeExito"></span>
          </div>
          <div x-show="feedbackMensajeError" x-transition class="p-3 rounded-xl bg-rose-500/15 border border-rose-500/30 text-rose-300 text-xs flex items-center space-x-2">
            <i class="fa-solid fa-triangle-exclamation text-rose-400"></i>
            <span x-text="feedbackMensajeError"></span>
          </div>

          <div class="flex justify-end pt-1">
            <button @click="enviarFeedback()" :disabled="feedbackEnviando"
                    class="px-5 py-2.5 rounded-xl bg-gradient-to-r from-amber-500 to-amber-600 hover:from-amber-400 hover:to-amber-500 text-black text-xs font-bold flex items-center space-x-2 shadow-lg shadow-amber-500/20 transition disabled:opacity-50">
              <i class="fa-solid fa-paper-plane" :class="feedbackEnviando ? 'fa-bounce' : ''"></i>
              <span x-text="feedbackEnviando ? 'Enviando reporte...' : 'Publicar en el Buzón'"></span>
            </button>
          </div>
        </div>

        <!-- BARRA DE FILTROS Y RESUMEN -->
        <div class="glass p-4 rounded-2xl border border-gray-800 flex flex-wrap items-center justify-between gap-4">
          <!-- Filtros de Tipo -->
          <div class="flex items-center space-x-2 text-xs">
            <span class="text-gray-400 font-semibold mr-1">Tipo:</span>
            <button @click="feedbackFiltroTipo = 'todos'"
                    :class="feedbackFiltroTipo === 'todos' ? 'bg-amber-500/20 text-amber-300 border-amber-500/30 font-bold' : 'text-gray-400 hover:text-gray-200 border-transparent'"
                    class="px-3 py-1.5 rounded-lg border transition">Todos</button>
            <button @click="feedbackFiltroTipo = 'fallo'"
                    :class="feedbackFiltroTipo === 'fallo' ? 'bg-rose-500/20 text-rose-300 border-rose-500/30 font-bold' : 'text-gray-400 hover:text-gray-200 border-transparent'"
                    class="px-3 py-1.5 rounded-lg border transition flex items-center space-x-1.5">
              <i class="fa-solid fa-bug text-rose-400 text-[10px]"></i>
              <span>Fallos</span>
            </button>
            <button @click="feedbackFiltroTipo = 'mejora'"
                    :class="feedbackFiltroTipo === 'mejora' ? 'bg-cyan-500/20 text-cyan-300 border-cyan-500/30 font-bold' : 'text-gray-400 hover:text-gray-200 border-transparent'"
                    class="px-3 py-1.5 rounded-lg border transition flex items-center space-x-1.5">
              <i class="fa-solid fa-lightbulb text-cyan-400 text-[10px]"></i>
              <span>Mejoras</span>
            </button>
          </div>

          <!-- Filtros de Estado -->
          <div class="flex items-center space-x-2 text-xs">
            <span class="text-gray-400 font-semibold mr-1">Estado:</span>
            <select x-model="feedbackFiltroEstado" class="bg-gray-900 text-gray-200 text-xs px-3 py-1.5 rounded-lg border border-gray-700 outline-none focus:border-amber-500">
              <option value="todos">Todos los estados</option>
              <option value="pendiente">🟡 Pendientes</option>
              <option value="en_proceso">🔵 En Proceso</option>
              <option value="resuelto">🟢 Resueltos</option>
              <option value="descartado">⚪ Descartados</option>
            </select>
          </div>
        </div>

        <!-- LISTA DE REPORTES -->
        <div class="space-y-4">
          <template x-for="rep in feedbackFiltrado()" :key="rep.id">
            <div class="glass p-5 rounded-2xl border transition hover:border-gray-700 space-y-3.5"
                 :class="rep.tipo === 'fallo' ? 'border-rose-500/20 bg-rose-500/[0.02]' : 'border-cyan-500/20 bg-cyan-500/[0.02]'">
              <!-- HEADER DE LA TARJETA -->
              <div class="flex flex-wrap items-center justify-between gap-2">
                <div class="flex items-center space-x-2">
                  <!-- Badge Tipo -->
                  <span x-show="rep.tipo === 'fallo'" class="px-2.5 py-1 rounded-full text-[10px] font-bold bg-rose-500/20 text-rose-300 border border-rose-500/30 flex items-center space-x-1">
                    <i class="fa-solid fa-bug text-[9px]"></i>
                    <span>Fallo Técnico</span>
                  </span>
                  <span x-show="rep.tipo === 'mejora'" class="px-2.5 py-1 rounded-full text-[10px] font-bold bg-cyan-500/20 text-cyan-300 border border-cyan-500/30 flex items-center space-x-1">
                    <i class="fa-solid fa-lightbulb text-[9px]"></i>
                    <span>Propuesta de Mejora</span>
                  </span>

                  <!-- Badge Estado -->
                  <span x-show="rep.estado === 'pendiente'" class="px-2 py-0.5 rounded-full text-[10px] font-semibold bg-amber-500/10 text-amber-300 border border-amber-500/30 flex items-center space-x-1">
                    <i class="fa-solid fa-clock text-[9px]"></i>
                    <span>Pendiente de revisión</span>
                  </span>
                  <span x-show="rep.estado === 'en_proceso'" class="px-2 py-0.5 rounded-full text-[10px] font-semibold bg-cyan-500/10 text-cyan-300 border border-cyan-500/30 flex items-center space-x-1">
                    <i class="fa-solid fa-spinner fa-spin text-[9px]"></i>
                    <span>En desarrollo</span>
                  </span>
                  <span x-show="rep.estado === 'resuelto'" class="px-2 py-0.5 rounded-full text-[10px] font-semibold bg-emerald-500/10 text-emerald-300 border border-emerald-500/30 flex items-center space-x-1">
                    <i class="fa-solid fa-check text-[9px]"></i>
                    <span>Resuelto</span>
                  </span>
                  <span x-show="rep.estado === 'descartado'" class="px-2 py-0.5 rounded-full text-[10px] font-semibold bg-gray-700/50 text-gray-400 border border-gray-600/50 flex items-center space-x-1">
                    <i class="fa-solid fa-xmark text-[9px]"></i>
                    <span>Descartado</span>
                  </span>

                  <!-- Asignatura opcional -->
                  <span x-show="rep.asignatura" class="px-2 py-0.5 rounded-full text-[10px] bg-gray-800 text-gray-300 border border-gray-700" x-text="rep.asignatura"></span>
                </div>

                <!-- Autor y Fecha -->
                <div class="text-[11px] text-gray-400 flex items-center space-x-2">
                  <span>Por <strong class="text-gray-200" x-text="rep.nombre || rep.username"></strong></span>
                  <span>•</span>
                  <span x-text="rep.creado_en"></span>
                </div>
              </div>

              <!-- CONTENIDO -->
              <div>
                <h4 class="font-bold text-white text-sm" x-text="rep.titulo"></h4>
                <p class="text-xs text-gray-300 mt-1.5 leading-relaxed whitespace-pre-line" x-text="rep.descripcion"></p>
              </div>

              <!-- RESPUESTA OFICIAL DE ALEX / ADMIN -->
              <div x-show="rep.respuesta_admin" class="p-3.5 rounded-xl bg-amber-500/10 border border-amber-500/30 space-y-1">
                <div class="flex items-center space-x-2 text-[11px] font-bold text-amber-300">
                  <i class="fa-solid fa-shield-halved text-amber-400"></i>
                  <span>Respuesta Oficial de la Administración:</span>
                </div>
                <p class="text-xs text-amber-100/90 leading-relaxed whitespace-pre-line" x-text="rep.respuesta_admin"></p>
              </div>

              <!-- ACCIONES DE ADMINISTRADOR (SOLO ALEX / ADMIN) -->
              <div x-show="usuarioActual?.es_admin" class="pt-2 border-t border-gray-800/80 flex flex-wrap items-center justify-between gap-3 text-xs">
                <div class="flex items-center space-x-1.5 flex-wrap gap-y-1">
                  <span class="text-gray-400 text-[11px] font-semibold mr-1">Cambiar Estado:</span>
                  <button @click="adminCambiarEstadoFeedback(rep.id, 'pendiente')"
                          class="px-2 py-1 rounded-md text-[10px] font-semibold transition"
                          :class="rep.estado === 'pendiente' ? 'bg-amber-500 text-black font-bold' : 'bg-gray-800 text-gray-300 hover:bg-gray-700'">Pendiente</button>
                  <button @click="adminCambiarEstadoFeedback(rep.id, 'en_proceso')"
                          class="px-2 py-1 rounded-md text-[10px] font-semibold transition"
                          :class="rep.estado === 'en_proceso' ? 'bg-cyan-500 text-black font-bold' : 'bg-gray-800 text-gray-300 hover:bg-gray-700'">En Proceso</button>
                  <button @click="adminCambiarEstadoFeedback(rep.id, 'resuelto')"
                          class="px-2 py-1 rounded-md text-[10px] font-semibold transition"
                          :class="rep.estado === 'resuelto' ? 'bg-emerald-500 text-black font-bold' : 'bg-gray-800 text-gray-300 hover:bg-gray-700'">Resuelto</button>
                  <button @click="adminCambiarEstadoFeedback(rep.id, 'descartado')"
                          class="px-2 py-1 rounded-md text-[10px] font-semibold transition"
                          :class="rep.estado === 'descartado' ? 'bg-gray-600 text-white font-bold' : 'bg-gray-800 text-gray-300 hover:bg-gray-700'">Descartado</button>
                </div>

                <div class="flex items-center space-x-2">
                  <button @click="feedbackEditandoRespId = (feedbackEditandoRespId === rep.id ? null : rep.id); feedbackTextoRespTemp = rep.respuesta_admin || ''"
                          class="px-2.5 py-1 rounded-md bg-gray-800 hover:bg-amber-500/20 text-gray-300 hover:text-amber-300 border border-gray-700 text-[11px] font-medium flex items-center space-x-1 transition">
                    <i class="fa-solid fa-reply text-[10px]"></i>
                    <span x-text="rep.respuesta_admin ? 'Editar respuesta' : 'Responder'"></span>
                  </button>
                  <button @click="adminEliminarFeedback(rep.id)"
                          class="px-2 py-1 rounded-md bg-gray-800 hover:bg-rose-500/20 text-gray-400 hover:text-rose-400 border border-gray-700 text-[11px] transition" title="Eliminar reporte">
                    <i class="fa-solid fa-trash text-[10px]"></i>
                  </button>
                </div>

                <!-- Input para redactar respuesta admin -->
                <div x-show="feedbackEditandoRespId === rep.id" x-transition class="w-full mt-2 pt-2 border-t border-gray-800 space-y-2">
                  <label class="block text-[11px] font-bold text-amber-300">Respuesta oficial del Administrador:</label>
                  <textarea x-model="feedbackTextoRespTemp" rows="2" placeholder="Escribe tu respuesta para el estudiante..."
                            class="w-full bg-gray-900 text-gray-200 text-xs p-2.5 rounded-xl border border-amber-500/40 focus:outline-none focus:border-amber-400"></textarea>
                  <div class="flex justify-end space-x-2">
                    <button @click="feedbackEditandoRespId = null" class="px-3 py-1 rounded-lg bg-gray-800 text-gray-400 hover:text-gray-200 text-xs">Cancelar</button>
                    <button @click="adminGuardarRespuestaFeedback(rep.id, rep.estado)"
                            class="px-3.5 py-1 rounded-lg bg-amber-500 hover:bg-amber-400 text-black font-bold text-xs flex items-center space-x-1">
                      <i class="fa-solid fa-check text-[10px]"></i>
                      <span>Guardar Respuesta</span>
                    </button>
                  </div>
                </div>
              </div>
            </div>
          </template>

          <div x-show="feedbackFiltrado().length === 0" class="text-center py-16 text-gray-500 text-xs glass rounded-2xl border border-gray-800">
            <i class="fa-solid fa-clipboard-check text-3xl mb-3 opacity-30 text-amber-400"></i>
            <p class="text-sm font-semibold text-gray-400">No hay reportes ni propuestas en esta categoría.</p>
            <p class="text-xs text-gray-500 mt-1">¡Sé el primero en proponer una idea o reportar cualquier detalle!</p>
          </div>
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

      <!-- Selector de Tipo de Generación: Ambos / Solo Guía / Solo Test -->
      <div>
        <label class="block text-xs font-bold text-gray-300 mb-2">¿Qué deseas generar con IA?</label>
        <div class="grid grid-cols-3 gap-2">
          <!-- Opción Ambos -->
          <button type="button" 
                  @click="generadorTipo = 'ambos'"
                  :class="generadorTipo === 'ambos' ? 'border-emerald-500 bg-emerald-500/15 text-emerald-300 ring-1 ring-emerald-500 font-bold' : 'border-gray-800 bg-gray-900/60 text-gray-400 hover:border-gray-700'"
                  class="p-2.5 rounded-2xl border text-center transition flex flex-col items-center justify-center space-y-1">
            <i class="fa-solid fa-wand-magic-sparkles text-sm" :class="generadorTipo === 'ambos' ? 'text-emerald-400' : 'text-gray-500'"></i>
            <span class="text-xs">Guía + Test</span>
            <span class="text-[10px] opacity-75">Completo</span>
          </button>

          <!-- Opción Solo Guía -->
          <button type="button" 
                  @click="generadorTipo = 'solo_guia'"
                  :class="generadorTipo === 'solo_guia' ? 'border-cyan-500 bg-cyan-500/15 text-cyan-300 ring-1 ring-cyan-500 font-bold' : 'border-gray-800 bg-gray-900/60 text-gray-400 hover:border-gray-700'"
                  class="p-2.5 rounded-2xl border text-center transition flex flex-col items-center justify-center space-y-1">
            <i class="fa-solid fa-book-bookmark text-sm" :class="generadorTipo === 'solo_guia' ? 'text-cyan-400' : 'text-gray-500'"></i>
            <span class="text-xs">Solo Guía</span>
            <span class="text-[10px] opacity-75">Apuntes MD</span>
          </button>

          <!-- Opción Solo Test -->
          <button type="button" 
                  @click="generadorTipo = 'solo_test'"
                  :class="generadorTipo === 'solo_test' ? 'border-purple-500 bg-purple-500/15 text-purple-300 ring-1 ring-purple-500 font-bold' : 'border-gray-800 bg-gray-900/60 text-gray-400 hover:border-gray-700'"
                  class="p-2.5 rounded-2xl border text-center transition flex flex-col items-center justify-center space-y-1">
            <i class="fa-solid fa-clipboard-question text-sm" :class="generadorTipo === 'solo_test' ? 'text-purple-400' : 'text-gray-500'"></i>
            <span class="text-xs">Solo Test</span>
            <span class="text-[10px] opacity-75">Simulacro</span>
          </button>
        </div>
      </div>

      <!-- Selector de Profundidad de la Guía -->
      <div>
        <label class="block text-xs font-bold text-gray-300 mb-2">
          <span x-text="generadorTipo === 'solo_test' ? 'Dificultad del Test:' : 'Profundidad y Rigor de la Guía:'"></span>
        </label>
        <div class="grid grid-cols-1 sm:grid-cols-3 gap-2.5">
          <!-- Opción Robusta (Recomendada por defecto) -->
          <div @click="generadorProfundidad = 'robusta'; generadorNumPreguntas = 15"
               :class="generadorProfundidad === 'robusta' ? 'border-purple-500 bg-purple-500/15 ring-2 ring-purple-500' : 'border-gray-800 bg-gray-900/60 hover:border-gray-700'"
               class="p-3 rounded-2xl border cursor-pointer transition flex flex-col justify-between space-y-1.5 order-first sm:order-last">
            <div class="flex items-center space-x-1.5">
              <i class="fa-solid fa-crown text-amber-400 text-xs"></i>
              <span class="text-xs font-bold text-purple-400">Robusta</span>
            </div>
            <p class="text-[11px] text-gray-200 font-semibold">Alto Rendimiento (Cornell & Active Recall)</p>
            <p class="text-[10px] text-gray-400 leading-tight">~4.500+ palabras, cajas interactivas de autoevaluación, chuleta de firmas/métodos, trampas de examen y 15 preguntas.</p>
          </div>

          <!-- Opción Media -->
          <div @click="generadorProfundidad = 'media'; generadorNumPreguntas = 10"
               :class="generadorProfundidad === 'media' ? 'border-blue-500 bg-blue-500/10 ring-1 ring-blue-500' : 'border-gray-800 bg-gray-900/60 hover:border-gray-700'"
               class="p-3 rounded-2xl border cursor-pointer transition flex flex-col justify-between space-y-1.5">
            <div class="flex items-center space-x-1.5">
              <i class="fa-solid fa-book-open text-blue-400 text-xs"></i>
              <span class="text-xs font-bold text-blue-400">Media</span>
            </div>
            <p class="text-[11px] text-gray-300 font-medium">Equilibrada</p>
            <p class="text-[10px] text-gray-400 leading-tight">~3.000 palabras, resumen ejecutivo, tabla de métodos clave, avisos docentes y 10 preguntas.</p>
          </div>

          <!-- Opción Corta -->
          <div @click="generadorProfundidad = 'corta'; generadorNumPreguntas = 5"
               :class="generadorProfundidad === 'corta' ? 'border-emerald-500 bg-emerald-500/10 ring-1 ring-emerald-500' : 'border-gray-800 bg-gray-900/60 hover:border-gray-700'"
               class="p-3 rounded-2xl border cursor-pointer transition flex flex-col justify-between space-y-1.5">
            <div class="flex items-center space-x-1.5">
              <i class="fa-solid fa-bolt text-emerald-400 text-xs"></i>
              <span class="text-xs font-bold text-emerald-400">Corta</span>
            </div>
            <p class="text-[11px] text-gray-300 font-medium">Repaso Rápido</p>
            <p class="text-[10px] text-gray-400 leading-tight">~1.500 palabras, chuleta rápida de sintaxis y test de 5 preguntas de última hora.</p>
          </div>
        </div>
      </div>

      <!-- Selector de Preguntas para el Test (oculto si es solo guía) -->
      <div x-show="generadorTipo !== 'solo_guia'" x-transition class="flex items-center justify-between bg-gray-900/80 p-2.5 rounded-xl border border-gray-800">
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
            <span x-text="generandoIaPaso === 1 ? 'Paso 1/3: Extrayendo PDF oficial y lecciones magistrales...' : (generandoIaPaso === 2 ? (generadorTipo === 'solo_test' ? 'Paso 2/3: Preparando contexto de examen...' : 'Paso 2/3: Redactando Guía de Estudio con NOVA...') : (generadorTipo === 'solo_guia' ? 'Paso 3/3: Guardando guía oficial...' : 'Paso 3/3: Diseñando Simulacro de Test (' + generadorNumPreguntas + ' preguntas de examen)...'))"></span>
          </span>
          <span class="text-[11px] font-mono text-emerald-400" x-text="generandoIaPaso + ' / 3'"></span>
        </div>
        <div class="w-full bg-gray-900 rounded-full h-1.5 overflow-hidden">
          <div class="bg-gradient-to-r from-emerald-500 to-cyan-400 h-full transition-all duration-500"
               :style="'width: ' + (generandoIaPaso === 1 ? '33%' : (generandoIaPaso === 2 ? '68%' : '95%'))"></div>
        </div>
      </div>

      <!-- AVISO DE CLAVE / TOKENS BYOK -->
      <div class="flex items-center justify-between bg-gray-900/80 p-2.5 rounded-xl border border-gray-800 text-xs">
        <div class="flex items-center space-x-2">
          <span class="h-2 w-2 rounded-full shrink-0" :class="userGeminiKey ? 'bg-emerald-400' : 'bg-amber-400'"></span>
          <span class="text-gray-300 text-[11px]" x-text="userGeminiKey ? 'Generando con tu API Key privada de Gemini' : 'Generando con la cuota compartida del Hub'"></span>
        </div>
        <button type="button" @click="abrirModalApiKey()" class="text-cyan-400 hover:text-cyan-300 text-[11px] font-semibold underline shrink-0 ml-2">
          <span x-text="userGeminiKey ? 'Cambiar mi key' : 'Poner mi clave gratis'"></span>
        </button>
      </div>

      <div class="pt-2 flex justify-end space-x-3">
        <button @click="modalGenerador = false" class="px-4 py-2 bg-gray-800 text-gray-300 rounded-xl text-xs font-semibold">Cancelar</button>
        <button @click="ejecutarGeneracionIa()" :disabled="generandoIa || !generadorTemaNombre.trim()" class="px-5 py-2 bg-gradient-to-r from-emerald-500 to-teal-600 text-gray-900 font-bold rounded-xl text-xs transition flex items-center space-x-2 disabled:opacity-50">
          <i :class="generandoIa ? 'fa-solid fa-spinner fa-spin' : 'fa-solid fa-bolt'"></i>
          <span x-text="generandoIa ? 'Generando con IA...' : (generadorTipo === 'solo_guia' ? 'Generar Guía de Estudio' : (generadorTipo === 'solo_test' ? 'Generar Examen Tipo Test' : 'Generar Guía y Test'))"></span>
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
          <button @click="cerrarModalVisor()" class="text-gray-400 hover:text-white p-2 rounded-lg" title="Cerrar visor">
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
          <button @click="if (confirm('¿Deseas salir del simulador de examen?')) { cerrarModalTest(); }" 
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
        <div class="test-enunciado-container" x-html="renderEnunciadoTest(testPreguntas[testIndice]?.enunciado)"></div>

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
              <p class="text-amber-100/90 text-xs leading-relaxed" x-html="renderTextoOpcion(obtenerPistaPregunta(testPreguntas[testIndice]))"></p>
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
                <span class="text-sm font-medium leading-relaxed pt-0.5" x-html="renderTextoOpcion(texto)"></span>
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
              <span x-text="testRespuestasUsuario[testIndice] === testPreguntas[testIndice]?.correcta ? '+1.0 Punto' : '-0.33 Puntos'"></span>
            </span>
          </div>

          <div class="bg-gray-900/90 rounded-xl p-3.5 border border-white/5 space-y-1.5 mt-2">
            <div class="flex items-center space-x-1.5 text-cyan-400 text-xs font-bold">
              <i class="fa-solid fa-graduation-cap text-[11px]"></i>
              <span>¿Por qué? Justificación técnica oficial:</span>
            </div>
            <div class="text-xs text-gray-200 leading-relaxed font-sans" x-html="renderTextoOpcion(testPreguntas[testIndice]?.justificacion)"></div>
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
        
        <!-- DESGLOSE OFICIAL (+1 / -0.33) -->
        <div class="flex flex-wrap items-center justify-center gap-3 py-2 px-4 bg-gray-900/90 rounded-2xl border border-gray-800 max-w-sm mx-auto text-xs font-mono">
          <span class="text-emerald-400 font-bold" x-text="'+' + testAciertos + ' Aciertos'"></span>
          <span class="text-rose-400 font-bold" x-text="'-' + testFallos + ' Fallos (-' + (testFallos * 0.33).toFixed(2) + ')'"></span>
          <span class="text-gray-400" x-text="testBlancos + ' Blanco'"></span>
        </div>
        <p class="text-[11px] text-gray-500">Baremo oficial de examen: Acierto (+1.0) • Fallo (-0.33) • Blanco (0.0)</p>
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

  <!-- ==================== MODAL: ENTRENAMIENTOS DE CÓDIGO OFICIALES Y GENERADOS CON IA ==================== -->
  <div x-show="modalEntrenamientos" x-transition class="fixed inset-0 z-50 flex items-center justify-center p-3 md:p-6 bg-black/85 backdrop-blur-md">
    <div class="glass w-full max-w-4xl max-h-[92vh] rounded-3xl flex flex-col overflow-hidden border border-purple-500/30 shadow-2xl bg-surface-1/95">
      <!-- HEADER -->
      <div class="p-4 sm:p-5 bg-gray-900/95 border-b border-gray-800 flex items-center justify-between gap-3">
        <div class="flex items-center space-x-3 min-w-0">
          <div class="w-10 h-10 rounded-xl bg-purple-500/20 border border-purple-500/40 text-purple-300 flex items-center justify-center text-lg shrink-0 shadow-sm">
            <i class="fa-solid fa-code"></i>
          </div>
          <div class="min-w-0">
            <div class="flex flex-wrap items-center gap-2">
              <span class="text-[10px] font-bold px-2 py-0.5 rounded-full bg-cyan-500/10 text-cyan-400 border border-cyan-500/20" x-text="entrenamientosTemaInfo?.asignatura"></span>
              <span class="text-[10px] font-bold px-2 py-0.5 rounded-full bg-purple-500/20 text-purple-300 border border-purple-500/40" x-text="(entrenamientosDatos?.total_oficiales || 5) + ' Retos Oficiales UNIR'"></span>
            </div>
            <h3 class="font-bold text-white text-sm sm:text-base truncate mt-0.5" x-text="'Tema ' + entrenamientosTemaInfo?.temaNum + ': ' + (entrenamientosTemaInfo?.temaTitulo || '')"></h3>
          </div>
        </div>
        <button @click="modalEntrenamientos = false" class="w-8 h-8 rounded-full bg-gray-800 hover:bg-gray-700 text-gray-400 hover:text-white flex items-center justify-center transition shrink-0">
          <i class="fa-solid fa-xmark text-sm"></i>
        </button>
      </div>

      <!-- CUERPO MODAL -->
      <div class="flex-1 overflow-y-auto p-4 sm:p-6 space-y-4">
        <!-- LOADING -->
        <div x-show="entrenamientosCargando" class="text-center py-16 space-y-3">
          <i class="fa-solid fa-spinner fa-spin text-3xl text-purple-400"></i>
          <p class="text-xs text-gray-400">Cargando entrenamientos prácticos del tema...</p>
        </div>

        <!-- ERROR -->
        <div x-show="!entrenamientosCargando && entrenamientosError" class="p-5 rounded-2xl bg-rose-500/10 border border-rose-500/30 text-center space-y-2">
          <i class="fa-solid fa-circle-exclamation text-2xl text-rose-400"></i>
          <p class="text-xs text-rose-200" x-text="entrenamientosError"></p>
        </div>

        <!-- PANEL DESPLEGABLE: GENERAR NUEVO RETO CON IA -->
        <div x-show="generadorIaAbierto" x-transition class="glass-card p-4 sm:p-5 rounded-2xl border border-emerald-500/30 bg-emerald-950/20 space-y-3 shadow-lg">
          <div class="flex items-center justify-between border-b border-emerald-500/20 pb-2.5">
            <div class="flex items-center gap-2">
              <div class="w-7 h-7 rounded-lg bg-emerald-500/20 text-emerald-300 flex items-center justify-center text-xs">
                <i class="fa-solid fa-wand-magic-sparkles"></i>
              </div>
              <h4 class="font-bold text-white text-xs sm:text-sm">Generador de Retos Prácticos con IA</h4>
            </div>
            <button @click="generadorIaAbierto = false" class="text-gray-400 hover:text-white text-xs">
              <i class="fa-solid fa-xmark"></i>
            </button>
          </div>

          <p class="text-[11px] text-gray-300 leading-relaxed">
            NOVA diseñará un ejercicio de programación nuevo adaptado al temario oficial de este tema y complementario a los ejercicios que ya tienes.
          </p>

          <div class="grid grid-cols-1 sm:grid-cols-2 gap-3 pt-1">
            <div>
              <label class="block text-[11px] font-semibold text-emerald-300 mb-1">Dificultad del Reto:</label>
              <select x-model="generadorIaDificultad" class="w-full bg-gray-900 border border-gray-700 rounded-xl px-3 py-1.5 text-xs text-white focus:border-emerald-500 focus:outline-none">
                <option value="facil">Básica / Conceptos Clave</option>
                <option value="medio">Intermedia / Desarrollo estándar</option>
                <option value="examen" selected>Nivel Examen UNIR (Recomendado)</option>
                <option value="avanzado">Avanzada / Caso Real de Empresa</option>
              </select>
            </div>

            <div>
              <label class="block text-[11px] font-semibold text-emerald-300 mb-1">Enfoque específico (opcional):</label>
              <input type="text" x-model="generadorIaEnfoque" placeholder="Ej: Control de excepciones, Streams, Serializable..."
                     class="w-full bg-gray-900 border border-gray-700 rounded-xl px-3 py-1.5 text-xs text-white placeholder-gray-500 focus:border-emerald-500 focus:outline-none">
            </div>
          </div>

          <div x-show="generadorIaError" class="p-2.5 rounded-xl bg-rose-500/15 border border-rose-500/30 text-[11px] text-rose-300" x-text="generadorIaError"></div>

          <div class="flex items-center justify-end gap-2 pt-1">
            <button @click="generadorIaAbierto = false" :disabled="generandoRetoIa"
                    class="px-3 py-1.5 rounded-xl bg-gray-800 hover:bg-gray-700 text-gray-300 text-xs font-medium transition">
              Cancelar
            </button>
            <button @click="generarNuevoRetoIa()" :disabled="generandoRetoIa"
                    class="px-4 py-1.5 rounded-xl bg-gradient-to-r from-emerald-500 to-teal-500 hover:from-emerald-400 hover:to-teal-400 text-gray-950 font-bold text-xs transition flex items-center gap-2 shadow-md shadow-emerald-500/20 disabled:opacity-50 disabled:cursor-not-allowed">
              <i class="fa-solid" :class="generandoRetoIa ? 'fa-spinner fa-spin' : 'fa-play'"></i>
              <span x-text="generandoRetoIa ? 'Diseñando reto con IA...' : 'Diseñar Reto de Código'"></span>
            </button>
          </div>
        </div>

        <!-- CONTENIDO ENTRENAMIENTOS -->
        <div x-show="!entrenamientosCargando && entrenamientosDatos?.entrenamientos?.length" class="space-y-4">
          <!-- BARRA DE SELECCIÓN COMPACTA Y ULTRA-ESTILIZADA (1 SOLA LÍNEA) -->
          <div class="flex items-center justify-between gap-3 p-1.5 bg-gray-950/85 rounded-2xl border border-gray-800 shadow-inner">
            <div class="flex items-center gap-1.5 overflow-x-auto no-scrollbar py-0.5 min-w-0">
              <!-- SEGMENTO OFICIALES -->
              <div class="flex items-center bg-gray-900/90 p-1 rounded-xl border border-gray-800 gap-1 shrink-0">
                <span class="text-[10px] font-bold text-gray-500 uppercase px-1.5 hidden sm:inline tracking-wider">Oficiales:</span>
                <template x-for="(reto, idx) in (entrenamientosDatos?.entrenamientos || [])" :key="reto.id || idx">
                  <template x-if="!reto.es_ia">
                    <button @click="entrenamientoActivoIndice = idx; generadorIaAbierto = false"
                            :class="entrenamientoActivoIndice === idx 
                                    ? 'bg-purple-600 text-white font-black shadow-md shadow-purple-500/25 border-purple-500' 
                                    : 'bg-gray-800/80 text-gray-400 hover:text-white hover:bg-gray-700/80 border-transparent'"
                            class="h-7 min-w-[28px] px-2 rounded-lg text-xs font-bold transition flex items-center justify-center gap-1.5 border"
                            :title="'Reto ' + (reto.numero || (idx + 1)) + ': ' + reto.titulo">
                      <span x-text="reto.numero || (idx + 1)"></span>
                      <span x-show="entrenamientoActivoIndice === idx" 
                            class="text-[11px] font-medium hidden md:inline truncate max-w-[180px]" 
                            x-text="reto.titulo"></span>
                    </button>
                  </template>
                </template>
              </div>

              <!-- SEGMENTO IA (SI EXISTEN) -->
              <template x-if="(entrenamientosDatos?.entrenamientos || []).some(e => e.es_ia)">
                <div class="flex items-center bg-emerald-950/30 p-1 rounded-xl border border-emerald-500/30 gap-1 shrink-0">
                  <span class="text-[10px] font-bold text-emerald-400 uppercase px-1.5 hidden sm:inline tracking-wider">IA:</span>
                  <template x-for="(reto, idx) in (entrenamientosDatos?.entrenamientos || [])" :key="reto.id || idx">
                    <template x-if="reto.es_ia">
                      <button @click="entrenamientoActivoIndice = idx; generadorIaAbierto = false"
                              :class="entrenamientoActivoIndice === idx 
                                      ? 'bg-emerald-500 text-gray-950 font-black shadow-md shadow-emerald-500/25 border-emerald-400' 
                                      : 'bg-emerald-500/15 text-emerald-300 hover:bg-emerald-500/25 border-emerald-500/30'"
                              class="h-7 min-w-[32px] px-2 rounded-lg text-xs font-bold transition flex items-center justify-center gap-1 border"
                              :title="'Reto IA: ' + reto.titulo">
                        <i class="fa-solid fa-wand-magic-sparkles text-[9px]" :class="entrenamientoActivoIndice === idx ? 'text-gray-950' : 'text-emerald-400'"></i>
                        <span x-text="idx - (entrenamientosDatos?.total_oficiales || 5) + 1"></span>
                        <span x-show="entrenamientoActivoIndice === idx" 
                              class="text-[11px] font-medium hidden md:inline truncate max-w-[160px]" 
                              x-text="reto.titulo"></span>
                      </button>
                    </template>
                  </template>
                </div>
              </template>
            </div>

            <!-- BOTÓN ACCIÓN: + RETO CON IA -->
            <button @click="generadorIaAbierto = !generadorIaAbierto"
                    :class="generadorIaAbierto ? 'bg-emerald-500 text-gray-950 font-black' : 'bg-emerald-500/15 hover:bg-emerald-500/25 text-emerald-300 border border-emerald-500/40'"
                    class="h-7 px-3 rounded-xl text-xs font-bold border transition flex items-center gap-1.5 shrink-0 shadow-sm"
                    title="Diseñar un nuevo reto de código con IA para este tema">
              <i class="fa-solid fa-wand-magic-sparkles text-xs" :class="generadorIaAbierto ? 'text-gray-950' : 'text-emerald-400'"></i>
              <span x-text="generadorIaAbierto ? 'Cerrar' : '+ Reto con IA'"></span>
            </button>
          </div>

          <!-- DETALLE DEL RETO SELECCIONADO -->
          <template x-if="entrenamientosDatos?.entrenamientos?.[entrenamientoActivoIndice]">
            <div class="space-y-4">
              <!-- TARJETA DEL PLANTEAMIENTO / ENUNCIADO -->
              <div class="glass-card p-5 rounded-2xl border border-gray-800/90 space-y-3 bg-surface-2/60">
                <div class="flex flex-wrap items-center justify-between gap-2 border-b border-gray-800/80 pb-3">
                  <div class="flex items-center gap-2.5 min-w-0">
                    <template x-if="!entrenamientosDatos.entrenamientos[entrenamientoActivoIndice].es_ia">
                      <span class="w-7 h-7 rounded-lg bg-purple-500/30 text-purple-300 font-black text-xs flex items-center justify-center shrink-0"
                            x-text="'E' + (entrenamientosDatos.entrenamientos[entrenamientoActivoIndice].numero || (entrenamientoActivoIndice + 1))"></span>
                    </template>
                    <template x-if="entrenamientosDatos.entrenamientos[entrenamientoActivoIndice].es_ia">
                      <span class="w-7 h-7 rounded-lg bg-emerald-500/30 text-emerald-300 font-black text-xs flex items-center justify-center shrink-0">
                        <i class="fa-solid fa-wand-magic-sparkles text-xs"></i>
                      </span>
                    </template>
                    <div class="min-w-0">
                      <div class="flex items-center gap-2">
                        <span class="text-[10px] font-bold px-2 py-0.5 rounded-full"
                              :class="entrenamientosDatos.entrenamientos[entrenamientoActivoIndice].es_ia ? 'bg-emerald-500/15 text-emerald-300 border border-emerald-500/30' : 'bg-purple-500/15 text-purple-300 border border-purple-500/30'"
                              x-text="entrenamientosDatos.entrenamientos[entrenamientoActivoIndice].es_ia ? 'Reto Inédito IA' : 'Oficial UNIR'"></span>
                        <span class="text-[10px] font-semibold text-gray-400 font-mono" x-text="entrenamientosDatos.entrenamientos[entrenamientoActivoIndice].dificultad || 'Examen'"></span>
                      </div>
                      <h4 class="font-bold text-white text-sm sm:text-base mt-0.5 leading-snug" x-text="entrenamientosDatos.entrenamientos[entrenamientoActivoIndice].titulo"></h4>
                    </div>
                  </div>

                  <div class="flex items-center gap-2 shrink-0">
                    <button @click="copiarTextoReto(entrenamientosDatos.entrenamientos[entrenamientoActivoIndice].enunciado)"
                            class="px-2.5 py-1.5 rounded-xl bg-gray-800 hover:bg-gray-700 text-gray-300 hover:text-white text-xs font-semibold transition flex items-center gap-1.5 border border-gray-700">
                      <i class="fa-regular fa-copy text-xs"></i>
                      <span>Copiar Enunciado</span>
                    </button>
                    <button @click="pedirPistaRetoNova(entrenamientosDatos.entrenamientos[entrenamientoActivoIndice])"
                            class="px-2.5 py-1.5 rounded-xl bg-cyan-500/15 hover:bg-cyan-500/25 border border-cyan-500/35 text-cyan-300 text-xs font-bold transition flex items-center gap-1.5 shadow-sm"
                            title="Preguntar a NOVA en el chat para que te dé una pista pedagógica">
                      <i class="fa-solid fa-robot text-xs text-cyan-400"></i>
                      <span>Pedir Pista a NOVA</span>
                    </button>
                    <template x-if="entrenamientosDatos.entrenamientos[entrenamientoActivoIndice].es_ia">
                      <button @click="eliminarRetoIaActual()"
                              class="px-2.5 py-1.5 rounded-xl bg-rose-500/15 hover:bg-rose-500/25 border border-rose-500/30 text-rose-300 text-xs font-medium transition flex items-center gap-1"
                              title="Eliminar este reto generado por IA">
                        <i class="fa-regular fa-trash-can text-xs"></i>
                      </button>
                    </template>
                  </div>
                </div>

                <!-- ENUNCIADO -->
                <div>
                  <p class="text-[11px] font-bold uppercase tracking-wider text-purple-400 mb-1.5">🎯 Planteamiento del Reto</p>
                  <p class="text-xs text-gray-200 whitespace-pre-line leading-relaxed font-sans" x-text="entrenamientosDatos.entrenamientos[entrenamientoActivoIndice].enunciado"></p>
                </div>

                <!-- PISTA CONCEPTUAL SI VIENE EN EL RETO IA -->
                <template x-if="entrenamientosDatos.entrenamientos[entrenamientoActivoIndice].pista">
                  <div class="p-3.5 rounded-xl bg-amber-500/10 border border-amber-500/25 space-y-1">
                    <p class="text-[10px] font-bold uppercase tracking-wider text-amber-300 flex items-center gap-1">
                      <i class="fa-solid fa-lightbulb text-amber-400"></i>
                      <span>Pista conceptual (sin spoilers)</span>
                    </p>
                    <p class="text-[11px] text-amber-200/90 leading-relaxed" x-text="entrenamientosDatos.entrenamientos[entrenamientoActivoIndice].pista"></p>
                  </div>
                </template>
              </div>

              <!-- DESPLEGABLE SOLUCIÓN Y CÓDIGO PASO A PASO -->
              <details class="glass-card rounded-2xl border border-gray-800/90 overflow-hidden group/sol bg-surface-2/40 transition">
                <summary class="p-4 cursor-pointer flex items-center justify-between text-xs font-bold text-cyan-300 hover:text-cyan-200 select-none bg-cyan-950/20 hover:bg-cyan-950/30 transition">
                  <div class="flex items-center gap-2">
                    <i class="fa-solid fa-lightbulb text-amber-400 text-sm"></i>
                    <span>💡 Ver Solución y Código Paso a Paso (¡Pica el código antes de abrirla!)</span>
                  </div>
                  <i class="fa-solid fa-chevron-down text-xs transition-transform group-open/sol:rotate-180"></i>
                </summary>

                <div class="p-5 border-t border-gray-800 space-y-3">
                  <div class="flex items-center justify-between gap-2">
                    <span class="text-[11px] text-gray-400 font-semibold"
                          x-text="entrenamientosDatos.entrenamientos[entrenamientoActivoIndice].es_ia ? 'Solución diseñada por IA para este reto:' : 'Solución oficial extraída del temario UNIR:'"></span>
                    <button @click="copiarTextoReto(entrenamientosDatos.entrenamientos[entrenamientoActivoIndice].solucion)"
                            class="px-2.5 py-1 rounded-lg bg-gray-800 hover:bg-gray-700 text-gray-300 text-[11px] font-medium transition flex items-center gap-1">
                      <i class="fa-regular fa-copy text-[10px]"></i>
                      <span>Copiar Solución</span>
                    </button>
                  </div>

                  <pre class="p-4 rounded-xl bg-gray-950/90 border border-gray-800/90 text-gray-200 font-mono text-[11px] overflow-x-auto whitespace-pre-wrap leading-relaxed selection:bg-purple-500/30" x-text="entrenamientosDatos.entrenamientos[entrenamientoActivoIndice].solucion"></pre>
                </div>
              </details>

              <!-- BARRA DE NAVEGACIÓN ENTRE RETOS (ANTERIOR / SIGUIENTE) -->
              <div class="flex items-center justify-between pt-1">
                <button @click="entrenamientoActivoIndice > 0 ? (entrenamientoActivoIndice--) : null"
                        :disabled="entrenamientoActivoIndice === 0"
                        class="px-3.5 py-2 rounded-xl bg-gray-900 border border-gray-800 text-xs font-bold text-gray-300 hover:text-white hover:border-gray-700 disabled:opacity-30 disabled:cursor-not-allowed transition flex items-center gap-1.5">
                  <i class="fa-solid fa-chevron-left text-[10px]"></i>
                  <span>Reto Anterior</span>
                </button>

                <div class="text-xs text-gray-400 font-medium">
                  Reto <span class="font-bold text-white" x-text="entrenamientoActivoIndice + 1"></span> de <span class="font-bold text-white" x-text="entrenamientosDatos.entrenamientos.length"></span>
                </div>

                <button @click="entrenamientoActivoIndice < (entrenamientosDatos.entrenamientos.length - 1) ? (entrenamientoActivoIndice++) : null"
                        :disabled="entrenamientoActivoIndice >= (entrenamientosDatos.entrenamientos.length - 1)"
                        class="px-3.5 py-2 rounded-xl bg-gray-900 border border-gray-800 text-xs font-bold text-gray-300 hover:text-white hover:border-gray-700 disabled:opacity-30 disabled:cursor-not-allowed transition flex items-center gap-1.5">
                  <span>Siguiente Reto</span>
                  <i class="fa-solid fa-chevron-right text-[10px]"></i>
                </button>
              </div>
            </div>
          </template>
        </div>
      </div>

      <!-- FOOTER -->
      <div class="p-3.5 bg-gray-900 border-t border-gray-800 flex items-center justify-between text-xs text-gray-400">
        <span x-show="copiadoRetoToast" class="text-emerald-400 font-bold flex items-center gap-1.5 transition">
          <i class="fa-solid fa-check"></i>
          <span>¡Copiado al portapapeles!</span>
        </span>
        <span x-show="!copiadoRetoToast" class="text-gray-500 text-[11px]">
          💡 Consejo de examen: Intenta resolver el reto a ciegas en tu IDE antes de desplegar la solución.
        </span>
        <button @click="modalEntrenamientos = false" class="px-4 py-2 bg-gray-800 hover:bg-gray-700 text-white rounded-xl text-xs font-semibold transition ml-auto">
          Cerrar
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
              <span class="text-xs text-amber-400 font-mono" x-text="formatearFechaDDMMAAAA(minuteroClase?.fecha)"></span>
            </div>
            <h3 class="font-bold text-white text-sm mt-0.5" x-text="minuteroClase?.titulo"></h3>
          </div>
        </div>

        <div class="flex items-center space-x-2">
          <button @click="modalMinutero = false" class="text-gray-400 hover:text-white p-2 rounded-lg transition hover:bg-gray-800">
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
                <span class="font-mono font-bold text-amber-400 bg-amber-500/10 px-2.5 py-0.5 rounded-lg border border-amber-500/25 text-[11px] flex items-center space-x-1 shrink-0"
                      title="Minuto de la intervención">
                  <i class="fa-regular fa-clock text-[9px]"></i>
                  <span x-text="cue.start_str"></span>
                </span>
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
              <span class="text-xs text-amber-400 font-mono" x-text="formatearFechaDDMMAAAA(resumenClaseActual?.fecha)"></span>
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
          <button @click="modalResumenClase = false" class="text-gray-400 hover:text-white p-2 rounded-lg transition hover:bg-gray-800">
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
          <button @click="cerrarModalPdf()" class="text-gray-400 hover:text-white p-1.5 rounded-lg" title="Cerrar visor">
            <i class="fa-solid fa-xmark text-lg"></i>
          </button>
        </div>
      </div>
      <!-- IFRAME VISOR NATIVO CON TEMA OSCURO -->
      <div class="flex-1 bg-gray-950 w-full h-full">
        <template x-if="modalVisorPdf && modalRutaPdf">
          <iframe :src="'/api/archivo-raw?ruta=' + encodeURIComponent(modalRutaPdf)" class="w-full h-full border-0"></iframe>
        </template>
      </div>
    </div>
  </div>

  <!-- ==================== MODAL CONFIGURACIÓN BYOK & PROVEEDORES IA (3-EN-1) ==================== -->
  <div x-show="modalApiKey"
       x-transition:enter="transition ease-out duration-200"
       x-transition:enter-start="opacity-0"
       x-transition:enter-end="opacity-100"
       x-transition:leave="transition ease-in duration-150"
       x-transition:leave-start="opacity-100"
       x-transition:leave-end="opacity-0"
       class="fixed inset-0 bg-black/80 backdrop-blur-sm z-50 flex items-center justify-center p-4"
       style="display: none;">
    <div @click.away="modalApiKey = false" class="bg-gray-900 border border-gray-800 rounded-3xl max-w-xl w-full p-5 sm:p-6 shadow-2xl relative flex flex-col max-h-[90vh]">
      <!-- CABECERA -->
      <div class="flex items-center justify-between pb-3.5 border-b border-gray-800 shrink-0">
        <div class="flex items-center space-x-3">
          <div class="h-10 w-10 rounded-2xl bg-gradient-to-tr from-cyan-500 via-indigo-500 to-purple-600 flex items-center justify-center text-white shadow-lg shadow-cyan-500/20">
            <i class="fa-solid fa-key text-base"></i>
          </div>
          <div>
            <h3 class="font-bold text-gray-100 text-base">Claves de IA y Modelos Gratuitos</h3>
            <p class="text-xs text-gray-400">Gemini, OpenRouter y Groq Cloud (Triple Persistencia)</p>
          </div>
        </div>
        <button @click="modalApiKey = false" class="text-gray-400 hover:text-white p-1.5 rounded-xl hover:bg-gray-800 transition">
          <i class="fa-solid fa-xmark text-lg"></i>
        </button>
      </div>

      <!-- CUERPO CON SCROLL -->
      <div class="mt-4 space-y-4 overflow-y-auto pr-1">
        <!-- BANNER DE TRIPLE PERSISTENCIA -->
        <div class="p-3 bg-cyan-950/40 border border-cyan-500/20 rounded-2xl text-[11px] text-gray-300 space-y-1">
          <div class="flex items-center text-cyan-400 font-semibold gap-1.5">
            <i class="fa-solid fa-shield-halved"></i>
            <span>Triple Redundancia Permanente</span>
          </div>
          <p class="text-gray-400 leading-relaxed">
            Tus claves se guardan en tu navegador (<code class="text-cyan-300">localStorage</code>), en tu cuenta de usuario y en el servidor local (<code class="text-cyan-300">.env</code>). 
            Permanecerán guardadas ante cualquier actualización de código, reinicio o cambio de ventana.
          </p>
        </div>

        <!-- 1. GOOGLE GEMINI -->
        <div class="p-3.5 bg-gray-950/80 border border-gray-800/90 rounded-2xl space-y-2">
          <div class="flex items-center justify-between">
            <div class="flex items-center space-x-2">
              <span class="w-6 h-6 rounded-lg bg-cyan-500/20 text-cyan-400 flex items-center justify-center text-xs font-bold">1</span>
              <span class="text-xs font-bold text-gray-100">Google Gemini (AI Studio)</span>
              <span class="text-[10px] text-cyan-400 bg-cyan-500/10 px-1.5 py-0.5 rounded font-mono">Oficial</span>
            </div>
            <span class="text-[10px] px-2 py-0.5 rounded-full font-medium"
                  :class="inputApiKeyTemp.trim() ? 'bg-emerald-500/20 text-emerald-300 border border-emerald-500/30' : 'bg-gray-800 text-gray-400'"
                  x-text="inputApiKeyTemp.trim() ? '✓ Configurada' : 'Sin configurar'"></span>
          </div>
          <p class="text-[11px] text-gray-400">
            Permite usar Gemini 3.5 Flash, Gemini Flash Lite y modelos oficiales de Google con cuota gratuita.
          </p>
          <div class="relative">
            <input :type="mostrarKeyGemini ? 'text' : 'password'"
                   x-model="inputApiKeyTemp"
                   placeholder="AIzaSy..."
                   class="w-full bg-gray-900 border border-gray-800 rounded-xl pl-3.5 pr-10 py-2 text-xs text-cyan-300 outline-none focus:border-cyan-500 font-mono transition">
            <button type="button" 
                    @click="mostrarKeyGemini = !mostrarKeyGemini"
                    class="absolute right-3 top-2 text-gray-500 hover:text-gray-300 text-xs">
              <i class="fa-solid" :class="mostrarKeyGemini ? 'fa-eye-slash' : 'fa-eye'"></i>
            </button>
          </div>
          <div class="flex items-center justify-between text-[11px] pt-0.5">
            <a href="https://aistudio.google.com/app/apikey" target="_blank" rel="noopener" class="text-cyan-400 hover:underline flex items-center gap-1">
              <span>Obtener clave gratis en Google AI Studio</span>
              <i class="fa-solid fa-arrow-up-right-from-square text-[9px]"></i>
            </a>
            <span class="text-gray-500">Tier Free Oficial</span>
          </div>
        </div>

        <!-- 2. OPENROUTER -->
        <div class="p-3.5 bg-gray-950/80 border border-gray-800/90 rounded-2xl space-y-2">
          <div class="flex items-center justify-between">
            <div class="flex items-center space-x-2">
              <span class="w-6 h-6 rounded-lg bg-purple-500/20 text-purple-400 flex items-center justify-center text-xs font-bold">2</span>
              <span class="text-xs font-bold text-gray-100">OpenRouter (Modelos de la Comunidad)</span>
              <span class="text-[10px] text-purple-400 bg-purple-500/10 px-1.5 py-0.5 rounded font-mono">Gratis</span>
            </div>
            <span class="text-[10px] px-2 py-0.5 rounded-full font-medium"
                  :class="inputOpenrouterTemp.trim() ? 'bg-emerald-500/20 text-emerald-300 border border-emerald-500/30' : 'bg-gray-800 text-gray-400'"
                  x-text="inputOpenrouterTemp.trim() ? '✓ Configurada' : 'Opcional'"></span>
          </div>
          <p class="text-[11px] text-gray-400">
            Habilita DeepSeek V3 Free, Qwen 3 235B Free, Llama 3.3 70B Free y Mistral Small sin coste.
          </p>
          <div class="relative">
            <input :type="mostrarKeyOpenrouter ? 'text' : 'password'"
                   x-model="inputOpenrouterTemp"
                   placeholder="sk-or-v1-..."
                   class="w-full bg-gray-900 border border-gray-800 rounded-xl pl-3.5 pr-10 py-2 text-xs text-purple-300 outline-none focus:border-purple-500 font-mono transition">
            <button type="button" 
                    @click="mostrarKeyOpenrouter = !mostrarKeyOpenrouter"
                    class="absolute right-3 top-2 text-gray-500 hover:text-gray-300 text-xs">
              <i class="fa-solid" :class="mostrarKeyOpenrouter ? 'fa-eye-slash' : 'fa-eye'"></i>
            </button>
          </div>
          <div class="flex items-center justify-between text-[11px] pt-0.5">
            <a href="https://openrouter.ai/keys" target="_blank" rel="noopener" class="text-purple-400 hover:underline flex items-center gap-1">
              <span>Obtener clave gratis en OpenRouter</span>
              <i class="fa-solid fa-arrow-up-right-from-square text-[9px]"></i>
            </a>
            <span class="text-gray-500">Comunidad Free</span>
          </div>
        </div>

        <!-- 3. GROQ CLOUD -->
        <div class="p-3.5 bg-gray-950/80 border border-gray-800/90 rounded-2xl space-y-2">
          <div class="flex items-center justify-between">
            <div class="flex items-center space-x-2">
              <span class="w-6 h-6 rounded-lg bg-amber-500/20 text-amber-400 flex items-center justify-center text-xs font-bold">3</span>
              <span class="text-xs font-bold text-gray-100">Groq Cloud (Velocidad LPU Instantánea)</span>
              <span class="text-[10px] text-amber-400 bg-amber-500/10 px-1.5 py-0.5 rounded font-mono">Ultra-rápido</span>
            </div>
            <span class="text-[10px] px-2 py-0.5 rounded-full font-medium"
                  :class="inputGroqTemp.trim() ? 'bg-emerald-500/20 text-emerald-300 border border-emerald-500/30' : 'bg-gray-800 text-gray-400'"
                  x-text="inputGroqTemp.trim() ? '✓ Configurada' : 'Opcional'"></span>
          </div>
          <p class="text-[11px] text-gray-400">
            Habilita Llama 3.3 70B y DeepSeek R1 Distill 70B a una velocidad extrema de ~300 tokens/segundo.
          </p>
          <div class="relative">
            <input :type="mostrarKeyGroq ? 'text' : 'password'"
                   x-model="inputGroqTemp"
                   placeholder="gsk_..."
                   class="w-full bg-gray-900 border border-gray-800 rounded-xl pl-3.5 pr-10 py-2 text-xs text-amber-300 outline-none focus:border-amber-500 font-mono transition">
            <button type="button" 
                    @click="mostrarKeyGroq = !mostrarKeyGroq"
                    class="absolute right-3 top-2 text-gray-500 hover:text-gray-300 text-xs">
              <i class="fa-solid" :class="mostrarKeyGroq ? 'fa-eye-slash' : 'fa-eye'"></i>
            </button>
          </div>
          <div class="flex items-center justify-between text-[11px] pt-0.5">
            <a href="https://console.groq.com/keys" target="_blank" rel="noopener" class="text-amber-400 hover:underline flex items-center gap-1">
              <span>Obtener clave gratis en Groq Console</span>
              <i class="fa-solid fa-arrow-up-right-from-square text-[9px]"></i>
            </a>
            <span class="text-gray-500">Tier Free Oficial</span>
          </div>
        </div>

        <!-- MENSAJES DE ESTADO -->
        <template x-if="apiKeyMensajeError">
          <div class="p-3 bg-rose-500/10 border border-rose-500/30 rounded-xl text-rose-300 text-xs flex items-center gap-2">
            <i class="fa-solid fa-circle-exclamation shrink-0"></i>
            <span x-text="apiKeyMensajeError"></span>
          </div>
        </template>

        <template x-if="apiKeyMensajeExito">
          <div class="p-3 bg-emerald-500/10 border border-emerald-500/30 rounded-xl text-emerald-300 text-xs flex items-center gap-2">
            <i class="fa-solid fa-circle-check shrink-0"></i>
            <span x-text="apiKeyMensajeExito"></span>
          </div>
        </template>
      </div>

      <!-- BOTONES DE ACCIÓN -->
      <div class="mt-4 pt-4 border-t border-gray-800 flex items-center justify-between gap-3 shrink-0">
        <button x-show="userGeminiKey || userOpenrouterKey || userGroqKey" 
                @click="eliminarApiKeyUsuario()" 
                type="button"
                class="px-3 py-2 rounded-xl bg-gray-800 hover:bg-rose-500/20 text-gray-400 hover:text-rose-300 text-xs transition border border-gray-700"
                title="Limpiar todas las claves guardadas">
          <i class="fa-solid fa-trash-can mr-1 text-[10px]"></i>
          Limpiar claves
        </button>
        <div class="flex items-center gap-2 ml-auto">
          <button @click="modalApiKey = false" type="button" class="px-4 py-2 rounded-xl bg-gray-800 text-gray-300 hover:text-white text-xs transition">
            Cancelar
          </button>
          <button @click="guardarApiKeyUsuario()" 
                  :disabled="apiKeyValidando"
                  type="button" 
                  class="px-5 py-2 rounded-xl bg-gradient-to-r from-cyan-500 via-indigo-600 to-purple-600 hover:from-cyan-400 hover:to-indigo-500 text-white font-semibold text-xs shadow-lg shadow-cyan-500/20 transition flex items-center gap-2">
            <i x-show="apiKeyValidando" class="fa-solid fa-spinner fa-spin text-xs"></i>
            <span x-text="apiKeyValidando ? 'Guardando...' : 'Guardar y Activar Claves'"></span>
          </button>
        </div>
      </div>
    </div>
  </div>

  <!-- ==================== MODAL CONECTAR MI CAMPUS UNIR (CANVAS LMS PERSONAL) ==================== -->
  <div x-show="modalMiCampus"
       x-transition:enter="transition ease-out duration-200"
       x-transition:enter-start="opacity-0"
       x-transition:enter-end="opacity-100"
       x-transition:leave="transition ease-in duration-150"
       x-transition:leave-start="opacity-100"
       x-transition:leave-end="opacity-0"
       class="fixed inset-0 bg-black/80 backdrop-blur-sm z-50 flex items-center justify-center p-4"
       style="display: none;">
    <div @click.away="modalMiCampus = false" class="bg-gray-900 border border-gray-800 rounded-3xl max-w-xl w-full p-6 shadow-2xl relative max-h-[92vh] overflow-y-auto">
      <div class="flex items-center justify-between pb-4 border-b border-gray-800">
        <div class="flex items-center space-x-3">
          <div class="h-10 w-10 rounded-2xl bg-gradient-to-tr from-indigo-500 to-cyan-500 flex items-center justify-center text-white shadow-lg shadow-indigo-500/20">
            <i class="fa-solid fa-building-columns text-base"></i>
          </div>
          <div>
            <h3 class="font-bold text-gray-100 text-base">Conectar Mi Campus UNIR (Canvas)</h3>
            <p class="text-xs text-gray-400">Sincroniza tus propias entregas, PACs y calificaciones sin contraseñas</p>
          </div>
        </div>
        <button @click="modalMiCampus = false" class="text-gray-400 hover:text-white p-1.5 rounded-xl hover:bg-gray-800 transition">
          <i class="fa-solid fa-xmark text-lg"></i>
        </button>
      </div>

      <div class="mt-4 space-y-4">
        <!-- ESTADO ACTUAL DE LA CONEXIÓN -->
        <template x-if="miCampusConfig?.conectado">
          <div class="p-3.5 bg-indigo-500/10 border border-indigo-500/30 rounded-2xl space-y-2">
            <div class="flex items-center justify-between gap-3">
              <div class="text-xs">
                <p class="font-bold text-indigo-300 flex items-center gap-1.5">
                  <i class="fa-solid fa-circle-check text-emerald-400"></i>
                  <span x-text="'Conectado como: ' + (miCampusConfig.alumno_nombre || usuarioActual?.username)"></span>
                </p>
                <p class="text-[11px] text-gray-400 mt-0.5" x-text="'Última sincronización: ' + (miCampusConfig.ultima_sincronizacion || 'Ahora') + ' • ' + (miCampusCursos.length || 0) + ' cursos'"></p>
              </div>
              <button @click="desconectarMiCampus()" class="px-3 py-1.5 rounded-xl bg-rose-500/15 hover:bg-rose-500/25 text-rose-300 text-xs font-semibold transition border border-rose-500/30 shrink-0">
                Desconectar
              </button>
            </div>

            <!-- AVISO DE CADUCIDAD DEL TOKEN (SI APLICA) -->
            <template x-if="miCampusConfig?.token_estado?.estado === 'por_caducar'">
              <div class="p-2.5 rounded-xl bg-amber-500/15 border border-amber-500/30 text-amber-300 text-xs flex items-start gap-2">
                <i class="fa-solid fa-triangle-exclamation mt-0.5 shrink-0 text-amber-400"></i>
                <div class="flex-1">
                  <p class="font-bold">Tu Token de Canvas caduca pronto (<span x-text="miCampusConfig.token_estado.dias_restantes"></span> días restantes)</p>
                  <p class="text-[11px] text-amber-200/80 mt-0.5">Genera uno nuevo en tu perfil de Canvas y pégalo abajo para no perder la descarga de PDFs nuevos ni las notas actualizadas.</p>
                </div>
              </div>
            </template>
            <template x-if="miCampusConfig?.token_estado?.estado === 'caducado'">
              <div class="p-2.5 rounded-xl bg-rose-500/15 border border-rose-500/30 text-rose-300 text-xs flex items-start gap-2">
                <i class="fa-solid fa-clock-rotate-left mt-0.5 shrink-0 text-rose-400"></i>
                <div class="flex-1">
                  <p class="font-bold">Tu Token de Canvas ha caducado</p>
                  <p class="text-[11px] text-rose-200/80 mt-0.5">Tus PDFs ya descargados se mantienen intactos. Para volver a descargar PDFs nuevos o actualizar notas, genera un nuevo token en Canvas y pégalo abajo.</p>
                </div>
              </div>
            </template>
            <template x-if="miCampusConfig?.token_estado?.estado === 'ok'">
              <div class="text-[11px] text-emerald-400/90 flex items-center gap-1.5">
                <i class="fa-solid fa-shield-halved text-[10px]"></i>
                <span x-text="'Token activo (vigente hasta: ' + (formatearFechaDDMMAAAA(miCampusConfig.token_estado.expira) || '30 días') + ')'"></span>
              </div>
            </template>
          </div>
        </template>

        <!-- TOKEN PERSONAL DE ACCESO DE CANVAS UNIR -->
        <div class="p-4 bg-gray-950/90 border border-gray-800 rounded-2xl text-xs text-gray-300 space-y-3">
          <div class="flex items-center justify-between">
            <p class="font-bold text-cyan-400 flex items-center gap-2">
              <i class="fa-solid fa-key text-cyan-400"></i>
              <span>Token de Acceso de Canvas UNIR</span>
            </p>
            <span class="text-[10px] px-2 py-0.5 rounded-full bg-indigo-500/15 text-indigo-300 border border-indigo-500/30 font-bold">Vigencia 30 días</span>
          </div>
          <p class="text-[11px] text-gray-400 leading-relaxed">
            Conecta tu Canvas personal para sincronizar tus <strong>asignaturas</strong>, <strong>entregas/PACs</strong>, <strong>calificaciones oficiales</strong> y permitir la descarga de <strong>PDFs de temarios</strong>.
          </p>
          <div class="bg-gray-900/80 p-3 rounded-xl border border-gray-800/80 text-[11px] text-gray-300 space-y-1">
            <div class="font-semibold text-gray-200 flex items-center gap-1.5"><i class="fa-solid fa-circle-info text-cyan-400 text-[10px]"></i> ¿Cómo obtener tu token en 1 minuto?</div>
            <ol class="list-decimal list-inside space-y-0.5 text-gray-400 pl-1">
              <li>Entra en tu <a href="https://campusfp.unir.net/profile/settings" target="_blank" class="text-cyan-400 hover:underline font-bold">Perfil UNIR → Integraciones aprobadas</a>.</li>
              <li>Pulsa el botón <strong>«+ Nuevo token de acceso»</strong>.</li>
              <li>En "Propósito" escribe <em>Study Hub</em> y genera el token.</li>
              <li>Copia el token generado y pégalo aquí abajo:</li>
            </ol>
          </div>

          <div>
            <label class="block text-[11px] font-bold text-gray-300 mb-1">Pega aquí tu Token de Canvas:</label>
            <input type="password"
                   x-model="inputCanvasToken"
                   placeholder="Ej: 11842~aBcDeFgHiJkLmNoPqRsTuVwXyZ..."
                   class="w-full bg-gray-900 border border-gray-800 rounded-xl px-3.5 py-2.5 text-xs text-indigo-300 outline-none focus:border-cyan-500 font-mono transition">
          </div>

          <div class="flex flex-wrap items-center justify-between gap-2 pt-1 border-t border-gray-800/80">
            <label class="text-[10px] text-gray-400 flex items-center gap-1">
              <i class="fa-regular fa-calendar text-gray-500"></i>
              <span>Fecha de vencimiento (la que pusiste en Canvas, o se calculará a 30 días):</span>
            </label>
            <input type="date"
                   x-model="inputCanvasTokenExpira"
                   class="bg-gray-900 border border-gray-800 rounded-lg px-2.5 py-1 text-[11px] text-gray-300 outline-none focus:border-cyan-500">
          </div>
        </div>

        <!-- SECCIÓN OAUTH2 INICIAR SESIÓN CON UNIR (PREPARADO) -->
        <template x-if="miCampusConfig?.oauth_disponible">
          <div class="p-3.5 bg-gradient-to-r from-indigo-950/40 to-cyan-950/40 border border-indigo-500/30 rounded-2xl text-xs flex items-center justify-between gap-3">
            <div>
              <p class="font-bold text-indigo-300 flex items-center gap-1.5">
                <i class="fa-solid fa-key text-cyan-400"></i>
                <span>Iniciar sesión oficial con UNIR (SSO)</span>
              </p>
              <p class="text-[11px] text-gray-400 mt-0.5">Inicio oficial sin tokens manuales (requiere Developer Key institucional).</p>
            </div>
            <a href="/api/canvas/oauth/iniciar" class="px-3 py-1.5 rounded-xl bg-indigo-500 hover:bg-indigo-400 text-white font-bold text-xs transition shadow-md shadow-indigo-500/20 shrink-0">
              Conectar UNIR
            </a>
          </div>
        </template>

        <div class="p-2.5 rounded-xl bg-sky-500/5 border border-sky-500/20 text-[11px] text-sky-300/90 flex items-center gap-2">
          <i class="fa-solid fa-moon text-sky-400"></i>
          <span><strong>Sincronización nocturna automática activa:</strong> El servidor revisa cada madrugada las entregas y PDFs nuevos sin que tengas que pulsar nada.</span>
        </div>

        <template x-if="miCampusMensajeError">
          <div class="p-3 bg-rose-500/10 border border-rose-500/30 rounded-xl text-rose-300 text-xs flex items-center gap-2">
            <i class="fa-solid fa-circle-exclamation shrink-0"></i>
            <span x-text="miCampusMensajeError"></span>
          </div>
        </template>

        <template x-if="miCampusMensajeExito">
          <div class="p-3 bg-emerald-500/10 border border-emerald-500/30 rounded-xl text-emerald-300 text-xs flex items-center gap-2">
            <i class="fa-solid fa-circle-check shrink-0"></i>
            <span x-text="miCampusMensajeExito"></span>
          </div>
        </template>
      </div>

      <div class="mt-6 pt-4 border-t border-gray-800 flex flex-wrap items-center justify-between gap-2">
        <div>
          <button x-show="miCampusConfig?.conectado && miCampusConfig?.modo === 'token'"
                  @click="sincronizarMaterialesMiCampus()"
                  :disabled="sincronizandoMiCampus"
                  type="button"
                  class="px-3.5 py-2 rounded-xl bg-emerald-500/15 hover:bg-emerald-500/25 border border-emerald-500/40 text-emerald-300 font-bold text-xs transition flex items-center gap-1.5">
            <i class="fa-solid fa-file-arrow-down"></i>
            <span>Descargar Mis PDFs del Campus</span>
          </button>
        </div>
        <div class="flex items-center gap-2 ml-auto">
          <button @click="modalMiCampus = false" type="button" class="px-4 py-2 rounded-xl bg-gray-800 text-gray-300 hover:text-white text-xs transition">
            Cerrar
          </button>
          <button @click="conectarMiCampus()"
                  :disabled="sincronizandoMiCampus"
                  type="button"
                  class="px-5 py-2 rounded-xl bg-gradient-to-r from-indigo-500 to-cyan-600 hover:from-indigo-400 hover:to-cyan-500 text-white font-bold text-xs shadow-lg shadow-indigo-500/20 transition flex items-center gap-2">
            <i x-show="sincronizandoMiCampus" class="fa-solid fa-spinner fa-spin text-xs"></i>
            <span x-text="sincronizandoMiCampus ? 'Sincronizando con UNIR...' : 'Conectar y Sincronizar'"></span>
          </button>
        </div>
      </div>
    </div>
  </div>

  <!-- ==================== MODAL RÁPIDO: ENTREGAS DEL MES ==================== -->
  <div x-show="modalEntregasMes"
       @keydown.escape.window="modalEntregasMes = false"
       x-transition:enter="transition ease-out duration-200"
       x-transition:enter-start="opacity-0"
       x-transition:enter-end="opacity-100"
       x-transition:leave="transition ease-in duration-150"
       x-transition:leave-start="opacity-100"
       x-transition:leave-end="opacity-0"
       class="fixed inset-0 bg-black/80 backdrop-blur-sm z-50 flex items-center justify-center p-4"
       style="display: none;">
    <div @click.away="modalEntregasMes = false" class="bg-gray-900 border border-rose-500/30 rounded-3xl max-w-xl w-full p-6 shadow-2xl relative max-h-[90vh] flex flex-col">
      <div class="flex items-center justify-between pb-4 border-b border-gray-800 shrink-0">
        <div class="flex items-center space-x-3">
          <div class="h-10 w-10 rounded-2xl bg-rose-500/20 text-rose-400 border border-rose-500/30 flex items-center justify-center text-lg shrink-0">
            <i class="fa-solid fa-calendar-check"></i>
          </div>
          <div>
            <div class="flex items-center gap-2">
              <h3 class="font-extrabold text-white text-base">Entregas de <span class="text-rose-400 capitalize" x-text="nombreMesCalendario"></span></h3>
              <span class="text-[10px] px-2 py-0.5 rounded-full bg-rose-500/20 text-rose-300 border border-rose-500/40 font-bold"
                    x-text="entregasMesActual.length + ' pendientes'"></span>
            </div>
            <p class="text-xs text-gray-400 mt-0.5">Haz clic en cualquier entrega para ver su detalle, cambiar estado o editarla</p>
          </div>
        </div>
        <button @click="modalEntregasMes = false" class="text-gray-400 hover:text-white p-1.5 rounded-xl hover:bg-gray-800 transition">
          <i class="fa-solid fa-xmark text-lg"></i>
        </button>
      </div>

      <!-- LISTA DE ENTREGAS DEL MES -->
      <div class="mt-4 space-y-2.5 overflow-y-auto flex-1 pr-1">
        <template x-for="(ev, idx) in entregasMesActual" :key="'modal-ent-' + (ev.id || 'ev') + '-' + idx">
          <div @click="modalEntregasMes = false; abrirModalItemAgenda(ev)"
               class="p-4 rounded-2xl border border-rose-500/30 bg-rose-950/20 hover:bg-rose-950/35 hover:border-rose-500/50 transition cursor-pointer flex items-center justify-between gap-3 group">
            <div class="min-w-0 flex-1">
              <div class="flex items-center gap-2 mb-1">
                <span class="text-[10px] font-bold px-2 py-0.2 rounded-md bg-rose-500 text-white uppercase tracking-wider" x-text="ev.categoria || 'Entrega'"></span>
                <span class="text-xs font-bold text-rose-300 font-mono" x-text="formatearFechaDDMMAAAA(ev.fecha_dia || ev.fecha_iso)"></span>
                <span x-show="ev.plazo_texto" class="text-[10px] px-1.5 py-0.2 rounded bg-black/40 text-rose-200 border border-rose-500/30 font-medium" x-text="formatearPlazoTexto(ev.plazo_texto, ev.fecha_dia || ev.fecha_iso)"></span>
              </div>
              <h4 class="text-sm font-extrabold text-white group-hover:text-rose-200 transition truncate" x-text="ev.titulo"></h4>
              <p class="text-xs text-gray-400 mt-0.5 flex items-center gap-1.5">
                <span x-text="iconoAsignatura(ev.asignatura)"></span>
                <span x-text="ev.asignatura"></span>
              </p>
            </div>
            <div class="flex items-center gap-2 shrink-0">
              <button @click.stop="marcarItemRapidoHecho(ev); if(entregasMesActual.length <= 1) modalEntregasMes = false;"
                      class="px-3 py-1.5 rounded-xl bg-emerald-500/20 hover:bg-emerald-500/30 text-emerald-300 border border-emerald-500/40 text-xs font-bold transition flex items-center gap-1.5"
                      title="Marcar como entregada / hecha">
                <i class="fa-solid fa-check"></i>
                <span class="hidden sm:inline">Completar</span>
              </button>
              <i class="fa-solid fa-chevron-right text-gray-500 group-hover:text-white transition text-xs"></i>
            </div>
          </div>
        </template>
      </div>

      <div class="mt-4 pt-3 border-t border-gray-800 flex items-center justify-between text-xs text-gray-400 shrink-0">
        <span>También puedes verlas resaltadas en el calendario cambiando el filtro a «Entregas».</span>
        <button @click="modalEntregasMes = false" class="px-4 py-2 rounded-xl bg-gray-800 hover:bg-gray-700 text-white font-medium text-xs transition">
          Cerrar
        </button>
      </div>
    </div>
  </div>

  <!-- ==================== MODAL DE ÍTEM DE CALENDARIO / TO-DO ESTILO NOTION (COMO EN EL VÍDEO) ==================== -->
  <div x-show="modalItemAgenda"
       x-cloak
       x-transition:enter="transition ease-out duration-200"
       x-transition:enter-start="opacity-0"
       x-transition:enter-end="opacity-100"
       x-transition:leave="transition ease-in duration-150"
       x-transition:leave-start="opacity-100"
       x-transition:leave-end="opacity-0"
       :class="modalItemAgenda ? 'flex' : 'hidden'"
       class="fixed inset-0 bg-black/80 backdrop-blur-sm z-50 items-center justify-center p-4">
    <div @click.away="modalItemAgenda = false" class="bg-surface-6 border border-gray-700/80 rounded-3xl max-w-xl w-full p-6 shadow-2xl relative max-h-[92vh] overflow-y-auto">
      <div class="flex items-start justify-between gap-3 pb-4 border-b border-gray-800">
        <div class="flex-1">
          <div class="flex items-center gap-2 mb-1.5">
            <select x-model="itemEditCategoria"
                    class="text-[10px] font-extrabold uppercase px-2.5 py-1 rounded-lg bg-gray-900 border border-gray-700 text-cyan-400 outline-none focus:border-cyan-500">
              <option value="Tema">Tema</option>
              <option value="Clase">Clase</option>
              <option value="Tutoría">Tutoría</option>
              <option value="Actividad">Actividad</option>
              <option value="Entrega">Entrega</option>
              <option value="Práctica">Práctica</option>
              <option value="Test">Test</option>
              <option value="Examen">Examen</option>
              <option value="Proyecto">Proyecto</option>
              <option value="FFE">FFE</option>
              <option value="Cita">📌 Cita / Personal</option>
              <option value="Otro">✨ Otro</option>
            </select>
          </div>
          <input type="text"
                 x-model="itemEditTitulo"
                 placeholder="Ej: Tema 3, Cita médico, Reunión, Examen..."
                 class="w-full text-lg sm:text-xl font-black text-white bg-gray-900/70 hover:bg-gray-900 focus:bg-gray-900 border border-gray-800 focus:border-cyan-500 rounded-xl px-3 py-1.5 outline-none transition">
        </div>
        <button @click="modalItemAgenda = false" class="text-gray-400 hover:text-white p-1.5 rounded-xl hover:bg-gray-800 transition">
          <i class="fa-solid fa-xmark text-lg"></i>
        </button>
      </div>

      <!-- PROPIEDADES ESTILO NOTION -->
      <div class="mt-4 space-y-3 text-xs">
        <!-- Asignatura / Ámbito (Desplegable con TODAS tus asignaturas + Personal/General + botón para escribir nombre personalizado) -->
        <div class="grid grid-cols-3 items-center py-1.5 border-b border-gray-800/60 gap-2">
          <span class="text-gray-400 flex items-center gap-2"><i class="fa-solid fa-book text-[10px]"></i><span>Asignatura / Ámbito</span></span>
          <div class="col-span-2 space-y-1.5">
            <div class="flex items-center gap-1.5">
              <template x-if="!modoAsignaturaPersonalizada">
                <select x-model="itemEditAsignatura"
                        class="flex-1 bg-gray-900 border border-gray-700 focus:border-cyan-500 rounded-xl px-3 py-1.5 text-xs font-bold text-white outline-none">
                  <template x-for="asigNombre in listaAsignaturasCompleta" :key="'sel-edit-' + asigNombre">
                    <option :value="asigNombre" x-text="asigNombre" :selected="itemEditAsignatura === asigNombre"></option>
                  </template>
                </select>
              </template>
              <template x-if="modoAsignaturaPersonalizada">
                <input type="text"
                       x-model="itemEditAsignatura"
                       placeholder="Escribe la asignatura o categoría personal a tu gusto..."
                       class="flex-1 bg-gray-900 border border-cyan-500 rounded-xl px-3 py-1.5 text-xs font-bold text-white outline-none">
              </template>
              <button @click="modoAsignaturaPersonalizada = !modoAsignaturaPersonalizada"
                      type="button"
                      :title="modoAsignaturaPersonalizada ? 'Elegir de la lista de asignaturas' : 'Modificar o escribir nombre a tu gusto'"
                      class="px-2.5 py-1.5 rounded-xl bg-gray-800 hover:bg-gray-700 text-gray-300 hover:text-white text-[11px] font-semibold transition shrink-0">
                <span x-text="modoAsignaturaPersonalizada ? '📋 Lista' : '✏️ Editar'"></span>
              </button>
              <button @click="irAAsignaturaDesdeModal(itemEditAsignatura || itemAgendaActivo?.asignatura)"
                      type="button"
                      title="Abrir vista y materiales de esta materia"
                      class="px-2.5 py-1.5 rounded-xl bg-cyan-500/20 hover:bg-cyan-500/30 border border-cyan-500/40 text-cyan-300 text-[11px] font-bold transition shrink-0 flex items-center gap-1">
                <i class="fa-solid fa-arrow-up-right-from-square text-[10px]"></i>
                <span>Ver materia</span>
              </button>
            </div>
          </div>
        </div>

        <!-- Estado (Selector de píldoras de colores idéntico al vídeo de Notion) -->
        <div class="grid grid-cols-3 items-start py-1.5 border-b border-gray-800/60">
          <span class="text-gray-400 flex items-center gap-2 pt-1"><i class="fa-solid fa-bullseye text-[10px]"></i><span>Estado</span></span>
          <div class="col-span-2 flex flex-wrap gap-1.5">
            <template x-for="est in opcionesEstadoParaItem({ categoria: itemEditCategoria })" :key="est">
              <button @click="itemEditEstado = est"
                      type="button"
                      :class="[
                        claseBadgeEstadoNotion(est),
                        itemEditEstado === est ? 'ring-2 ring-white scale-105' : 'opacity-65 hover:opacity-100'
                      ]"
                      class="px-2.5 py-1 rounded-lg text-[11px] font-bold transition">
                <span x-text="est"></span>
              </button>
            </template>
          </div>
        </div>

        <!-- Fecha y Hora (permite mover el Tema, Cita o Evento al día y hora que el alumno quiera) -->
        <div class="grid grid-cols-3 items-start py-1.5 border-b border-gray-800/60">
          <span class="text-gray-400 flex items-center gap-2 pt-1.5"><i class="fa-regular fa-calendar text-[10px]"></i><span>Fecha y hora</span></span>
          <div class="col-span-2 space-y-2">
            <div class="flex flex-wrap items-center gap-2">
              <input type="date" x-model="itemEditFechaDia" class="bg-gray-900 border border-gray-700 rounded-xl px-3 py-1.5 text-xs text-white outline-none focus:border-cyan-500">
              <span class="text-xs text-cyan-300 font-mono font-bold px-2 py-1 bg-cyan-950/40 border border-cyan-800/60 rounded-lg" x-text="formatearFechaDDMMAAAA(itemEditFechaDia)"></span>
              <input type="time" x-model="itemEditHora" class="bg-gray-900 border border-gray-700 rounded-xl px-2.5 py-1.5 text-xs text-amber-200 font-mono outline-none focus:border-cyan-500" title="Hora de la cita, clase o recordatorio">
              <div class="flex items-center gap-1">
                <button type="button" @click="desplazarFechaModal(-7)" class="px-2 py-1 rounded-lg bg-gray-800 hover:bg-gray-700 text-gray-300 text-[10px] font-bold" title="1 semana antes">-1 sem</button>
                <button type="button" @click="desplazarFechaModal(-1)" class="px-2 py-1 rounded-lg bg-gray-800 hover:bg-gray-700 text-gray-200 text-[10px] font-bold" title="Día anterior">◀ -1 día</button>
                <button type="button" @click="itemEditFechaDia = new Date().toISOString().slice(0, 10)" class="px-2 py-1 rounded-lg bg-cyan-500/20 hover:bg-cyan-500/30 text-cyan-300 text-[10px] font-bold">Hoy</button>
                <button type="button" @click="desplazarFechaModal(1)" class="px-2 py-1 rounded-lg bg-gray-800 hover:bg-gray-700 text-gray-200 text-[10px] font-bold" title="Día siguiente">+1 día ▶</button>
                <button type="button" @click="desplazarFechaModal(7)" class="px-2 py-1 rounded-lg bg-gray-800 hover:bg-gray-700 text-gray-300 text-[10px] font-bold" title="1 semana después">+1 sem</button>
              </div>
            </div>
          </div>
        </div>

        <!-- Puntuación / Nota -->
        <div class="grid grid-cols-3 items-center py-1.5 border-b border-gray-800/60">
          <span class="text-gray-400 flex items-center gap-2"><i class="fa-solid fa-hashtag text-[10px]"></i><span>Puntuación (0-10)</span></span>
          <div class="col-span-2">
            <input type="number" step="0.1" min="0" max="10" x-model="itemEditNota" placeholder="Ej: 8.5"
                   class="w-32 bg-gray-900 border border-gray-700 rounded-xl px-3 py-1.5 text-xs text-emerald-300 font-mono outline-none focus:border-emerald-500">
          </div>
        </div>

        <!-- URL Campus -->
        <div class="grid grid-cols-3 items-center py-1.5 border-b border-gray-800/60">
          <span class="text-gray-400 flex items-center gap-2"><i class="fa-solid fa-link text-[10px]"></i><span>Enlace Campus</span></span>
          <div class="col-span-2">
            <a :href="itemAgendaActivo?.url || 'https://campusfp.unir.net'" target="_blank" class="text-cyan-400 hover:underline text-xs flex items-center gap-1.5">
              <span>Abrir directamente en Canvas UNIR</span>
              <i class="fa-solid fa-arrow-up-right-from-square text-[10px]"></i>
            </a>
          </div>
        </div>

        <!-- Comentarios / Apuntes rápidos -->
        <div class="pt-2">
          <label class="text-gray-400 font-semibold block mb-1.5">Comentarios, detalles de la cita o apuntes personales</label>
          <textarea x-model="itemEditComentario" rows="3" placeholder="Añade aquí tus notas sobre esta clase, tema, cita o recordatorio (solo visibles en tu cuenta)..."
                    class="w-full bg-gray-900/90 border border-gray-700 rounded-2xl p-3 text-xs text-gray-200 outline-none focus:border-cyan-500"></textarea>
        </div>
      </div>

      <div class="mt-6 pt-4 border-t border-gray-800 flex items-center justify-between gap-2">
        <div>
          <button @click="eliminarItemAgendaPropio(itemAgendaActivo?.id)"
                  type="button"
                  class="px-3 py-2 rounded-xl bg-rose-500/15 hover:bg-rose-500/25 text-rose-300 text-xs font-semibold transition border border-rose-500/30">
            Quitar de mi calendario
          </button>
        </div>
        <div class="flex items-center gap-2 ml-auto">
          <button @click="modalItemAgenda = false" type="button" class="px-4 py-2 rounded-xl bg-gray-800 text-gray-300 hover:text-white text-xs transition">
            Cancelar
          </button>
          <button @click="guardarCambiosItemAgenda()"
                  type="button"
                  class="px-5 py-2 rounded-xl bg-gradient-to-r from-cyan-500 to-indigo-600 hover:from-cyan-400 hover:to-indigo-500 text-white font-bold text-xs shadow-lg shadow-cyan-500/20 transition">
            Guardar cambios
          </button>
        </div>
      </div>
    </div>
  </div>

  <!-- ==================== MODAL NUEVO TEMA / TAREA / CITA / RECORDATORIO EN CALENDARIO PERSONAL ==================== -->
  <div x-show="modalNuevoItemAgenda"
       x-cloak
       x-transition:enter="transition ease-out duration-200"
       x-transition:enter-start="opacity-0"
       x-transition:enter-end="opacity-100"
       :class="modalNuevoItemAgenda ? 'flex' : 'hidden'"
       class="fixed inset-0 bg-black/80 backdrop-blur-sm z-50 items-center justify-center p-4">
    <div @click.away="modalNuevoItemAgenda = false" class="bg-surface-6 border border-gray-700/80 rounded-3xl max-w-md w-full p-6 shadow-2xl max-h-[92vh] overflow-y-auto">
      <div class="flex items-center justify-between pb-3 border-b border-gray-800">
        <h3 class="text-base font-extrabold text-white">Añadir a Mi Calendario / To-do</h3>
        <button @click="modalNuevoItemAgenda = false" class="text-gray-400 hover:text-white p-1 rounded-lg"><i class="fa-solid fa-xmark"></i></button>
      </div>

      <!-- FORMULARIO MANUAL -->
      <div class="mt-4 space-y-3 text-xs">
        <!-- SELECTOR RÁPIDO: ¿DÓNDE QUIERES AÑADIRLO? -->
        <div>
          <label class="text-gray-400 block mb-1.5">¿Dónde quieres añadirlo?</label>
          <div class="grid grid-cols-2 gap-1.5 bg-gray-950 p-1 rounded-xl border border-gray-800">
            <button type="button"
                    @click="cambiarDestinoNuevoItem('calendario')"
                    :class="nuevoItemDestino === 'calendario' ? 'bg-cyan-500/20 text-cyan-300 border border-cyan-500/40 font-bold' : 'text-gray-400 hover:text-white'"
                    class="py-1.5 px-2 rounded-lg text-[11px] transition flex items-center justify-center gap-1.5">
              <i class="fa-regular fa-calendar-days"></i>
              <span>Calendario / Semana</span>
            </button>
            <button type="button"
                    @click="cambiarDestinoNuevoItem('hoy')"
                    :class="nuevoItemDestino === 'hoy' ? 'bg-sky-500/20 text-sky-300 border border-sky-500/40 font-bold' : 'text-gray-400 hover:text-white'"
                    class="py-1.5 px-2 rounded-lg text-[11px] transition flex items-center justify-center gap-1.5">
              <i class="fa-solid fa-bolt"></i>
              <span>To-do hoy</span>
            </button>
          </div>
        </div>

        <div>
          <label class="text-gray-400 block mb-1">Título <span class="text-[10px] text-gray-500 font-normal">(opcional para clases y tutorías)</span></label>
          <input type="text" x-model="nuevoItemTitulo" :placeholder="['Clase', 'Tutoría'].includes(nuevoItemCategoria) ? ('Ej: Clase ' + (nuevoItemHora || '19:00') + ' (o déjalo vacío para automático)') : 'Ej: Tema 3 - Ficheros XML o Cita a las 17:30'" class="w-full bg-gray-900 border border-gray-700 rounded-xl px-3 py-2 text-white outline-none focus:border-cyan-500">
        </div>
        <div class="grid grid-cols-12 gap-2">
          <div class="col-span-5">
            <label class="text-gray-400 block mb-1">Tipo</label>
            <select x-model="nuevoItemCategoria" @change="actualizarEstadoSegunCategoriaNuevo()" class="w-full bg-gray-900 border border-gray-700 rounded-xl px-2.5 py-2 text-white outline-none">
              <option value="Tema">Tema</option>
              <option value="Clase">Clase</option>
              <option value="Tutoría">Tutoría</option>
              <option value="Actividad">Actividad</option>
              <option value="Entrega">Entrega</option>
              <option value="Práctica">Práctica</option>
              <option value="Test">Test</option>
              <option value="Examen">Examen</option>
              <option value="Proyecto">Proyecto</option>
              <option value="FFE">FFE</option>
              <option value="Cita">📌 Cita / Personal</option>
              <option value="Otro">✨ Otro</option>
            </select>
          </div>
          <div class="col-span-4">
            <div class="flex items-center justify-between mb-1">
              <label class="text-gray-400 block">Fecha</label>
              <span class="text-[10px] text-cyan-300 font-mono font-bold" x-text="formatearFechaDDMMAAAA(nuevoItemFecha)"></span>
            </div>
            <input type="date" x-model="nuevoItemFecha" class="w-full bg-gray-900 border border-gray-700 rounded-xl px-2.5 py-2 text-white outline-none">
          </div>
          <div class="col-span-3">
            <label class="text-gray-400 block mb-1">Hora (opc.)</label>
            <input type="time" x-model="nuevoItemHora" class="w-full bg-gray-900 border border-gray-700 rounded-xl px-2 py-2 text-amber-200 font-mono outline-none">
          </div>
        </div>

        <div>
          <div class="flex items-center justify-between mb-1">
            <label class="text-gray-400">Asignatura o ámbito (ej. Personal / General)</label>
            <button type="button"
                    @click="modoNuevaAsignaturaPersonalizada = !modoNuevaAsignaturaPersonalizada"
                    class="text-[10px] text-cyan-400 hover:underline font-semibold">
              <span x-text="modoNuevaAsignaturaPersonalizada ? '📋 Elegir de la lista' : '✏️ Escribir otro a mi gusto'"></span>
            </button>
          </div>
          <template x-if="!modoNuevaAsignaturaPersonalizada">
            <select x-model="nuevoItemAsignatura" class="w-full bg-gray-900 border border-gray-700 rounded-xl px-3 py-2 text-white font-semibold outline-none focus:border-cyan-500">
              <template x-for="asigNombre in listaAsignaturasCompleta" :key="'sel-new-' + asigNombre">
                <option :value="asigNombre" x-text="asigNombre" :selected="nuevoItemAsignatura === asigNombre"></option>
              </template>
            </select>
          </template>
          <template x-if="modoNuevaAsignaturaPersonalizada">
            <input type="text" x-model="nuevoItemAsignatura" placeholder="Ej: Personal, Médico, Trabajo, Entrevista..." class="w-full bg-gray-900 border border-cyan-500 rounded-xl px-3 py-2 text-white outline-none">
          </template>
        </div>
        <div>
          <label class="text-gray-400 block mb-1.5">Estado inicial</label>
          <div class="grid grid-cols-2 gap-2">
            <template x-for="op in opcionesEstadoNuevoModal(nuevoItemCategoria)" :key="'est-op-' + op.valor">
              <button type="button"
                      @click="nuevoItemEstado = op.valor"
                      :class="nuevoItemEstado === op.valor ? (op.claseActiva + ' ring-2 ring-white/80 shadow-lg scale-[1.01]') : 'bg-gray-900/90 border-gray-800 text-gray-400 hover:bg-gray-800/80 hover:text-gray-200'"
                      class="p-2.5 rounded-xl border text-left transition flex items-start gap-2.5">
                <span class="w-2.5 h-2.5 rounded-full mt-1 shrink-0" :class="op.dot"></span>
                <div class="min-w-0 flex-1">
                  <div class="font-bold text-xs leading-tight flex items-center justify-between gap-1">
                    <span x-text="op.titulo"></span>
                    <i x-show="nuevoItemEstado === op.valor" class="fa-solid fa-check text-[10px]"></i>
                  </div>
                  <p class="text-[10px] opacity-80 mt-0.5 truncate" x-text="op.desc"></p>
                </div>
              </button>
            </template>
          </div>
        </div>

        <div class="mt-5 pt-3 border-t border-gray-800 flex justify-end gap-2">
          <button @click="modalNuevoItemAgenda = false" class="px-4 py-2 rounded-xl bg-gray-800 hover:bg-gray-700 text-gray-300 text-xs transition">Cancelar</button>
          <button @click="crearNuevoItemAgenda()" class="px-5 py-2 rounded-xl bg-gradient-to-r from-cyan-500 to-sky-500 hover:from-cyan-400 hover:to-sky-400 text-gray-950 font-extrabold text-xs shadow-lg shadow-cyan-500/20 transition">Añadir al Calendario</button>
        </div>
      </div>
    </div>
  </div>


  <!-- ==================== BARRA DE NAVEGACIÓN INFERIOR PARA MÓVILES (ESTILO APP NATIVA) ==================== -->
  <nav class="md:hidden fixed bottom-0 inset-x-0 z-40 bg-surface-3/95 border-t border-gray-800/90 backdrop-blur-lg flex items-center justify-around px-2 py-1.5 shadow-2xl safe-area-pb select-none">
    <button @click="irASeccion('dashboard')" 
            :class="tab === 'dashboard' ? 'text-cyan-400 font-bold' : 'text-gray-400 hover:text-gray-200'"
            class="flex flex-col items-center justify-center p-1.5 transition flex-1 text-center">
      <i class="fa-solid fa-house text-base mb-0.5" :class="tab === 'dashboard' ? 'text-cyan-400' : 'text-gray-400'"></i>
      <span class="text-[10px]">Inicio</span>
    </button>
    <button @click="irASeccion('asignaturas')" 
            :class="tab === 'asignaturas' ? 'text-cyan-400 font-bold' : 'text-gray-400 hover:text-gray-200'"
            class="flex flex-col items-center justify-center p-1.5 transition flex-1 text-center">
      <i class="fa-solid fa-folder-tree text-base mb-0.5" :class="tab === 'asignaturas' ? 'text-cyan-400' : 'text-gray-400'"></i>
      <span class="text-[10px]">Materias</span>
    </button>
    <button @click="irASeccion('clases')" 
            :class="tab === 'clases' ? 'text-amber-400 font-bold' : 'text-gray-400 hover:text-gray-200'"
            class="flex flex-col items-center justify-center p-1.5 transition flex-1 text-center">
      <i class="fa-solid fa-video text-base mb-0.5" :class="tab === 'clases' ? 'text-amber-400' : 'text-gray-400'"></i>
      <span class="text-[10px]">Clases</span>
    </button>
    <button @click="irASeccion('flashcards')" 
            :class="tab === 'flashcards' ? 'text-purple-400 font-bold' : 'text-gray-400 hover:text-gray-200'"
            class="flex flex-col items-center justify-center p-1.5 transition flex-1 text-center">
      <i class="fa-solid fa-clone text-base mb-0.5" :class="tab === 'flashcards' ? 'text-purple-400' : 'text-gray-400'"></i>
      <span class="text-[10px]">Cards</span>
    </button>
    <button @click="irASeccion('tutor')" 
            :class="tab === 'tutor' ? 'text-cyan-400 font-bold' : 'text-gray-400 hover:text-gray-200'"
            class="flex flex-col items-center justify-center p-1.5 transition flex-1 text-center">
      <i class="fa-solid fa-robot text-base mb-0.5" :class="tab === 'tutor' ? 'text-cyan-400' : 'text-gray-400'"></i>
      <span class="text-[10px]">NOVA</span>
    </button>
  </nav>

  <script>
    function studyHubApp() {
      return {
        tab: 'dashboard',
        sidebarVisible: window.innerWidth >= 768 && localStorage.getItem('unir_sidebar_visible') !== 'false',
        
        // Usuario y Autenticación
        usuarioActual: null,
        adminUsuarios: [],
        adminCargando: false,
        adminPendientesCount: 0,

        // Sistema de Pestañas Multi-tarea
        pestanasAbiertas: [
          { id: 'sec-dashboard', titulo: 'Inicio', icono: 'fa-house', tipo: 'seccion', seccion: 'dashboard', url: '/?tab=dashboard' }
        ],
        pestanaActivaId: 'sec-dashboard',
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
        generadorProfundidad: 'robusta',
        generadorNumPreguntas: 15,
        generadorTipo: 'ambos', // 'ambos', 'solo_guia', 'solo_test'
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
        chatAbortController: null,
        chatHistorial: [],
        chatHistorialesPorAsignatura: {},
        chatModeloSeleccionado: localStorage.getItem('unir_modelo_ia') || 'auto',
        chatModeloActualNombre: 'Gemini 3.5 Flash',
        modelosIaDisponibles: [],

        // BYOK (Bring Your Own Key) y Proveedores de IA con Triple Redundancia
        userGeminiKey: localStorage.getItem('unir_user_gemini_key') || '',
        userOpenrouterKey: localStorage.getItem('unir_openrouter_key') || '',
        userGroqKey: localStorage.getItem('unir_groq_key') || '',
        modalApiKey: false,
        inputApiKeyTemp: '',
        inputOpenrouterTemp: '',
        inputGroqTemp: '',
        mostrarKeyGemini: false,
        mostrarKeyOpenrouter: false,
        mostrarKeyGroq: false,
        apiKeyValidando: false,
        apiKeyMensajeError: '',
        apiKeyMensajeExito: '',

        // Conexión Personal al Campus UNIR (Canvas por Estudiante) y Dashboard Personal
        modalEntregasMes: false,
        modalMiCampus: false,
        verEntregasDelMes() {
          if (this.entregasMesActual.length === 0) {
            alert('No tienes entregas pendientes en ' + this.nombreMesCalendario);
            return;
          }
          // Filtra el calendario para destacar tareas y abre el panel rápido de entregas
          this.calFiltroTipo = 'tarea';
          this.modalEntregasMes = true;
        },
        miCampusConfig: { conectado: false },
        miCampusCursos: [],
        miCampusCalificaciones: [],
        filtroCalTexto: '',
        filtroCalAsignatura: 'todas',
        filtroCalCurso: 'todos',
        asignaturaExpandidaCal: null,
        toggleExpandirAsignaturaCal(asig) {
          this.asignaturaExpandidaCal = (this.asignaturaExpandidaCal === asig ? null : asig);
        },
        get asignaturasConCalificaciones() {
          const s = new Set();
          (this.miCampusCalificaciones || []).forEach(c => { if (c.asignatura) s.add(c.asignatura); });
          return Array.from(s).sort();
        },
        get calificacionesFiltradas() {
          let lista = this.miCampusCalificaciones || [];
          if (this.filtroCalTexto.trim()) {
            const q = this.filtroCalTexto.toLowerCase().trim();
            lista = lista.filter(c => (c.titulo || '').toLowerCase().includes(q) || (c.asignatura || '').toLowerCase().includes(q));
          }
          if (this.filtroCalAsignatura !== 'todas') {
            lista = lista.filter(c => c.asignatura === this.filtroCalAsignatura);
          }
          if (this.filtroCalCurso === '2dam') {
            lista = lista.filter(c => !(c.asignatura || '').startsWith('1º DAM'));
          } else if (this.filtroCalCurso === '1dam') {
            lista = lista.filter(c => (c.asignatura || '').startsWith('1º DAM'));
          }
          return lista;
        },
        get calificacionesAgrupadasPorAsignatura() {
          const grupos = {};
          const lista = this.calificacionesFiltradas;
          lista.forEach(item => {
            const asig = item.asignatura || 'Sin Asignatura';
            if (!grupos[asig]) {
              grupos[asig] = {
                asignatura: asig,
                es_primero: asig.startsWith('1º DAM'),
                notas: [],
                media: 0
              };
            }
            grupos[asig].notas.push(item);
          });
          const resultado = Object.values(grupos);
          resultado.forEach(g => {
            const validas = g.notas.map(n => (n.nota_sobre_10 !== null ? n.nota_sobre_10 : n.score)).filter(n => typeof n === 'number' && !isNaN(n));
            g.media = validas.length ? (validas.reduce((a, b) => a + b, 0) / validas.length) : null;
          });
          // Ordenar: primero asignaturas de 2º y luego las de 1º, y alfabéticamente
          return resultado.sort((a, b) => {
            if (a.es_primero !== b.es_primero) return a.es_primero ? 1 : -1;
            return a.asignatura.localeCompare(b.asignatura);
          });
        },
        inputCanvasToken: '',
        inputCanvasTokenExpira: '',
        inputCanvasIcsUrl: '',
        sincronizandoMiCampus: false,
        miCampusMensajeError: '',
        miCampusMensajeExito: '',
        asignaturasFavoritas: [],
        mostrarSoloFavoritas: false,
        actividadReciente: [],

        // Lectura por voz de NOVA (Audio Real Neuronal Edge-TTS)
        autoVozActiva: localStorage.getItem('unir_auto_voz') === 'true',
        // Temas de color personalizables (definidos en <head> como window.NOVA_TEMAS)
        temasDisponibles: window.NOVA_TEMAS || [],
        temaActual: document.documentElement.getAttribute('data-tema') || 'nova',
        menuTemaAbierto: false,
        cambiarTema(id) {
          this.temaActual = window.novaAplicarTema(id);
          localStorage.setItem('nova_tema', this.temaActual);
        },
        previewTema(t) {
          const p = window.NOVA_PALETAS || {};
          const s = t.surf.split(' ');
          return [s[2], (p[t.cyan] || '').split(' ')[4], (p[t.sky] || '').split(' ')[4], (p[t.indigo] || '').split(' ')[5]];
        },
        vozMensajeActivoIdx: null,
        reproductorAudio: null,

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

        // Agenda y Calendario Mensual Interactivo (Estilo Notion DAM)
        agendaEventos: [],
        sincronizandoAgenda: false,
        calAnio: new Date().getFullYear(),
        calMes: new Date().getMonth(),
        calDiaSeleccionado: new Date().toISOString().slice(0, 10),
        calFiltroTipo: 'todos',
        calModoVista: 'quincena', // 'quincena' (Lun-Vie 2 semanas como el vídeo de Notion) o 'mes' (Lun-Dom mes completo)
        calOffsetSemanas: 0,
        incluirAtrasadosTodo: false,

        // Modal Entrenamientos Oficiales del Tema y Retos con IA
        modalEntrenamientos: false,
        entrenamientosCargando: false,
        entrenamientosDatos: null,
        entrenamientoActivoIndice: 0,
        entrenamientosError: '',
        entrenamientosTemaInfo: null,
        copiadoRetoToast: false,
        generadorIaAbierto: false,
        generandoRetoIa: false,
        generadorIaDificultad: 'examen',
        generadorIaEnfoque: '',
        generadorIaError: '',

        async abrirModalEntrenamientos(asig, temaNum, temaTitulo) {
          this.modalEntrenamientos = true;
          this.entrenamientosCargando = true;
          this.entrenamientosError = '';
          this.entrenamientosDatos = null;
          this.entrenamientoActivoIndice = 0;
          this.generadorIaAbierto = false;
          this.generandoRetoIa = false;
          this.generadorIaError = '';
          this.generadorIaEnfoque = '';
          this.entrenamientosTemaInfo = { asignatura: asig, temaNum: temaNum, temaTitulo: temaTitulo };
          try {
            const res = await fetch(`/api/asignatura/tema/entrenamientos?asignatura=${encodeURIComponent(asig)}&tema_num=${encodeURIComponent(temaNum)}`);
            const data = await res.json();
            if (data.status === 'error' || !data.entrenamientos || !data.entrenamientos.length) {
              this.entrenamientosError = data.mensaje || 'No se pudieron extraer los entrenamientos de este tema.';
            } else {
              this.entrenamientosDatos = data;
            }
          } catch(e) {
            this.entrenamientosError = 'Error de conexión al cargar entrenamientos.';
          } finally {
            this.entrenamientosCargando = false;
          }
        },

        async generarNuevoRetoIa() {
          const tieneKey = this.userGeminiKey || this.userOpenrouterKey || this.userGroqKey || this.usuarioActual?.es_admin;
          if (!tieneKey) {
            this.generadorIaError = 'Para generar retos con IA necesitas configurar tu clave gratuita de Gemini o Groq. Pulsa en «Claves IA» arriba.';
            return;
          }
          this.generandoRetoIa = true;
          this.generadorIaError = '';
          try {
            const asig = this.entrenamientosTemaInfo?.asignatura || this.asigSeleccionada?.nombre || '';
            const temaNum = this.entrenamientosTemaInfo?.temaNum || 1;
            const temaTitulo = this.entrenamientosTemaInfo?.temaTitulo || '';
            const res = await fetch('/api/asignatura/tema/generar-entrenamiento', {
              method: 'POST',
              headers: {
                'Content-Type': 'application/json',
                ...(this.userGeminiKey ? { 'X-User-Gemini-Key': this.userGeminiKey } : {}),
                ...(this.userOpenrouterKey ? { 'X-User-Openrouter-Key': this.userOpenrouterKey } : {}),
                ...(this.userGroqKey ? { 'X-User-Groq-Key': this.userGroqKey } : {})
              },
              body: JSON.stringify({
                asignatura: asig,
                tema_num: parseInt(temaNum),
                tema_titulo: temaTitulo,
                dificultad: this.generadorIaDificultad || 'examen',
                enfoque: this.generadorIaEnfoque || '',
                api_key: (this.userGeminiKey && this.userGeminiKey.trim()) || null
              })
            });
            const data = await res.json();
            if (!res.ok || data.status === 'error') {
              this.generadorIaError = data.detail || data.mensaje || 'Error al generar el reto con IA.';
              return;
            }
            if (data.entrenamiento) {
              if (!this.entrenamientosDatos) {
                this.entrenamientosDatos = { status: 'ok', asignatura: asig, tema_num: temaNum, entrenamientos: [], total_oficiales: 0, total_ia: 0, total: 0 };
              }
              this.entrenamientosDatos.entrenamientos.push(data.entrenamiento);
              this.entrenamientosDatos.total = this.entrenamientosDatos.entrenamientos.length;
              this.entrenamientosDatos.total_ia = (this.entrenamientosDatos.total_ia || 0) + 1;
              this.entrenamientoActivoIndice = this.entrenamientosDatos.entrenamientos.length - 1;
              this.generadorIaAbierto = false;
              this.generadorIaEnfoque = '';
            }
          } catch(e) {
            this.generadorIaError = 'Error de red al conectar con el motor de IA.';
          } finally {
            this.generandoRetoIa = false;
          }
        },

        async eliminarRetoIaActual() {
          const reto = this.entrenamientosDatos?.entrenamientos?.[this.entrenamientoActivoIndice];
          if (!reto || !reto.es_ia) return;
          if (!confirm(`¿Eliminar este reto práctico generado por IA ("${reto.titulo}")?`)) return;
          const asig = this.entrenamientosTemaInfo?.asignatura || this.asigSeleccionada?.nombre || '';
          const temaNum = this.entrenamientosTemaInfo?.temaNum || 1;
          try {
            await fetch('/api/asignatura/tema/eliminar-entrenamiento', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({
                asignatura: asig,
                tema_num: parseInt(temaNum),
                reto_id: reto.id
              })
            });
            this.entrenamientosDatos.entrenamientos.splice(this.entrenamientoActivoIndice, 1);
            this.entrenamientosDatos.total = this.entrenamientosDatos.entrenamientos.length;
            this.entrenamientosDatos.total_ia = Math.max(0, (this.entrenamientosDatos.total_ia || 1) - 1);
            this.entrenamientoActivoIndice = Math.max(0, this.entrenamientoActivoIndice - 1);
          } catch(e) {
            console.error(e);
          }
        },

        copiarTextoReto(texto) {
          navigator.clipboard.writeText(texto).then(() => {
            this.copiadoRetoToast = true;
            setTimeout(() => this.copiadoRetoToast = false, 2500);
          });
        },

        pedirPistaRetoNova(reto) {
          this.modalEntrenamientos = false;
          const asig = this.entrenamientosTemaInfo?.asignatura || this.asigSeleccionada?.nombre || '';
          const tmNum = this.entrenamientosTemaInfo?.temaNum || 1;
          const etiqueta = reto.es_ia ? 'generado con IA' : `Oficial ${reto.numero || ''}`;
          const prompt = `NOVA, estoy programando el Reto ${etiqueta} ("${reto.titulo}") del Tema ${tmNum} de ${asig}:\n\n"${reto.enunciado}"\n\n¿Podrías darme una pista pedagógica para orientarme en el diseño de clases, métodos y excepciones necesarias para resolverlo sin darme el código de la solución todavía?`;
          this.abrirChatConAsignatura(asig);
          this.chatInput = prompt;
          this.enviarMensajeChat();
        },

        // Modal Editar / Nuevo Item Agenda Notion + Drag & Drop
        modalItemAgenda: false,
        itemAgendaActivo: null,
        itemEditTitulo: '',
        itemEditAsignatura: '',
        itemEditCategoria: 'Tema',
        itemEditEstado: '',
        itemEditFechaDia: '',
        itemEditHora: '',
        itemEditNota: '',
        itemEditComentario: '',
        modoAsignaturaPersonalizada: false,
        temasEstado: {},
        filtroPacTipo: 'todas',
        itemArrastrando: null,
        diaArrastreSobre: '',
        modalNuevoItemAgenda: false,
        nuevoItemDestino: 'calendario',
        nuevoItemTitulo: '',
        nuevoItemCategoria: 'Tema',
        nuevoItemFecha: new Date().toISOString().slice(0, 10),
        nuevoItemHora: '',
        nuevoItemAsignatura: '',
        nuevoItemEstado: 'No hecho',
        modoNuevaAsignaturaPersonalizada: false,

        // Flashcards Repetición Espaciada
        flashcards: [],
        flashcardIndice: 0,
        flashcardVolteada: false,
        flashcardFiltroAsig: '',
        flashcardVistaModo: 'estudio', // 'estudio' o 'catalogo'
        flashcardBusqueda: '',
        flashcardSesionCompletada: false,
        flashcardStatsSesion: { facil: 0, medio: 0, dificil: 0 },
        modalGenerarFlashcards: false,
        nuevaCardAsig: '',
        nuevaCardTema: '',
        nuevaCardCantidad: 4,
        generandoFlashcards: false,

        // Buzón de Incidencias y Mejoras
        feedbackLista: [],
        feedbackCargando: false,
        feedbackPendientesCount: 0,
        feedbackFiltroTipo: 'todos',
        feedbackFiltroEstado: 'todos',
        feedbackNuevoTipo: 'fallo',
        feedbackNuevoAsig: '',
        feedbackNuevoTitulo: '',
        feedbackNuevoDesc: '',
        feedbackEnviando: false,
        feedbackMensajeExito: '',
        feedbackMensajeError: '',
        feedbackEditandoRespId: null,
        feedbackTextoRespTemp: '',

        feedbackFiltrado() {
          return (this.feedbackLista || []).filter(item => {
            if (this.feedbackFiltroTipo !== 'todos' && item.tipo !== this.feedbackFiltroTipo) {
              return false;
            }
            if (this.feedbackFiltroEstado !== 'todos' && item.estado !== this.feedbackFiltroEstado) {
              return false;
            }
            return true;
          });
        },

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

        get totalGuiasPrograma() {
          return this.catalogoFiltrado.reduce((acc, a) => acc + (a.guias?.length || 0), 0);
        },

        get eventosCalendarioFiltrados() {
          let evs = this.agendaEventos || [];
          if (this.mostrarSoloFavoritas && this.asignaturasFavoritas.length > 0) {
            evs = evs.filter(e => this.asignaturasFavoritas.includes(e.asignatura));
          }
          if (this.calFiltroTipo !== 'todos') {
            evs = evs.filter(e => e.tipo === this.calFiltroTipo);
          }
          return evs;
        },

        get entregasCanvasTab() {
          return (this.agendaEventos || []).filter(e => e.tipo === 'tarea' || e.tipo === 'clase_directo');
        },

        get listaAsignaturasCompleta() {
          const base = [
            'Acceso a Datos',
            'Desarrollo de Interfaces',
            'Programación de Servicios y Procesos',
            'Programación Multimedia y Dispositivos Móviles',
            'Sistemas de Gestión Empresarial',
            'Arquitecturas en la Nube',
            'Proyecto Intermodular DAM',
            'Digitalización Aplicada a los Sectores Productivos',
            'Sostenibilidad Aplicada al Sistema Productivo',
            'Fase de Formación en Empresa (FFE)',
            'Inglés Profesional',
            'Personal / General'
          ];
          const conjunto = new Set(base);
          (this.catalogoTotal || []).forEach(a => {
            if (a?.nombre) conjunto.add(a.nombre);
          });
          (this.agendaEventos || []).forEach(ev => {
            if (ev?.asignatura) conjunto.add(ev.asignatura);
          });
          return Array.from(conjunto);
        },

        get esPeriodoActualCalendario() {
          if (this.calModoVista === 'quincena') {
            return this.calOffsetSemanas === 0;
          }
          const hoy = new Date();
          return this.calAnio === hoy.getFullYear() && this.calMes === hoy.getMonth();
        },

        get etiquetaPeriodoCalendario() {
          const mesesCortos = ['Ene', 'Feb', 'Mar', 'Abr', 'May', 'Jun', 'Jul', 'Ago', 'Sep', 'Oct', 'Nov', 'Dic'];
          if (this.calModoVista === 'quincena') {
            const celdas = this.celdasCalendarioVisibles;
            if (celdas.length > 0) {
              const ini = celdas[0].fechaStr;
              const fin = celdas[celdas.length - 1].fechaStr;
              if (ini && fin) {
                const pIni = ini.split('-');
                const pFin = fin.split('-');
                const dIni = parseInt(pIni[2], 10);
                const mIni = mesesCortos[parseInt(pIni[1], 10) - 1];
                const dFin = parseInt(pFin[2], 10);
                const mFin = mesesCortos[parseInt(pFin[1], 10) - 1];
                const rangoStr = mIni === mFin ? `${dIni} - ${dFin} ${mFin}` : `${dIni} ${mIni} - ${dFin} ${mFin}`;
                if (this.calOffsetSemanas === 0) {
                  return `Hoy (${rangoStr})`;
                }
                const signo = this.calOffsetSemanas > 0 ? `+${this.calOffsetSemanas} sem` : `${this.calOffsetSemanas} sem`;
                return `${rangoStr} (${signo})`;
              }
            }
            return this.calOffsetSemanas === 0 ? 'Hoy' : `${this.calOffsetSemanas > 0 ? '+' : ''}${this.calOffsetSemanas} sem`;
          }
          const hoy = new Date();
          const mCorto = mesesCortos[this.calMes];
          if (this.calAnio === hoy.getFullYear() && this.calMes === hoy.getMonth()) {
            return `Hoy (${mCorto} ${this.calAnio})`;
          }
          return `${mCorto} ${this.calAnio} (Ir a Hoy)`;
        },

        get nombreMesCalendario() {
          const meses = ['Enero', 'Febrero', 'Marzo', 'Abril', 'Mayo', 'Junio', 'Julio', 'Agosto', 'Septiembre', 'Octubre', 'Noviembre', 'Diciembre'];
          if (this.calModoVista === 'quincena') {
            const celdas = this.celdasCalendarioVisibles;
            if (celdas.length > 0) {
              const fInicio = celdas[0].fechaStr;
              if (fInicio) {
                const partes = fInicio.split('-');
                const mIdx = parseInt(partes[1], 10) - 1;
                return `${meses[mIdx]} ${partes[0]}`;
              }
            }
          }
          return `${meses[this.calMes]} ${this.calAnio}`;
        },

        get clasesMesActualCount() {
          const prefijo = `${this.calAnio}-${String(this.calMes + 1).padStart(2, '0')}`;
          return (this.agendaEventos || []).filter(e => e.tipo === 'clase_directo' && (e.fecha_dia || (e.fecha_iso || '').slice(0, 10)).startsWith(prefijo)).length;
        },

        get entregasMesActual() {
          const prefijo = `${this.calAnio}-${String(this.calMes + 1).padStart(2, '0')}`;
          let evs = (this.agendaEventos || []).filter(e => {
            if (e.tipo !== 'tarea' || e.entregada) return false;
            const f = e.fecha_dia || (e.fecha_iso || '').slice(0, 10);
            return f.startsWith(prefijo);
          });
          if (this.mostrarSoloFavoritas && this.asignaturasFavoritas.length > 0) {
            evs = evs.filter(e => this.asignaturasFavoritas.includes(e.asignatura));
          }
          return evs.sort((a, b) => (a.dias_restantes ?? 999) - (b.dias_restantes ?? 999));
        },

        get proximasEntregasRojas() {
          let evs = (this.agendaEventos || []).filter(e => e.tipo === 'tarea' && (e.dias_restantes === undefined || e.dias_restantes >= -1) && !e.entregada);
          if (this.mostrarSoloFavoritas && this.asignaturasFavoritas.length > 0) {
            evs = evs.filter(e => this.asignaturasFavoritas.includes(e.asignatura));
          }
          return evs.sort((a, b) => (a.dias_restantes ?? 999) - (b.dias_restantes ?? 999));
        },


        // To-do hoy (estilo Notion: lo que tiene fecha de hoy + tareas sin fecha en 'En proceso')
        get todoHoyLista() {
          const hoyStr = new Date().toISOString().slice(0, 10);
          let evs = this.agendaEventos || [];
          if (this.mostrarSoloFavoritas && this.asignaturasFavoritas.length > 0) {
            evs = evs.filter(e => this.asignaturasFavoritas.includes(e.asignatura));
          }
          return evs.filter(ev => {
            const est = ev.estado || '';
            if (['Vista', 'Vista no relevante', 'Hecho'].includes(est) || ev.entregada) return false;
            const f = ev.fecha_dia || (ev.fecha_iso || '').slice(0, 10);
            if (f) {
              return f === hoyStr;
            }
            return ['En proceso', 'Pendiente de empezar'].includes(est);
          }).sort((a, b) => {
            const hA = (a.hora_inicio || '').trim();
            const hB = (b.hora_inicio || '').trim();
            if (hA && hB) return hA.localeCompare(hB);
            if (hA && !hB) return -1;
            if (!hA && hB) return 1;
            return (a.titulo || '').localeCompare(b.titulo || '');
          });
        },

        // To-do semana (estilo Notion: clases, tutorías, entregas y temas pendientes de esta semana + atrasados si se activa)
        get todoSemanaLista() {
          const hoy = new Date();
          const diaSem = (hoy.getDay() + 6) % 7; // Lunes = 0
          const lunes = new Date(hoy);
          lunes.setDate(hoy.getDate() - diaSem);
          const domingo = new Date(lunes);
          domingo.setDate(lunes.getDate() + 6);
          const lunesStr = lunes.toISOString().slice(0, 10);
          const domingoStr = domingo.toISOString().slice(0, 10);

          let evs = this.agendaEventos || [];
          if (this.mostrarSoloFavoritas && this.asignaturasFavoritas.length > 0) {
            evs = evs.filter(e => this.asignaturasFavoritas.includes(e.asignatura));
          }
          return evs.filter(ev => {
            const est = ev.estado || '';
            if (['Vista', 'Vista no relevante', 'Hecho'].includes(est) || ev.entregada) return false;
            const f = ev.fecha_dia || (ev.fecha_iso || '').slice(0, 10);
            if (!f) return false;
            if (f >= lunesStr && f <= domingoStr) return true;
            if (this.incluirAtrasadosTodo && f < lunesStr) return true;
            return false;
          }).sort((a, b) => (a.fecha_dia || '').localeCompare(b.fecha_dia || '')).slice(0, 25);
        },

        ordenarEventosDia(lista) {
          if (!lista || !Array.isArray(lista)) return [];
          return [...lista].sort((a, b) => {
            const hA = (a.hora_inicio || '').trim();
            const hB = (b.hora_inicio || '').trim();
            // Orden cronológico estricto por hora si ambos tienen hora (ej: 09:00, 09:15, 11:15, 13:05, 14:00)
            if (hA && hB) {
              const compHora = hA.localeCompare(hB);
              if (compHora !== 0) return compHora;
            } else if (hA && !hB) {
              return -1;
            } else if (!hA && hB) {
              return 1;
            }
            if (a.tipo !== b.tipo) return a.tipo === 'tarea' ? -1 : 1;
            return (a.titulo || '').localeCompare(b.titulo || '');
          });
        },

        get eventosDiaSeleccionado() {
          if (!this.calDiaSeleccionado) return [];
          const filtrados = this.eventosCalendarioFiltrados
            .filter(e => (e.fecha_dia || (e.fecha_iso || '').slice(0, 10)) === this.calDiaSeleccionado);
          return this.ordenarEventosDia(filtrados);
        },

        // Celdas visibles en el Calendario (Lun-Vie 2 semanas en modo 'quincena', o Mes completo en modo 'mes')
        get celdasCalendarioVisibles() {
          if (this.calModoVista === 'mes') {
            return this.diasCalendarioMes;
          }
          // Modo 'quincena' (Lun-Vie de la semana actual + siguiente semana, ajustable con calOffsetSemanas)
          const hoy = new Date();
          const hoyStr = hoy.toISOString().slice(0, 10);
          const diaSem = (hoy.getDay() + 6) % 7; // Lunes = 0
          const lunesBase = new Date(hoy);
          lunesBase.setDate(hoy.getDate() - diaSem + (this.calOffsetSemanas * 7));

          const mapaEventos = {};
          this.eventosCalendarioFiltrados.forEach(ev => {
            const f = ev.fecha_dia || (ev.fecha_iso || '').slice(0, 10);
            if (!f) return;
            if (!mapaEventos[f]) mapaEventos[f] = [];
            mapaEventos[f].push(ev);
          });

          const celdas = [];
          for (let sem = 0; sem < 2; sem++) {
            for (let d = 0; d < 5; d++) { // Lunes (0) a Viernes (4), además incluimos eventos del fin de semana en el Viernes si caen en Sáb/Dom
              const fecha = new Date(lunesBase);
              fecha.setDate(lunesBase.getDate() + (sem * 7) + d);
              const anio = fecha.getFullYear();
              const mes = fecha.getMonth();
              const diaNum = fecha.getDate();
              const fechaStr = `${anio}-${String(mes + 1).padStart(2, '0')}-${String(diaNum).padStart(2, '0')}`;
              let evsDia = [...(mapaEventos[fechaStr] || [])];
              // Si es viernes, añadir también entregas que venzan sábado o domingo para no perderlas en vista Lun-Vie
              if (d === 4) {
                for (let extra = 1; extra <= 2; extra++) {
                  const fFin = new Date(fecha);
                  fFin.setDate(fecha.getDate() + extra);
                  const fFinStr = `${fFin.getFullYear()}-${String(fFin.getMonth() + 1).padStart(2, '0')}-${String(fFin.getDate()).padStart(2, '0')}`;
                  if (mapaEventos[fFinStr]) {
                    evsDia.push(...mapaEventos[fFinStr]);
                  }
                }
              }
              const evsOrdenados = this.ordenarEventosDia(evsDia);
              const tieneEntregaRoja = evsOrdenados.some(e => e.tipo === 'tarea' && !e.entregada);
              celdas.push({
                key: `q-${fechaStr}`,
                dia: diaNum,
                fechaStr,
                esMesActual: true,
                esHoy: fechaStr === hoyStr,
                eventos: evsOrdenados,
                tieneEntregaRoja
              });
            }
          }
          return celdas;
        },

        get diasCalendarioMes() {
          const anio = this.calAnio;
          const mes = this.calMes;
          const primerDia = new Date(anio, mes, 1);
          const ultimoDia = new Date(anio, mes + 1, 0);
          // Lunes = 0 ... Domingo = 6
          const offsetInicio = (primerDia.getDay() + 6) % 7;
          const totalDiasMes = ultimoDia.getDate();
          const hoyStr = new Date().toISOString().slice(0, 10);

          const mapaEventos = {};
          this.eventosCalendarioFiltrados.forEach(ev => {
            const f = ev.fecha_dia || (ev.fecha_iso || '').slice(0, 10);
            if (!f) return;
            if (!mapaEventos[f]) mapaEventos[f] = [];
            mapaEventos[f].push(ev);
          });

          const celdas = [];
          const diasMesAnterior = new Date(anio, mes, 0).getDate();
          for (let i = offsetInicio - 1; i >= 0; i--) {
            celdas.push({
              key: `prev-${i}`,
              dia: diasMesAnterior - i,
              fechaStr: '',
              esMesActual: false,
              esHoy: false,
              eventos: [],
              tieneEntregaRoja: false
            });
          }

          for (let d = 1; d <= totalDiasMes; d++) {
            const fechaStr = `${anio}-${String(mes + 1).padStart(2, '0')}-${String(d).padStart(2, '0')}`;
            const evsOrdenados = this.ordenarEventosDia(mapaEventos[fechaStr] || []);
            const tieneEntregaRoja = evsOrdenados.some(e => e.tipo === 'tarea' && !e.entregada);
            celdas.push({
              key: fechaStr,
              dia: d,
              fechaStr,
              esMesActual: true,
              esHoy: fechaStr === hoyStr,
              eventos: evsOrdenados,
              tieneEntregaRoja
            });
          }

          const resto = (7 - (celdas.length % 7)) % 7;
          for (let r = 1; r <= resto; r++) {
            celdas.push({
              key: `next-${r}`,
              dia: r,
              fechaStr: '',
              esMesActual: false,
              esHoy: false,
              eventos: [],
              tieneEntregaRoja: false
            });
          }
          return celdas;
        },

        // Eventos filtrados exclusivamente para la asignatura abierta actualmente
        eventosDeAsignaturaActual() {
          if (!this.asigSeleccionada?.nombre) return [];
          const nombreAsig = this.asigSeleccionada.nombre.toLowerCase().trim();
          const siglasAsig = (this.abreviarAsignatura(this.asigSeleccionada.nombre) || '').toLowerCase().trim();
          const clean = (s) => (s || '').toLowerCase().replace(/[^a-z0-9áéíóúüñ]/gi, '');
          const cleanAsig = clean(nombreAsig);
          return (this.agendaEventos || []).filter(ev => {
            const evAsig = (ev.asignatura || '').toLowerCase().trim();
            if (!evAsig) return false;
            if (evAsig === nombreAsig) return true;
            if (siglasAsig && (evAsig === siglasAsig || evAsig.includes(`[${siglasAsig}]`) || evAsig.includes(`(${siglasAsig})`))) return true;
            if (evAsig.includes(nombreAsig) || nombreAsig.includes(evAsig)) return true;
            const cleanEv = clean(evAsig);
            if (cleanEv && cleanAsig && (cleanEv.includes(cleanAsig) || cleanAsig.includes(cleanEv))) return true;
            return false;
          });
        },

        // Mini-calendario de la asignatura (Lun-Vie 2 semanas)
        get diasCalendarioAsignaturaActual() {
          const hoy = new Date();
          const hoyStr = hoy.toISOString().slice(0, 10);
          const diaSem = (hoy.getDay() + 6) % 7;
          const lunesBase = new Date(hoy);
          lunesBase.setDate(hoy.getDate() - diaSem + (this.calOffsetSemanas * 7));

          const mapaEventos = {};
          this.eventosDeAsignaturaActual().forEach(ev => {
            const f = ev.fecha_dia || (ev.fecha_iso || '').slice(0, 10);
            if (!f) return;
            if (!mapaEventos[f]) mapaEventos[f] = [];
            mapaEventos[f].push(ev);
          });

          const celdas = [];
          for (let sem = 0; sem < 2; sem++) {
            for (let d = 0; d < 5; d++) {
              const fecha = new Date(lunesBase);
              fecha.setDate(lunesBase.getDate() + (sem * 7) + d);
              const fechaStr = `${fecha.getFullYear()}-${String(fecha.getMonth() + 1).padStart(2, '0')}-${String(fecha.getDate()).padStart(2, '0')}`;
              let evsDia = [...(mapaEventos[fechaStr] || [])];
              if (d === 4) {
                for (let extra = 1; extra <= 2; extra++) {
                  const fFin = new Date(fecha);
                  fFin.setDate(fecha.getDate() + extra);
                  const fFinStr = `${fFin.getFullYear()}-${String(fFin.getMonth() + 1).padStart(2, '0')}-${String(fFin.getDate()).padStart(2, '0')}`;
                  if (mapaEventos[fFinStr]) evsDia.push(...mapaEventos[fFinStr]);
                }
              }
              celdas.push({
                key: `asig-${fechaStr}`,
                dia: fecha.getDate(),
                fechaStr,
                esHoy: fechaStr === hoyStr,
                eventos: this.ordenarEventosDia(evsDia)
              });
            }
          }
          return celdas;
        },

        esItemTest(ev) {
          if (!ev) return false;
          if (ev.tipo === 'test_campus' || ev.categoria === 'Test') return true;
          const tit = (ev.titulo || '').toLowerCase().trim();
          return /^(?:test|cuestionario|autoevaluaci[oó]n)\\b/i.test(tit) || /\\btest\\s*tema\\b/i.test(tit);
        },

        get actividadesPurasAsignaturaActual() {
          return this.eventosDeAsignaturaActual()
            .filter(ev => (ev.tipo === 'tarea' || ['Actividad', 'Entrega', 'Práctica', 'Proyecto', 'FFE'].includes(ev.categoria)) && !this.esItemTest(ev))
            .sort((a, b) => {
              if (a.fecha_dia && b.fecha_dia) return a.fecha_dia.localeCompare(b.fecha_dia);
              if (a.fecha_dia && !b.fecha_dia) return -1;
              if (!a.fecha_dia && b.fecha_dia) return 1;
              return (a.titulo || '').localeCompare(b.titulo || '');
            });
        },

        get testsPurosAsignaturaActual() {
          return this.eventosDeAsignaturaActual()
            .filter(ev => this.esItemTest(ev) || (['Actividad', 'Entrega', 'Práctica', 'Proyecto', 'FFE'].includes(ev.categoria) && this.esItemTest(ev)))
            .sort((a, b) => {
              const numA = parseInt(((a.titulo || '').match(/\\d+/) || [999])[0], 10);
              const numB = parseInt(((b.titulo || '').match(/\\d+/) || [999])[0], 10);
              if (numA !== numB) return numA - numB;
              return (a.fecha_dia || '').localeCompare(b.fecha_dia || '');
            });
        },

        get actividadesAsignaturaActual() {
          // Primero las actividades/PACs y luego los tests
          return [...this.actividadesPurasAsignaturaActual, ...this.testsPurosAsignaturaActual];
        },

        get temasAsignaturaActual() {
          return this.eventosDeAsignaturaActual()
            .filter(ev => (ev.tipo === 'tema' || ev.tipo === 'tema_estudio' || ev.categoria === 'Tema') && !this.esItemTest(ev))
            .sort((a, b) => {
              const numA = parseInt((a.titulo.match(/\\d+/) || [999])[0], 10);
              const numB = parseInt((b.titulo.match(/\\d+/) || [999])[0], 10);
              if (numA !== numB) return numA - numB;
              return (a.titulo || '').localeCompare(b.titulo || '');
            });
        },

        // Colores exactos de las tarjetas del calendario de Notion (Verde=Vista/Hecho, Morado=Cita/Recordatorio, Rojo=Actividad/Entrega/Práctica/Examen pendiente, Azul=Clase/Tutoría pendiente, Ámbar=Tema/Test)
        claseColorTarjetaNotion(ev) {
          const est = ev?.estado || '';
          if (['Vista', 'Hecho'].includes(est) || ev?.entregada) {
            return 'bg-emerald-950/60 border-emerald-500/40 text-emerald-200 hover:bg-emerald-900/70';
          }
          if (est === 'Vista no relevante') {
            return 'bg-gray-800/70 border-gray-600/40 text-gray-400 hover:bg-gray-800';
          }
          if (['Cita', 'Recordatorio', 'Otro'].includes(ev?.categoria)) {
            return 'bg-purple-950/75 border-purple-500/60 text-purple-100 hover:bg-purple-900/80 shadow-sm shadow-purple-950/50';
          }
          if (ev?.tipo === 'tarea' || ['Actividad', 'Entrega', 'Práctica', 'Examen', 'Proyecto', 'FFE'].includes(ev?.categoria)) {
            return 'bg-rose-950/75 border-rose-500/60 text-rose-100 hover:bg-rose-900/80 shadow-sm shadow-rose-950/50';
          }
          if (ev?.tipo === 'tema' || ev?.tipo === 'tema_estudio' || ['Tema', 'Test'].includes(ev?.categoria)) {
            return 'bg-amber-950/60 border-amber-500/45 text-amber-100 hover:bg-amber-900/70';
          }
          return 'bg-surface-7/70 border-blue-500/40 text-blue-100 hover:bg-blue-900/75';
        },

        claseBadgeEstadoNotion(estado) {
          const mapa = {
            'Vista': 'bg-emerald-500/20 text-emerald-300 border border-emerald-500/40',
            'Hecho': 'bg-emerald-500/20 text-emerald-300 border border-emerald-500/40',
            'Vista no relevante': 'bg-amber-500/20 text-amber-300 border border-amber-500/30',
            'No vista': 'bg-blue-500/20 text-blue-300 border border-blue-500/40',
            'No entregado': 'bg-rose-500/25 text-rose-300 border border-rose-500/50',
            'No hecho': 'bg-gray-800 text-gray-400 border border-gray-700',
            'Pendiente de empezar': 'bg-amber-500/20 text-amber-300 border border-amber-500/40',
            'En proceso': 'bg-cyan-500/20 text-cyan-300 border border-cyan-500/40',
            'Entrenamientos': 'bg-purple-500/20 text-purple-300 border border-purple-500/40',
          };
          return mapa[estado] || 'bg-gray-800 text-gray-300 border border-gray-700';
        },

        iconoAsignatura(nombre) {
          const n = (nombre || '').toLowerCase();
          if (n.includes('personal') || n.includes('general') || n.includes('cita') || n.includes('recordatorio')) return '📌';
          if (n.includes('acceso a datos')) return '🗄️';
          if (n.includes('interfaces')) return '🖥️';
          if (n.includes('servicios y procesos')) return '⚙️';
          if (n.includes('multimedia') || n.includes('móviles')) return '📱';
          if (n.includes('gestión empresarial')) return '🏢';
          if (n.includes('proyecto')) return '🚀';
          if (n.includes('nube')) return '☁️';
          if (n.includes('digitalización')) return '🌐';
          if (n.includes('sostenibilidad')) return '🌱';
          if (n.includes('empresa') || n.includes('ffe')) return '💼';
          if (n.includes('inglés')) return '🇬🇧';
          return '📘';
        },

        temaVisualAsignatura(nombre) {
          const n = (nombre || '').toLowerCase();
          if (n.includes('acceso a datos')) {
            return {
              siglas: 'AD',
              icono: 'fa-solid fa-database',
              subtitulo: 'Ficheros XML/JSON, JDBC, ORM Hibernate y Bases de Datos NoSQL',
              imagen: 'https://images.unsplash.com/photo-1558494949-ef010cbdcc31?auto=format&fit=crop&w=700&q=80',
              gradiente: 'bg-gradient-to-br from-cyan-600/60 via-blue-900/60 to-slate-950',
              badgeBg: 'bg-gradient-to-br from-cyan-500 to-blue-600 shadow-cyan-500/30',
              chipClase: 'bg-cyan-500/20 text-cyan-200 border-cyan-400/40'
            };
          }
          if (n.includes('interfaces')) {
            return {
              siglas: 'DI',
              icono: 'fa-solid fa-layer-group',
              subtitulo: 'Diseño UI/UX, Componentes Visuales, Eventos, Informes y Usabilidad',
              imagen: 'https://images.unsplash.com/photo-1507238691740-187a5b1d37b8?auto=format&fit=crop&w=700&q=80',
              gradiente: 'bg-gradient-to-br from-fuchsia-600/60 via-purple-900/60 to-slate-950',
              badgeBg: 'bg-gradient-to-br from-fuchsia-500 to-purple-600 shadow-fuchsia-500/30',
              chipClase: 'bg-fuchsia-500/20 text-fuchsia-200 border-fuchsia-400/40'
            };
          }
          if (n.includes('multimedia') || n.includes('móviles')) {
            return {
              siglas: 'PMDM',
              icono: 'fa-solid fa-mobile-screen-button',
              subtitulo: 'Desarrollo Android & Kotlin, Jetpack, Multimedia y Motores 2D/3D',
              imagen: 'https://images.unsplash.com/photo-1512941937669-90a1b58e7e9c?auto=format&fit=crop&w=700&q=80',
              gradiente: 'bg-gradient-to-br from-emerald-600/60 via-teal-900/60 to-slate-950',
              badgeBg: 'bg-gradient-to-br from-emerald-500 to-teal-600 shadow-emerald-500/30',
              chipClase: 'bg-emerald-500/20 text-emerald-200 border-emerald-400/40'
            };
          }
          if (n.includes('servicios y procesos')) {
            return {
              siglas: 'PSP',
              icono: 'fa-solid fa-microchip',
              subtitulo: 'Programación Multihilo, Concurrencia, Sockets TCP/UDP y Criptografía',
              imagen: 'https://images.unsplash.com/photo-1518770660439-4636190af475?auto=format&fit=crop&w=700&q=80',
              gradiente: 'bg-gradient-to-br from-amber-600/60 via-orange-900/60 to-slate-950',
              badgeBg: 'bg-gradient-to-br from-amber-500 to-orange-600 shadow-amber-500/30',
              chipClase: 'bg-amber-500/20 text-amber-200 border-amber-400/40'
            };
          }
          if (n.includes('gestión empresarial')) {
            return {
              siglas: 'SGE',
              icono: 'fa-solid fa-chart-pie',
              subtitulo: 'Sistemas ERP/CRM, Odoo, Módulos Empresariales e Integración BI',
              imagen: 'https://images.unsplash.com/photo-1551288049-bebda4e38f71?auto=format&fit=crop&w=700&q=80',
              gradiente: 'bg-gradient-to-br from-rose-600/60 via-pink-900/60 to-slate-950',
              badgeBg: 'bg-gradient-to-br from-rose-500 to-pink-600 shadow-rose-500/30',
              chipClase: 'bg-rose-500/20 text-rose-200 border-rose-400/40'
            };
          }
          if (n.includes('nube')) {
            return {
              siglas: 'NUBE',
              icono: 'fa-solid fa-cloud-bolt',
              subtitulo: 'Infraestructura Cloud, AWS/Azure, Contenedores Docker y Serverless',
              imagen: 'https://images.unsplash.com/photo-1451187580459-43490279c0fa?auto=format&fit=crop&w=700&q=80',
              gradiente: 'bg-gradient-to-br from-sky-600/60 via-indigo-900/60 to-slate-950',
              badgeBg: 'bg-gradient-to-br from-sky-500 to-indigo-600 shadow-sky-500/30',
              chipClase: 'bg-sky-500/20 text-sky-200 border-sky-400/40'
            };
          }
          if (n.includes('proyecto')) {
            return {
              siglas: 'PROY',
              icono: 'fa-solid fa-rocket',
              subtitulo: 'Diseño, Arquitectura, Desarrollo Full-Stack y Defensa del Proyecto DAM',
              imagen: 'https://images.unsplash.com/photo-1522071820081-009f0129c71c?auto=format&fit=crop&w=700&q=80',
              gradiente: 'bg-gradient-to-br from-violet-600/60 via-indigo-900/60 to-slate-950',
              badgeBg: 'bg-gradient-to-br from-violet-500 to-indigo-600 shadow-violet-500/30',
              chipClase: 'bg-violet-500/20 text-violet-200 border-violet-400/40'
            };
          }
          if (n.includes('digitalización')) {
            return {
              siglas: 'DIG',
              icono: 'fa-solid fa-globe',
              subtitulo: 'Transformación Digital, Industria 4.0, IoT, IA y Ciberseguridad',
              imagen: 'https://images.unsplash.com/photo-1526374965328-7f61d4dc18c5?auto=format&fit=crop&w=700&q=80',
              gradiente: 'bg-gradient-to-br from-blue-600/60 via-cyan-900/60 to-slate-950',
              badgeBg: 'bg-gradient-to-br from-blue-500 to-cyan-600 shadow-blue-500/30',
              chipClase: 'bg-blue-500/20 text-blue-200 border-blue-400/40'
            };
          }
          if (n.includes('sostenibilidad')) {
            return {
              siglas: 'SOST',
              icono: 'fa-solid fa-seedling',
              subtitulo: 'Economía Circular, ODS, Eficiencia Energética y Tecnología Sostenible',
              imagen: 'https://images.unsplash.com/photo-1473341304170-971dccb5ac1e?auto=format&fit=crop&w=700&q=80',
              gradiente: 'bg-gradient-to-br from-green-600/60 via-emerald-900/60 to-slate-950',
              badgeBg: 'bg-gradient-to-br from-green-500 to-emerald-600 shadow-green-500/30',
              chipClase: 'bg-green-500/20 text-green-200 border-green-400/40'
            };
          }
          if (n.includes('inglés')) {
            return {
              siglas: 'ING',
              icono: 'fa-solid fa-earth-europe',
              subtitulo: 'Technical English for Software Developers, Documentation & Meetings',
              imagen: 'https://images.unsplash.com/photo-1513635269975-59663e0ac1ad?auto=format&fit=crop&w=700&q=80',
              gradiente: 'bg-gradient-to-br from-red-600/50 via-blue-900/60 to-slate-950',
              badgeBg: 'bg-gradient-to-br from-blue-500 to-rose-600 shadow-blue-500/30',
              chipClase: 'bg-blue-500/20 text-blue-200 border-blue-400/40'
            };
          }
          if (n.includes('empleabilidad')) {
            return {
              siglas: 'IPE',
              icono: 'fa-solid fa-user-tie',
              subtitulo: 'Carrera Tecnológica, Emprendimiento, Derecho Laboral y Equipos Ágiles',
              imagen: 'https://images.unsplash.com/photo-1521737604893-d14cc237f11d?auto=format&fit=crop&w=700&q=80',
              gradiente: 'bg-gradient-to-br from-amber-500/50 via-yellow-900/60 to-slate-950',
              badgeBg: 'bg-gradient-to-br from-amber-500 to-yellow-600 shadow-amber-500/30',
              chipClase: 'bg-amber-500/20 text-amber-200 border-amber-400/40'
            };
          }
          if (n.includes('empresa') || n.includes('ffe')) {
            return {
              siglas: 'FFE',
              icono: 'fa-solid fa-building-user',
              subtitulo: 'Prácticas Profesionales en Entorno Real de Desarrollo de Software',
              imagen: 'https://images.unsplash.com/photo-1497366216548-37526070297c?auto=format&fit=crop&w=700&q=80',
              gradiente: 'bg-gradient-to-br from-teal-600/60 via-cyan-900/60 to-slate-950',
              badgeBg: 'bg-gradient-to-br from-teal-500 to-cyan-600 shadow-teal-500/30',
              chipClase: 'bg-teal-500/20 text-teal-200 border-teal-400/40'
            };
          }
          if (n.includes('bases de datos')) {
            return {
              siglas: 'BBDD',
              icono: 'fa-solid fa-server',
              subtitulo: 'Diseño Relacional E-R, SQL Avanzado, Procedimientos y Normalización',
              imagen: 'https://images.unsplash.com/photo-1544383835-bda2bc66a55d?auto=format&fit=crop&w=700&q=80',
              gradiente: 'bg-gradient-to-br from-cyan-600/60 via-indigo-900/60 to-slate-950',
              badgeBg: 'bg-gradient-to-br from-cyan-500 to-indigo-600',
              chipClase: 'bg-cyan-500/20 text-cyan-200 border-cyan-400/40'
            };
          }
          if (n.includes('programación')) {
            return {
              siglas: 'PROG',
              icono: 'fa-solid fa-code',
              subtitulo: 'Algoritmia, Programación Orientada a Objetos en Java y Estructuras',
              imagen: 'https://images.unsplash.com/photo-1555066931-4365d14bab8c?auto=format&fit=crop&w=700&q=80',
              gradiente: 'bg-gradient-to-br from-emerald-600/60 via-cyan-900/60 to-slate-950',
              badgeBg: 'bg-gradient-to-br from-emerald-500 to-cyan-600',
              chipClase: 'bg-emerald-500/20 text-emerald-200 border-emerald-400/40'
            };
          }
          if (n.includes('sistemas informáticos')) {
            return {
              siglas: 'SIS',
              icono: 'fa-solid fa-terminal',
              subtitulo: 'Hardware, Administración Linux/Windows, Redes TCP/IP y Virtualización',
              imagen: 'https://images.unsplash.com/photo-1629654297299-c8506221ca97?auto=format&fit=crop&w=700&q=80',
              gradiente: 'bg-gradient-to-br from-slate-600/60 via-blue-900/60 to-slate-950',
              badgeBg: 'bg-gradient-to-br from-blue-500 to-slate-600',
              chipClase: 'bg-blue-500/20 text-blue-200 border-blue-400/40'
            };
          }
          if (n.includes('lenguajes de marcas')) {
            return {
              siglas: 'LMSGI',
              icono: 'fa-brands fa-html5',
              subtitulo: 'HTML5, CSS3, JavaScript, XML, XPath/XSLT y Sindicación de Contenidos',
              imagen: 'https://images.unsplash.com/photo-1547658719-da2b51169166?auto=format&fit=crop&w=700&q=80',
              gradiente: 'bg-gradient-to-br from-orange-600/60 via-rose-900/60 to-slate-950',
              badgeBg: 'bg-gradient-to-br from-orange-500 to-rose-600',
              chipClase: 'bg-orange-500/20 text-orange-200 border-orange-400/40'
            };
          }
          if (n.includes('entornos de desarrollo')) {
            return {
              siglas: 'ED',
              icono: 'fa-solid fa-code-branch',
              subtitulo: 'Control de Versiones Git, IDEs, Testing Unitario JUnit y Diagramas UML',
              imagen: 'https://images.unsplash.com/photo-1618401471353-b98afee0b2eb?auto=format&fit=crop&w=700&q=80',
              gradiente: 'bg-gradient-to-br from-purple-600/60 via-indigo-900/60 to-slate-950',
              badgeBg: 'bg-gradient-to-br from-purple-500 to-indigo-600',
              chipClase: 'bg-purple-500/20 text-purple-200 border-purple-400/40'
            };
          }
          return {
            siglas: 'MOD',
            icono: 'fa-solid fa-book-bookmark',
            subtitulo: 'Temario oficial, videoclases transcritas y simulacros interactivos',
            imagen: 'https://images.unsplash.com/photo-1517694712202-14dd9538aa97?auto=format&fit=crop&w=700&q=80',
            gradiente: 'bg-gradient-to-br from-cyan-600/50 via-indigo-900/60 to-slate-950',
            badgeBg: 'bg-gradient-to-br from-cyan-500 to-indigo-600',
            chipClase: 'bg-cyan-500/20 text-cyan-200 border-cyan-400/40'
          };
        },

        opcionesEstadoNuevoModal(cat) {
          if (['Clase', 'Tutoría'].includes(cat)) {
            return [
              { valor: 'No vista', titulo: 'Pendiente de ver', desc: 'En azul en tu calendario', dot: 'bg-blue-400', claseActiva: 'bg-blue-950/80 border-blue-400 text-blue-100' },
              { valor: 'Vista', titulo: 'Clase vista', desc: 'Completada en verde', dot: 'bg-emerald-400', claseActiva: 'bg-emerald-950/80 border-emerald-400 text-emerald-100' },
              { valor: 'Vista no relevante', titulo: 'No relevante', desc: 'Omitida / Archivada', dot: 'bg-amber-400', claseActiva: 'bg-gray-800 border-amber-400/60 text-amber-200' }
            ];
          }
          if (['Actividad', 'Entrega', 'Práctica', 'Examen', 'Proyecto', 'FFE'].includes(cat)) {
            return [
              { valor: 'No entregado', titulo: 'Pendiente entrega', desc: 'Alerta roja en calendario', dot: 'bg-rose-500', claseActiva: 'bg-rose-950/80 border-rose-400 text-rose-100' },
              { valor: 'En proceso', titulo: 'En proceso', desc: 'Fijada en To-do hoy', dot: 'bg-cyan-400', claseActiva: 'bg-cyan-950/80 border-cyan-400 text-cyan-100' },
              { valor: 'Hecho', titulo: 'Entregada / Hecho', desc: 'Completada en verde', dot: 'bg-emerald-400', claseActiva: 'bg-emerald-950/80 border-emerald-400 text-emerald-100' }
            ];
          }
          if (['Cita', 'Recordatorio', 'Otro'].includes(cat)) {
            return [
              { valor: 'Pendiente de empezar', titulo: 'Agendado + Aviso', desc: 'Sale en Hoy y Calendario', dot: 'bg-purple-400', claseActiva: 'bg-purple-950/80 border-purple-400 text-purple-100' },
              { valor: 'No hecho', titulo: 'En su fecha', desc: 'Solo en el día elegido', dot: 'bg-amber-400', claseActiva: 'bg-amber-950/70 border-amber-400 text-amber-100' },
              { valor: 'En proceso', titulo: 'En curso', desc: 'Activo ahora mismo', dot: 'bg-cyan-400', claseActiva: 'bg-cyan-950/80 border-cyan-400 text-cyan-100' },
              { valor: 'Hecho', titulo: 'Realizado', desc: 'Completado en verde', dot: 'bg-emerald-400', claseActiva: 'bg-emerald-950/80 border-emerald-400 text-emerald-100' }
            ];
          }
          return [
            { valor: 'No hecho', titulo: 'Programado', desc: 'En su día del calendario', dot: 'bg-amber-400', claseActiva: 'bg-amber-950/70 border-amber-400 text-amber-100' },
            { valor: 'Pendiente de empezar', titulo: 'Para hoy', desc: 'Fijado en To-do hoy', dot: 'bg-sky-400', claseActiva: 'bg-sky-950/80 border-sky-400 text-sky-100' },
            { valor: 'En proceso', titulo: 'En proceso', desc: 'Estudiando ahora', dot: 'bg-cyan-400', claseActiva: 'bg-cyan-950/80 border-cyan-400 text-cyan-100' },
            { valor: 'Hecho', titulo: 'Completado', desc: 'Marcado en verde', dot: 'bg-emerald-400', claseActiva: 'bg-emerald-950/80 border-emerald-400 text-emerald-100' }
          ];
        },

        opcionesEstadoParaItem(ev) {
          if (!ev) return ['No hecho', 'En proceso', 'Hecho'];
          const cat = ev.categoria || '';
          if (['Clase', 'Tutoría'].includes(cat) || ev.tipo === 'clase_directo') {
            return ['No vista', 'Vista', 'Vista no relevante'];
          }
          if (['Actividad', 'Entrega', 'Práctica', 'Examen', 'Proyecto', 'FFE'].includes(cat) || ev.tipo === 'tarea') {
            return ['No entregado', 'En proceso', 'Hecho'];
          }
          return ['No hecho', 'Pendiente de empezar', 'En proceso', 'Entrenamientos', 'Hecho'];
        },

        abrirModalItemAgenda(ev) {
          if (!ev) return;
          this.itemAgendaActivo = ev;
          this.modoAsignaturaPersonalizada = false;
          this.itemEditTitulo = ev.titulo || '';
          this.itemEditAsignatura = ev.asignatura || 'Acceso a Datos';
          this.itemEditCategoria = ev.categoria || (ev.tipo === 'clase_directo' ? 'Clase' : (ev.tipo === 'tarea' ? 'Actividad' : 'Tema'));
          this.itemEditEstado = ev.estado || (ev.tipo === 'clase_directo' ? 'No vista' : (ev.tipo === 'tarea' ? 'No entregado' : 'No hecho'));
          this.itemEditFechaDia = ev.fecha_dia || (ev.fecha_iso || '').slice(0, 10);
          this.itemEditHora = (ev.hora_inicio && ev.hora_inicio !== '23:59') ? ev.hora_inicio : '';
          const nVal = ev.nota_personal !== null && ev.nota_personal !== undefined ? ev.nota_personal : ev.nota;
          this.itemEditNota = nVal !== null && nVal !== undefined ? String(nVal) : '';
          this.itemEditComentario = ev.comentario || '';
          this.modalItemAgenda = true;
        },

        desplazarFechaModal(diasDelta) {
          const baseStr = this.itemEditFechaDia || new Date().toISOString().slice(0, 10);
          const partes = baseStr.split('-');
          const fecha = partes.length === 3
            ? new Date(parseInt(partes[0], 10), parseInt(partes[1], 10) - 1, parseInt(partes[2], 10))
            : new Date();
          fecha.setDate(fecha.getDate() + diasDelta);
          this.itemEditFechaDia = `${fecha.getFullYear()}-${String(fecha.getMonth() + 1).padStart(2, '0')}-${String(fecha.getDate()).padStart(2, '0')}`;
        },

        iniciarArrastreItem(event, ev) {
          this.itemArrastrando = ev;
          if (event?.dataTransfer) {
            event.dataTransfer.effectAllowed = 'move';
            event.dataTransfer.setData('text/plain', ev.id);
            try {
              event.dataTransfer.dropEffect = 'move';
            } catch (err) {}
          }
        },

        finalizarArrastreItem() {
          this.itemArrastrando = null;
          this.diaArrastreSobre = '';
        },

        async soltarItemEnDia(fechaDestinoStr) {
          const ev = this.itemArrastrando;
          this.diaArrastreSobre = '';
          this.itemArrastrando = null;
          if (!ev || !fechaDestinoStr) return;
          const fechaAnterior = ev.fecha_dia;
          if (fechaAnterior === fechaDestinoStr) return;

          // 1. Actualización optimista ultra fluida en memoria (0ms de retraso visual)
          ev.fecha_dia = fechaDestinoStr;
          if (ev.fecha_iso) {
            const h = (ev.hora_inicio || '23:59').slice(0, 5);
            ev.fecha_iso = `${fechaDestinoStr}T${h}:00Z`;
          }

          if (ev.esTemaUnificado) {
            try {
              await fetch('/api/asignatura/tema/estado', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                  asignatura: ev.asignatura || this.asigSeleccionada?.nombre,
                  tema_num: ev.numero,
                  tema_titulo: ev.titulo,
                  estado: ev.estado || 'Por hacer',
                  fecha_dia: fechaDestinoStr
                })
              });
            } catch(e) {
              ev.fecha_dia = fechaAnterior;
            }
            return;
          }

          try {
            await fetch('/api/agenda/item/actualizar', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({
                item_id: ev.id,
                fecha_dia: fechaDestinoStr
              })
            });
          } catch (e) {
            ev.fecha_dia = fechaAnterior;
          }
        },

        async moverItemDias(ev, diasDelta) {
          if (!ev) return;
          const baseStr = ev.fecha_dia || (ev.fecha_iso || '').slice(0, 10) || new Date().toISOString().slice(0, 10);
          const partes = baseStr.split('-');
          const fecha = partes.length === 3
            ? new Date(parseInt(partes[0], 10), parseInt(partes[1], 10) - 1, parseInt(partes[2], 10))
            : new Date();
          fecha.setDate(fecha.getDate() + diasDelta);
          const nuevaFecha = `${fecha.getFullYear()}-${String(fecha.getMonth() + 1).padStart(2, '0')}-${String(fecha.getDate()).padStart(2, '0')}`;
          
          // Actualización optimista instantánea sin recarga de red congelante
          const fechaAnterior = ev.fecha_dia;
          ev.fecha_dia = nuevaFecha;
          if (ev.fecha_iso) {
            const h = (ev.hora_inicio || '23:59').slice(0, 5);
            ev.fecha_iso = `${nuevaFecha}T${h}:00Z`;
          }

          try {
            await fetch('/api/agenda/item/actualizar', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({
                item_id: ev.id,
                fecha_dia: nuevaFecha
              })
            });
          } catch (e) {
            ev.fecha_dia = fechaAnterior;
          }
        },

        async guardarCambiosItemAgenda() {
          if (!this.itemAgendaActivo) return;
          const payload = {
            item_id: this.itemAgendaActivo.id,
            titulo: this.itemEditTitulo ? this.itemEditTitulo.trim() : null,
            asignatura: this.itemEditAsignatura ? this.itemEditAsignatura.trim() : null,
            categoria: this.itemEditCategoria || null,
            estado: this.itemEditEstado,
            fecha_dia: this.itemEditFechaDia || null,
            hora_inicio: this.itemEditHora ? this.itemEditHora.trim() : null,
            nota_personal: this.itemEditNota !== '' ? parseFloat(this.itemEditNota) : null,
            comentario: this.itemEditComentario
          };
          try {
            const res = await fetch('/api/agenda/item/actualizar', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify(payload)
            });
            if (res.ok) {
              this.modalItemAgenda = false;
              await this.cargarAgenda();
            }
          } catch (e) {
            console.error(e);
          }
        },

        async marcarItemRapidoHecho(ev) {
          if (!ev) return;
          const nuevoEstado = (['Clase', 'Tutoría'].includes(ev.categoria) || ev.tipo === 'clase_directo') ? 'Vista' : 'Hecho';
          await this.cambiarEstadoItemDirecto(ev, nuevoEstado);
        },

        async cambiarEstadoItemDirecto(ev, nuevoEstado) {
          if (!ev) return;
          ev.estado = nuevoEstado;
          ev.entregada = ['Vista', 'Vista no relevante', 'Hecho'].includes(nuevoEstado);
          try {
            await fetch('/api/agenda/item/actualizar', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({
                item_id: ev.id,
                estado: nuevoEstado
              })
            });
            await this.cargarAgenda();
          } catch (e) {
            console.error(e);
          }
        },

        abrirModalNuevoItemAgenda(categoriaPorDefecto = 'Tema', asignaturaPorDefecto = '', fechaPorDefecto = '', destino = 'calendario') {
          this.nuevoItemTitulo = '';
          this.modoNuevaAsignaturaPersonalizada = false;
          this.nuevoItemDestino = destino || 'calendario';
          this.nuevoItemCategoria = categoriaPorDefecto || 'Tema';
          const esCita = ['Cita', 'Otro'].includes(this.nuevoItemCategoria);
          this.nuevoItemHora = esCita ? '17:00' : (['Clase', 'Tutoría'].includes(this.nuevoItemCategoria) ? '19:00' : '');

          if (asignaturaPorDefecto) {
            this.nuevoItemAsignatura = asignaturaPorDefecto;
          } else if (this.asigSeleccionada?.nombre) {
            this.nuevoItemAsignatura = this.asigSeleccionada.nombre;
          } else if (esCita) {
            this.nuevoItemAsignatura = 'Personal / General';
          } else {
            this.nuevoItemAsignatura = (this.asigSeleccionada?.nombre) || (this.listaAsignaturasCompleta[0] || 'Acceso a Datos');
          }

          if (fechaPorDefecto) {
            this.nuevoItemFecha = fechaPorDefecto;
          } else if (this.asigSeleccionada && this.diasCalendarioAsignaturaActual.length > 0) {
            const hoyStr = new Date().toISOString().slice(0, 10);
            const celdaHoy = this.diasCalendarioAsignaturaActual.find(c => c.fechaStr === hoyStr);
            this.nuevoItemFecha = celdaHoy ? hoyStr : (this.diasCalendarioAsignaturaActual[0].fechaStr || hoyStr);
          } else if (destino === 'calendario') {
            if (this.calModoVista === 'quincena' && this.calOffsetSemanas !== 0 && this.celdasCalendarioVisibles.length > 0) {
              this.nuevoItemFecha = this.celdasCalendarioVisibles[0].fechaStr || new Date().toISOString().slice(0, 10);
            } else if (this.calModoVista === 'mes') {
              const hoy = new Date();
              if (this.calAnio === hoy.getFullYear() && this.calMes === hoy.getMonth()) {
                this.nuevoItemFecha = hoy.toISOString().slice(0, 10);
              } else {
                this.nuevoItemFecha = `${this.calAnio}-${String(this.calMes + 1).padStart(2, '0')}-01`;
              }
            } else {
              this.nuevoItemFecha = new Date().toISOString().slice(0, 10);
            }
          } else {
            this.nuevoItemFecha = new Date().toISOString().slice(0, 10);
          }
          this.actualizarEstadoSegunCategoriaNuevo();
          this.modalNuevoItemAgenda = true;
        },

        cambiarDestinoNuevoItem(destino) {
          this.nuevoItemDestino = destino;
          if (destino === 'hoy') {
            this.nuevoItemFecha = new Date().toISOString().slice(0, 10);
          }
          this.actualizarEstadoSegunCategoriaNuevo();
        },

        actualizarEstadoSegunCategoriaNuevo() {
          const cat = this.nuevoItemCategoria;
          if (['Cita', 'Otro'].includes(cat)) {
            if (!this.nuevoItemHora) this.nuevoItemHora = '17:00';
            if (!this.asigSeleccionada && (!this.nuevoItemAsignatura || this.nuevoItemAsignatura === 'Acceso a Datos')) {
              this.nuevoItemAsignatura = 'Personal / General';
            }
            this.nuevoItemEstado = 'Pendiente de empezar';
          } else if (['Clase', 'Tutoría'].includes(cat)) {
            this.nuevoItemEstado = 'No vista';
            if (!this.nuevoItemHora) this.nuevoItemHora = '19:00';
            if (this.asigSeleccionada?.nombre && this.nuevoItemAsignatura === 'Personal / General') {
              this.nuevoItemAsignatura = this.asigSeleccionada.nombre;
            }
          } else if (['Actividad', 'Entrega', 'Práctica', 'Examen', 'Proyecto', 'FFE'].includes(cat)) {
            this.nuevoItemEstado = this.nuevoItemDestino === 'hoy' ? 'En proceso' : 'No entregado';
            if (this.asigSeleccionada?.nombre && this.nuevoItemAsignatura === 'Personal / General') {
              this.nuevoItemAsignatura = this.asigSeleccionada.nombre;
            }
          } else {
            this.nuevoItemEstado = this.nuevoItemDestino === 'hoy' ? 'Pendiente de empezar' : 'No hecho';
            if (this.asigSeleccionada?.nombre && this.nuevoItemAsignatura === 'Personal / General') {
              this.nuevoItemAsignatura = this.asigSeleccionada.nombre;
            }
          }
        },


        async crearNuevoItemAgenda() {
          let titulo = (this.nuevoItemTitulo || '').trim();
          const cat = this.nuevoItemCategoria || 'Tema';
          const asig = this.nuevoItemAsignatura || (this.asigSeleccionada?.nombre || 'General');
          const hora = this.nuevoItemHora ? ` ${this.nuevoItemHora}` : '';
          if (!titulo) {
            if (cat === 'Clase') {
              titulo = `Clase${hora}`;
            } else if (cat === 'Tutoría') {
              titulo = `Tutoría${hora}`;
            } else if (cat === 'Tema') {
              titulo = `Estudio ${asig}`;
            } else {
              titulo = `${cat}${hora ? ' a las' + hora : ''}`;
            }
            this.nuevoItemTitulo = titulo;
          }
          const fechaCreada = this.nuevoItemFecha;
          try {
            const res = await fetch('/api/agenda/item/crear', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({
                titulo: titulo,
                asignatura: asig,
                categoria: cat,
                estado: this.nuevoItemEstado,
                fecha_dia: fechaCreada,
                hora_inicio: this.nuevoItemHora || null
              })
            });
            if (res.ok) {
              const resJson = await res.json().catch(() => ({}));
              if (resJson && resJson.item) {
                this.agendaEventos = [resJson.item, ...(this.agendaEventos || []).filter(e => e.id !== resJson.item.id)];
              }
              this.modalNuevoItemAgenda = false;
              if (fechaCreada) {
                this.irADiaDeEvento({ fecha_dia: fechaCreada });
              }
              await this.cargarAgenda();
            }
          } catch (e) {
            console.error(e);
          }
        },

        async eliminarItemAgendaPropio(itemId) {
          if (!itemId) return;
          try {
            this.agendaEventos = (this.agendaEventos || []).filter(e => e.id !== itemId);
            this.modalItemAgenda = false;
            const res = await fetch(`/api/agenda/item/${encodeURIComponent(itemId)}`, { method: 'DELETE' });
            if (res.ok) {
              await this.cargarAgenda();
            }
          } catch (e) {
            console.error(e);
          }
        },


        irAAsignaturaDesdeModal(nombreAsig) {
          if (!nombreAsig) return;
          this.modalItemAgenda = false;
          this.abrirAsignaturaPorNombre(nombreAsig);
        },

        navegarCalendario(delta) {
          if (this.calModoVista === 'quincena') {
            this.calOffsetSemanas += delta;
          } else {
            this.cambiarMesCalendario(delta);
          }
        },

        cambiarMesCalendario(delta) {
          let nuevoMes = this.calMes + delta;
          let nuevoAnio = this.calAnio;
          if (nuevoMes < 0) {
            nuevoMes = 11;
            nuevoAnio--;
          } else if (nuevoMes > 11) {
            nuevoMes = 0;
            nuevoAnio++;
          }
          this.calMes = nuevoMes;
          this.calAnio = nuevoAnio;
        },

        irAHoyCalendario() {
          const hoy = new Date();
          this.calAnio = hoy.getFullYear();
          this.calMes = hoy.getMonth();
          this.calOffsetSemanas = 0;
          this.calDiaSeleccionado = hoy.toISOString().slice(0, 10);
        },

        seleccionarDiaCalendario(fechaStr) {
          if (!fechaStr) return;
          this.calDiaSeleccionado = fechaStr;
        },

        irADiaDeEvento(ev) {
          const f = ev?.fecha_dia || (ev?.fecha_iso || '').slice(0, 10);
          if (!f) return;
          const partes = f.split('-');
          if (partes.length === 3) {
            const anio = parseInt(partes[0], 10);
            const mes = parseInt(partes[1], 10) - 1;
            const dia = parseInt(partes[2], 10);
            this.calAnio = anio;
            this.calMes = mes;
            this.calDiaSeleccionado = f;

            const hoy = new Date();
            hoy.setHours(0, 0, 0, 0);
            const diaSemHoy = (hoy.getDay() + 6) % 7;
            const lunesActual = new Date(hoy);
            lunesActual.setDate(hoy.getDate() - diaSemHoy);

            const fechaObjetivo = new Date(anio, mes, dia);
            fechaObjetivo.setHours(0, 0, 0, 0);
            const diaSemObj = (fechaObjetivo.getDay() + 6) % 7;
            const lunesObjetivo = new Date(fechaObjetivo);
            lunesObjetivo.setDate(fechaObjetivo.getDate() - diaSemObj);

            const utc1 = Date.UTC(lunesActual.getFullYear(), lunesActual.getMonth(), lunesActual.getDate());
            const utc2 = Date.UTC(lunesObjetivo.getFullYear(), lunesObjetivo.getMonth(), lunesObjetivo.getDate());
            const diffSemanas = Math.round((utc2 - utc1) / (7 * 24 * 60 * 60 * 1000));
            if (diffSemanas < this.calOffsetSemanas || diffSemanas > this.calOffsetSemanas + 1) {
              this.calOffsetSemanas = diffSemanas;
            }
          }
        },

        formatearFechaSeleccionada(fechaStr) {
          if (!fechaStr) return 'Selecciona un día';
          const partes = fechaStr.split('-');
          if (partes.length !== 3) return this.formatearFechaDDMMAAAA(fechaStr);
          const fecha = new Date(parseInt(partes[0], 10), parseInt(partes[1], 10) - 1, parseInt(partes[2], 10));
          const diasSemana = ['Domingo', 'Lunes', 'Martes', 'Miércoles', 'Jueves', 'Viernes', 'Sábado'];
          const dia = String(parseInt(partes[2], 10)).padStart(2, '0');
          const mes = String(parseInt(partes[1], 10)).padStart(2, '0');
          const anio = partes[0];
          return `${diasSemana[fecha.getDay()]}, ${dia}/${mes}/${anio}`;
        },

        nombreDiaDeFecha(fechaStr) {
          if (!fechaStr) return '';
          const partes = fechaStr.split('-');
          if (partes.length !== 3) return '';
          const fecha = new Date(parseInt(partes[0], 10), parseInt(partes[1], 10) - 1, parseInt(partes[2], 10));
          const diasSemana = ['Dom', 'Lun', 'Mar', 'Mié', 'Jue', 'Vie', 'Sáb'];
          return diasSemana[fecha.getDay()] || '';
        },

        abreviarAsignatura(nombre) {
          if (!nombre) return '';
          const mapa = {
            'Acceso a Datos': 'AD',
            'Desarrollo de Interfaces': 'DI',
            'Programación de Servicios y Procesos': 'PSP',
            'Programación Multimedia y Dispositivos Móviles': 'PMDM',
            'Sistemas de Gestión Empresarial': 'SGE',
            'Proyecto Intermodular DAM': 'Proyecto',
            'Proyecto Intermodular': 'Proyecto',
            'Arquitecturas en la Nube': 'Nube',
            'Arquitectura en la Nube': 'Nube',
            'Digitalización Aplicada a los Sectores Productivos': 'Digitalización',
            'Sostenibilidad Aplicada al Sistema Productivo': 'Sostenibilidad',
            'Fase de Formación en Empresa': 'FFE',
            'Inglés Profesional': 'Inglés'
          };
          return mapa[nombre] || (nombre.length > 12 ? nombre.slice(0, 10) + '…' : nombre);
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
          if (this.flashcardBusqueda && this.flashcardBusqueda.trim()) {
            const q = this.flashcardBusqueda.toLowerCase().trim();
            lista = lista.filter(f => (f.anverso || '').toLowerCase().includes(q) || (f.reverso || '').toLowerCase().includes(q));
          }
          return lista;
        },

        get flashcardsPorcentajeProgreso() {
          if (!this.flashcardsFiltradas.length) return 0;
          return Math.round(((this.flashcardIndice + 1) / this.flashcardsFiltradas.length) * 100);
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

        get unidadesUnificadasAsignaturaActual() {
          if (!this.asigSeleccionada) return [];
          const asig = this.asigSeleccionada;
          const temasMap = {};
          const otrosDocs = [];

          // 1. Mapear PDFs de temarios
          (asig.materiales || []).forEach(m => {
            const mStr = String(m || '').trim();
            const match = mStr.match(/tema\\s*(\\d+)[\\.\\s_-]*(.*?)(?:\\.pdf|$)/i);
            if (match) {
              const num = parseInt(match[1], 10);
              const nom = (match[2] || '').replace(/[\\.\\-_]/g, ' ').trim() || ('Tema ' + num);
              if (!temasMap[num]) {
                temasMap[num] = {
                  numero: num,
                  titulo: nom,
                  pdf: mStr,
                  guia: null,
                  test: null
                };
              } else {
                temasMap[num].pdf = mStr;
                if (!temasMap[num].titulo) temasMap[num].titulo = nom;
              }
            } else {
              otrosDocs.push(mStr);
            }
          });

          // 2. Asociar Guías de Estudio IA
          (asig.guias || []).forEach(g => {
            const match = g.match(/tema_(\\d+)/i);
            if (match) {
              const num = parseInt(match[1], 10);
              if (!temasMap[num]) {
                temasMap[num] = { numero: num, titulo: 'Tema ' + num, pdf: null, guia: g, test: null };
              } else {
                temasMap[num].guia = g;
              }
            }
          });

          // 3. Asociar Tests y Simulacros
          (asig.tests || []).forEach(t => {
            const match = t.match(/tema_(\\d+)/i);
            if (match) {
              const num = parseInt(match[1], 10);
              if (!temasMap[num]) {
                temasMap[num] = { numero: num, titulo: 'Tema ' + num, pdf: null, guia: null, test: t };
              } else {
                temasMap[num].test = t;
              }
            }
          });

          // 4. Vincular estado personal (Por hacer, En proceso, Hecho) y fecha en el calendario
          Object.values(temasMap).forEach(tm => {
            const evAgenda = this.obtenerEventoAgendaDeTema(tm.numero, tm.titulo);
            tm.eventoAgenda = evAgenda;
            tm.fecha_dia = evAgenda?.fecha_dia || null;
            tm.estado = this.obtenerEstadoDeTema(asig.nombre, tm.numero, evAgenda);
          });

          return {
            temas: Object.values(temasMap).sort((a, b) => a.numero - b.numero),
            otrosDocumentos: otrosDocs
          };
        },

        async cargarTemasEstado() {
          try {
            const guardado = localStorage.getItem('nova_temas_estado');
            if (guardado) this.temasEstado = JSON.parse(guardado);
          } catch(e) {}
          try {
            const res = await fetch('/api/temas-estado');
            if (res.ok) {
              const data = await res.json();
              if (data && typeof data === 'object') {
                this.temasEstado = Object.assign({}, this.temasEstado, data);
                try { localStorage.setItem('nova_temas_estado', JSON.stringify(this.temasEstado)); } catch(e) {}
              }
            }
          } catch(e) {}
        },

        obtenerEventoAgendaDeTema(numero, titulo = '') {
          if (!this.asigSeleccionada) return null;
          const asigNom = (this.asigSeleccionada.nombre || '').toLowerCase().trim();
          // 1. Priorizar evento que sea específicamente de categoría o tipo 'Tema'
          const temaExplicito = (this.agendaEventos || []).find(ev => {
            const evAsig = (ev.asignatura || '').toLowerCase().trim();
            if (evAsig !== asigNom && !asigNom.includes(evAsig) && !evAsig.includes(asigNom)) return false;
            if (ev.categoria !== 'Tema' && ev.tipo !== 'tema') return false;
            const tit = (ev.titulo || '').toLowerCase();
            const regex = new RegExp('(?:tema|tem|t)\\s*0*' + numero + '(?:\\b|[^\\d])', 'i');
            return regex.test(tit);
          });
          if (temaExplicito) return temaExplicito;

          // 2. Si no hay evento de tipo 'Tema', buscar cualquier otro evento planificado para el tema
          return (this.agendaEventos || []).find(ev => {
            const evAsig = (ev.asignatura || '').toLowerCase().trim();
            if (evAsig !== asigNom && !asigNom.includes(evAsig) && !evAsig.includes(asigNom)) return false;
            const tit = (ev.titulo || '').toLowerCase();
            const regex = new RegExp('(?:tema|tem|t)\\s*0*' + numero + '(?:\\b|[^\\d])', 'i');
            return regex.test(tit);
          }) || null;
        },

        obtenerEstadoDeTema(asigNombre, numero, evAgenda) {
          if (this.temasEstado && this.temasEstado[asigNombre] && this.temasEstado[asigNombre][numero]) {
            return this.temasEstado[asigNombre][numero];
          }
          if (evAgenda) {
            if (['Hecho', 'Vista', 'Vista no relevante'].includes(evAgenda.estado)) return 'Hecho';
            if (evAgenda.estado === 'En proceso') return 'En proceso';
          }
          return 'Por hacer';
        },

        claseBadgeEstadoTema(estado) {
          if (estado === 'Hecho') return 'bg-emerald-500/20 text-emerald-300 border-emerald-500/40 hover:bg-emerald-500/30';
          if (estado === 'En proceso') return 'bg-cyan-500/20 text-cyan-300 border-cyan-500/40 hover:bg-cyan-500/30';
          return 'bg-gray-800/90 text-gray-400 border-gray-700/80 hover:bg-gray-800 hover:text-gray-200';
        },

        iconoEstadoTema(estado) {
          if (estado === 'Hecho') return 'fa-solid fa-circle-check text-emerald-400';
          if (estado === 'En proceso') return 'fa-solid fa-spinner text-cyan-400';
          return 'fa-regular fa-circle text-gray-400';
        },

        formatearFechaDDMMAAAA(fechaStr) {
          if (!fechaStr) return '';
          const s = String(fechaStr).trim();
          if (/^\\d{2}\\/\\d{2}\\/\\d{4}$/.test(s)) return s;
          const m = s.match(/^(\\d{4})-(\\d{1,2})-(\\d{1,2})/);
          if (m) {
            return `${m[3].padStart(2, '0')}/${m[2].padStart(2, '0')}/${m[1]}`;
          }
          const d = new Date(s);
          if (!isNaN(d.getTime())) {
            const dd = String(d.getDate()).padStart(2, '0');
            const mm = String(d.getMonth() + 1).padStart(2, '0');
            const yyyy = d.getFullYear();
            return `${dd}/${mm}/${yyyy}`;
          }
          return s;
        },

        formatearPlazoTexto(txt, fechaStr) {
          if (!txt) return '';
          const m = txt.match(/\\((\\d{1,2}\\/\\d{1,2})\\)/);
          if (m && fechaStr) {
            const fPartes = String(fechaStr).match(/(\\d{4})/);
            if (fPartes) {
              return txt.replace(m[0], '(' + m[1] + '/' + fPartes[1] + ')');
            }
          }
          return txt;
        },

        formatearFechaCorta(fechaStr) {
          return this.formatearFechaDDMMAAAA(fechaStr);
        },

        async cambiarEstadoTema(tm, nuevoEstado) {
          if (!this.asigSeleccionada) return;
          const asig = this.asigSeleccionada.nombre;
          if (!this.temasEstado) this.temasEstado = {};
          if (!this.temasEstado[asig]) this.temasEstado[asig] = {};
          this.temasEstado[asig][tm.numero] = nuevoEstado;
          tm.estado = nuevoEstado;
          try {
            localStorage.setItem('nova_temas_estado', JSON.stringify(this.temasEstado));
          } catch(e) {}

          try {
            await fetch('/api/asignatura/tema/estado', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({
                asignatura: asig,
                tema_num: tm.numero,
                tema_titulo: tm.titulo,
                estado: nuevoEstado
              })
            });
            await this.cargarAgenda();
          } catch(e) {
            console.error('Error al guardar estado de tema:', e);
          }
        },

        planificarTemaEnCalendario(tm) {
          const asigNom = this.asigSeleccionada?.nombre || '';
          const hoyStr = new Date().toISOString().slice(0, 10);
          this.abrirModalNuevoItemAgenda('Tema', asigNom, hoyStr, 'calendario');
          this.nuevoItemTitulo = `Tema ${tm.numero}: ${tm.titulo}`;
          this.nuevoItemEstado = tm.estado === 'Hecho' ? 'Hecho' : (tm.estado === 'En proceso' ? 'En proceso' : 'Pendiente de empezar');
        },

        iniciarArrastreTema(event, tm) {
          if (tm.eventoAgenda) {
            this.iniciarArrastreItem(event, tm.eventoAgenda);
          } else {
            this.itemArrastrando = {
              esTemaUnificado: true,
              numero: tm.numero,
              titulo: tm.titulo,
              estado: tm.estado,
              asignatura: this.asigSeleccionada?.nombre || ''
            };
            if (event?.dataTransfer) {
              event.dataTransfer.effectAllowed = 'move';
              event.dataTransfer.setData('text/plain', `tema_${tm.numero}`);
            }
          }
        },

        async init() {
          if (window.marked) {
            marked.setOptions({
              gfm: true,
              breaks: true,
            });
          }
          window.addEventListener('popstate', () => this.procesarUrlInicial());
          await this.cargarUsuarioActual();
          await this.sincronizarClavesIa();
          this.cargarActividadReciente();
          await Promise.all([
            this.cargarMiCampus(),
            this.cargarProgramas(),
            this.cargarCatalogo(),
            this.cargarProgreso(),
            this.cargarAgenda(),
            this.cargarTemasEstado(),
            this.cargarModelosIa()
          ]);
          this.actualizarMensajeBienvenidaChat();
          await this.procesarUrlInicial();
        },

        abrirModalMiCampus() {
          this.miCampusMensajeError = '';
          this.miCampusMensajeExito = '';
          this.modalMiCampus = true;
        },

        async cargarMiCampus() {
          try {
            const res = await fetch('/api/mi-campus');
            if (res.ok) {
              const data = await res.json();
              this.miCampusConfig = data.config || { conectado: false };
              this.miCampusCursos = data.cursos || [];
              this.miCampusCalificaciones = data.calificaciones_campus || [];
              this.asignaturasFavoritas = this.miCampusConfig.asignaturas_favoritas || [];
            }
          } catch(e) { console.error(e); }
        },

        async conectarMiCampus() {
          this.miCampusMensajeError = '';
          this.miCampusMensajeExito = '';
          if (!this.inputCanvasToken.trim() && !this.miCampusConfig?.conectado) {
            this.miCampusMensajeError = 'Introduce tu Token de Acceso personal de Canvas UNIR.';
            return;
          }
          this.sincronizandoMiCampus = true;
          try {
            const res = await fetch('/api/mi-campus/conectar', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({
                canvas_token: this.inputCanvasToken.trim() || null,
                token_expira: this.inputCanvasTokenExpira ? this.inputCanvasTokenExpira : null
              })
            });
            const data = await res.json();
            if (!res.ok) {
              this.miCampusMensajeError = data.detail || 'No se pudo conectar con el Campus UNIR.';
              return;
            }
            this.inputCanvasToken = '';
            this.miCampusMensajeExito = data.mensaje || '¡Campus UNIR sincronizado con éxito!';
            await this.cargarMiCampus();
            await this.cargarAgenda();
          } catch(e) {
            this.miCampusMensajeError = 'Error de red al conectar con el servidor.';
          } finally {
            this.sincronizandoMiCampus = false;
          }
        },

        async desconectarMiCampus() {
          if (!confirm('¿Desconectar tu cuenta personal de Canvas UNIR?')) return;
          try {
            await fetch('/api/mi-campus/desconectar', { method: 'POST' });
            await this.cargarMiCampus();
            await this.cargarAgenda();
            this.miCampusMensajeExito = 'Cuenta de Campus desconectada.';
          } catch(e) {}
        },

        async sincronizarMaterialesMiCampus() {
          this.miCampusMensajeError = '';
          this.miCampusMensajeExito = '';
          this.sincronizandoMiCampus = true;
          try {
            const res = await fetch('/api/mi-campus/sincronizar-materiales', { method: 'POST' });
            const data = await res.json();
            if (!res.ok) {
              this.miCampusMensajeError = data.detail || 'No se pudieron sincronizar los PDFs.';
              return;
            }
            this.miCampusMensajeExito = data.mensaje || '¡PDFs sincronizados en tu espacio privado!';
            await this.cargarCatalogo();
          } catch(e) {
            this.miCampusMensajeError = 'Error de conexión al sincronizar materiales.';
          } finally {
            this.sincronizandoMiCampus = false;
          }
        },

        esAsignaturaFavorita(nombre) {
          return this.asignaturasFavoritas.includes(nombre);
        },

        async toggleAsignaturaFavorita(nombre) {
          if (this.asignaturasFavoritas.includes(nombre)) {
            this.asignaturasFavoritas = this.asignaturasFavoritas.filter(a => a !== nombre);
          } else {
            this.asignaturasFavoritas.push(nombre);
          }
          if (this.asignaturasFavoritas.length === 0) {
            this.mostrarSoloFavoritas = false;
          }
          this.aplicarFiltroCatalogo();
          try {
            await fetch('/api/mi-campus/favoritas', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ asignaturas: this.asignaturasFavoritas })
            });
          } catch(e) {}
        },

        toggleSoloFavoritas() {
          if (!this.mostrarSoloFavoritas && this.asignaturasFavoritas.length === 0) {
            alert('Primero marca con la estrella ⭐ en las tarjetas las asignaturas que estás cursando.');
            return;
          }
          this.mostrarSoloFavoritas = !this.mostrarSoloFavoritas;
          this.aplicarFiltroCatalogo();
        },

        notaCampusDeAsignatura(nombreAsig) {
          const c = (this.miCampusCursos || []).find(x => x.asignatura === nombreAsig);
          return (c && c.nota_actual_campus !== null && c.nota_actual_campus !== undefined) ? c.nota_actual_campus : null;
        },

        abrirGeneradorDirecto(asig) {
          this.asigSeleccionada = asig;
          this.generadorPdfSeleccionado = asig.materiales?.[0] || '';
          this.generadorTemaNombre = (asig.materiales?.[0] || 'Tema 1').replace('.pdf', '').replaceAll('_', ' ');
          this.generadorProfundidad = 'robusta';
          this.generadorNumPreguntas = 15;
          this.generadorTipo = 'ambos';
          this.modalGenerador = true;
        },

        _claveRecientesUsuario() {
          const u = (this.usuarioActual && this.usuarioActual.username) ? this.usuarioActual.username.toLowerCase() : 'anon';
          return 'unir_recientes_' + u;
        },

        cargarActividadReciente() {
          try {
            const raw = localStorage.getItem(this._claveRecientesUsuario());
            this.actividadReciente = raw ? JSON.parse(raw) : [];
          } catch(e) {
            this.actividadReciente = [];
          }
        },

        registrarActividadReciente(tipo, asignatura, titulo, valor) {
          if (!titulo) return;
          const id = `${tipo}:${asignatura}:${valor}`;
          const nuevo = { id, tipo, asignatura: asignatura || 'Estudio', titulo, valor };
          this.actividadReciente = [nuevo, ...this.actividadReciente.filter(x => x.id !== id)].slice(0, 8);
          try {
            localStorage.setItem(this._claveRecientesUsuario(), JSON.stringify(this.actividadReciente));
          } catch(e) {}
        },

        limpiarActividadReciente() {
          this.actividadReciente = [];
          try {
            localStorage.removeItem(this._claveRecientesUsuario());
          } catch(e) {}
        },

        abrirItemReciente(item) {
          if (!item) return;
          const asigObj = this.catalogoTotal.find(a => a.nombre === item.asignatura);
          if (asigObj) this.asigSeleccionada = asigObj;
          if (item.tipo === 'pdf') {
            this.abrirPdf(item.valor, item.titulo);
          } else if (item.tipo === 'guia') {
            this.verContenidoArchivo(item.valor);
          } else if (item.tipo === 'test') {
            this.iniciarTestWeb(item.asignatura, item.valor);
          }
        },

        async cargarModelosIa() {
          try {
            const res = await fetch('/api/modelos-ia');
            const data = await res.json();
            this.modelosIaDisponibles = data.modelos || [];

            // Si el modelo guardado previamente ya no existe en el catálogo (ej: modelos viejos o deprecados), volver a auto
            if (this.chatModeloSeleccionado && this.chatModeloSeleccionado !== 'auto') {
              const existe = this.modelosIaDisponibles.some(m => m.id === this.chatModeloSeleccionado);
              if (!existe) {
                this.chatModeloSeleccionado = 'auto';
                try { localStorage.setItem('unir_modelo_ia', 'auto'); } catch(e) {}
              }
            }

            if (data.ultimo_modelo_nombre && this.chatModeloSeleccionado === 'auto') {
              this.chatModeloActualNombre = data.ultimo_modelo_nombre;
            } else if (this.chatModeloSeleccionado !== 'auto') {
              const modObj = this.modelosIaDisponibles.find(m => m.id === this.chatModeloSeleccionado);
              if (modObj) {
                this.chatModeloActualNombre = modObj.nombre;
              }
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
          await this.abrirModalApiKey();
        },

        async abrirModalApiKey() {
          this.inputApiKeyTemp = this.userGeminiKey || localStorage.getItem('unir_user_gemini_key') || '';
          this.inputOpenrouterTemp = this.userOpenrouterKey || localStorage.getItem('unir_openrouter_key') || '';
          this.inputGroqTemp = this.userGroqKey || localStorage.getItem('unir_groq_key') || '';
          this.apiKeyMensajeError = '';
          this.apiKeyMensajeExito = '';
          this.modalApiKey = true;

          // Obtener las claves persistidas en el servidor (.env / SQLite) para pre-rellenar siempre
          try {
            const res = await fetch('/api/proveedores-ia');
            if (res.ok) {
              const data = await res.json();
              if (data.gemini_key && !this.inputApiKeyTemp) {
                this.inputApiKeyTemp = data.gemini_key;
              }
              if (data.openrouter_key && !this.inputOpenrouterTemp) {
                this.inputOpenrouterTemp = data.openrouter_key;
              }
              if (data.groq_key && !this.inputGroqTemp) {
                this.inputGroqTemp = data.groq_key;
              }
              if (data.gemini_key) {
                this.userGeminiKey = data.gemini_key;
                try { localStorage.setItem('unir_user_gemini_key', data.gemini_key); } catch(e) {}
              }
              if (data.openrouter_key) {
                this.userOpenrouterKey = data.openrouter_key;
                try { localStorage.setItem('unir_openrouter_key', data.openrouter_key); } catch(e) {}
              }
              if (data.groq_key) {
                this.userGroqKey = data.groq_key;
                try { localStorage.setItem('unir_groq_key', data.groq_key); } catch(e) {}
              }
            }
          } catch(e) {
            console.warn('No se pudieron obtener proveedores del servidor:', e);
          }
        },

        async guardarApiKeyUsuario() {
          const geminiLimpia = (this.inputApiKeyTemp || '').trim();
          const openrouterLimpia = (this.inputOpenrouterTemp || '').trim();
          const groqLimpia = (this.inputGroqTemp || '').trim();

          this.apiKeyValidando = true;
          this.apiKeyMensajeError = '';
          this.apiKeyMensajeExito = '';

          try {
            // Si introdujo una clave de Gemini nueva, validarla
            if (geminiLimpia && geminiLimpia !== this.userGeminiKey) {
              const resVal = await fetch('/api/validar-gemini-key', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ api_key: geminiLimpia })
              });
              const dataVal = await resVal.json();
              if (!dataVal.valida) {
                this.apiKeyMensajeError = dataVal.error || 'La clave de Gemini no es válida o ha caducado.';
                this.apiKeyValidando = false;
                return;
              }
            }

            // Guardar en el servidor (.env + SQLite de usuario)
            const res = await fetch('/api/proveedores-ia', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({
                gemini_key: geminiLimpia,
                openrouter_key: openrouterLimpia,
                groq_key: groqLimpia
              })
            });

            if (res.ok) {
              const data = await res.json();
              if (data && data.modelos) {
                this.modelosIaDisponibles = data.modelos;
              }
            }

            // Actualizar estado local y localStorage para triple redundancia
            this.userGeminiKey = geminiLimpia;
            this.userOpenrouterKey = openrouterLimpia;
            this.userGroqKey = groqLimpia;

            try {
              if (geminiLimpia) localStorage.setItem('unir_user_gemini_key', geminiLimpia);
              else localStorage.removeItem('unir_user_gemini_key');

              if (openrouterLimpia) localStorage.setItem('unir_openrouter_key', openrouterLimpia);
              else localStorage.removeItem('unir_openrouter_key');

              if (groqLimpia) localStorage.setItem('unir_groq_key', groqLimpia);
              else localStorage.removeItem('unir_groq_key');
            } catch(e) {}

            this.apiKeyMensajeExito = '¡Claves guardadas y activadas con éxito!';
            setTimeout(() => {
              this.modalApiKey = false;
            }, 1200);
          } catch(e) {
            this.apiKeyMensajeError = 'Ocurrió un error al guardar las claves: ' + (e.message || e);
          } finally {
            this.apiKeyValidando = false;
          }
        },

        async eliminarApiKeyUsuario() {
          if (!confirm('¿Deseas restablecer y eliminar las claves de IA configuradas?')) {
            return;
          }
          this.userGeminiKey = '';
          this.userOpenrouterKey = '';
          this.userGroqKey = '';
          this.inputApiKeyTemp = '';
          this.inputOpenrouterTemp = '';
          this.inputGroqTemp = '';

          try {
            localStorage.removeItem('unir_user_gemini_key');
            localStorage.removeItem('unir_openrouter_key');
            localStorage.removeItem('unir_groq_key');
          } catch(e) {}

          try {
            await fetch('/api/proveedores-ia', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({
                gemini_key: '',
                openrouter_key: '',
                groq_key: ''
              })
            });
          } catch(e) {}

          this.modalApiKey = false;
        },

        async sincronizarClavesIa() {
          try {
            const res = await fetch('/api/proveedores-ia');
            if (res.ok) {
              const data = await res.json();
              let cambioRequerido = false;
              const payload = {};

              // 1. Gemini
              const localGemini = localStorage.getItem('unir_user_gemini_key') || '';
              if (data.gemini_key) {
                this.userGeminiKey = data.gemini_key;
                try { localStorage.setItem('unir_user_gemini_key', data.gemini_key); } catch(e) {}
              } else if (localGemini) {
                payload.gemini_key = localGemini;
                this.userGeminiKey = localGemini;
                cambioRequerido = true;
              }

              // 2. OpenRouter
              const localOR = localStorage.getItem('unir_openrouter_key') || '';
              if (data.openrouter_key) {
                this.userOpenrouterKey = data.openrouter_key;
                try { localStorage.setItem('unir_openrouter_key', data.openrouter_key); } catch(e) {}
              } else if (localOR) {
                payload.openrouter_key = localOR;
                this.userOpenrouterKey = localOR;
                cambioRequerido = true;
              }

              // 3. Groq
              const localGroq = localStorage.getItem('unir_groq_key') || '';
              if (data.groq_key) {
                this.userGroqKey = data.groq_key;
                try { localStorage.setItem('unir_groq_key', data.groq_key); } catch(e) {}
              } else if (localGroq) {
                payload.groq_key = localGroq;
                this.userGroqKey = localGroq;
                cambioRequerido = true;
              }

              // Si el cliente tenía claves que faltaban en el servidor, sincronizar al servidor
              if (cambioRequerido) {
                const resPost = await fetch('/api/proveedores-ia', {
                  method: 'POST',
                  headers: { 'Content-Type': 'application/json' },
                  body: JSON.stringify(payload)
                });
                if (resPost.ok) {
                  const dataPost = await resPost.json();
                  if (dataPost && dataPost.modelos) {
                    this.modelosIaDisponibles = dataPost.modelos;
                  }
                }
              }
            }
          } catch(e) {
            console.warn('Error sincronizando claves de IA:', e);
          }
        },

        irASeccion(sec) {
          if (window.innerWidth < 768) {
            this.sidebarVisible = false;
          }
          this.tab = sec;
          this.asigSeleccionada = null;
          this.modalVisor = false;
          this.modalTest = false;
          this.modalVisorPdf = false;
          if (sec === 'clases' && (!this.todasLasClases || !this.todasLasClases.length)) {
            this.cargarVideoclases();
          }
          if (sec === 'flashcards' && (!this.flashcards || !this.flashcards.length)) {
            this.cargarFlashcards();
          }
          if (sec === 'admin') {
            this.cargarAdminUsuarios();
            this.cargarFeedback();
          }
          if (sec === 'feedback' && (!this.feedbackLista || !this.feedbackLista.length)) {
            this.cargarFeedback();
          }
          if (sec === 'tutor') {
            this.cargarHistorialParaAsignatura(this.chatAsignatura || '');
          }
          const url = (sec === 'dashboard') ? '/' : '/?tab=' + encodeURIComponent(sec);
          if (window.history && window.history.pushState) {
            window.history.pushState({ tab: sec }, '', url);
          }
        },

        abrirOPestana(p) {
          // Si tiene URL sincronizamos con el historial del navegador
          if (p?.url && window.history && window.history.replaceState) {
            window.history.replaceState(null, '', p.url);
          }
        },

        activarPestana(id) {},
        cerrarPestana(id) {},
        cerrarOtrasPestanas() {},
        cerrarPestanaActiva() {},

        cerrarModalVisor() {
          this.modalVisor = false;
          this.restaurarUrlBase();
        },

        cerrarModalPdf() {
          this.modalVisorPdf = false;
          this.restaurarUrlBase();
        },

        cerrarModalTest() {
          this.modalTest = false;
          this.restaurarUrlBase();
        },

        restaurarUrlBase() {
          let url = '/';
          if (this.asigSeleccionada) {
            url = '/?asig=' + encodeURIComponent(this.asigSeleccionada.nombre);
          } else if (this.tab && this.tab !== 'dashboard') {
            url = '/?tab=' + encodeURIComponent(this.tab);
          }
          if (window.history && window.history.replaceState) {
            window.history.replaceState(null, '', url);
          }
        },

        async cargarUsuarioActual() {
          try {
            const res = await fetch('/api/usuario/actual');
            const data = await res.json();
            this.usuarioActual = data;
            if (this.usuarioActual?.gemini_api_key) {
              this.userGeminiKey = this.usuarioActual.gemini_api_key;
              try { localStorage.setItem('unir_user_gemini_key', this.userGeminiKey); } catch(e) {}
            }
            if (this.usuarioActual?.openrouter_api_key) {
              this.userOpenrouterKey = this.usuarioActual.openrouter_api_key;
              try { localStorage.setItem('unir_openrouter_key', this.userOpenrouterKey); } catch(e) {}
            }
            if (this.usuarioActual?.groq_api_key) {
              this.userGroqKey = this.usuarioActual.groq_api_key;
              try { localStorage.setItem('unir_groq_key', this.userGroqKey); } catch(e) {}
            }
            if (this.usuarioActual && this.usuarioActual.es_admin) {
              await this.cargarAdminUsuarios();
            } else if (this.usuarioActual?.autenticado && !this.usuarioActual.es_admin && !this.userGeminiKey && !this.userOpenrouterKey && !this.userGroqKey) {
              // Estudiante sin ninguna API key configurada: abrir modal
              setTimeout(() => {
                this.abrirModalApiKey();
              }, 700);
            }
          } catch(e) { console.error('Error al cargar usuario actual', e); }
        },

        async cargarAdminUsuarios() {
          if (!this.usuarioActual?.es_admin) return;
          this.adminCargando = true;
          try {
            const res = await fetch('/api/admin/usuarios');
            const data = await res.json();
            this.adminUsuarios = data.usuarios || [];
            this.adminPendientesCount = data.pendientes || 0;
          } catch(e) {
            console.error('Error al cargar usuarios admin', e);
          } finally {
            this.adminCargando = false;
          }
        },

        async cambiarEstadoUsuario(userId, nuevoEstado) {
          try {
            const res = await fetch('/api/admin/usuario/estado', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ user_id: userId, activo: nuevoEstado })
            });
            if (res.ok) {
              await this.cargarAdminUsuarios();
            } else {
              alert('No se pudo actualizar el estado del usuario');
            }
          } catch(e) { alert('Error de conexión'); }
        },

        async eliminarUsuario(userId, username) {
          if (!confirm(`¿Estás seguro de que quieres eliminar la cuenta de "${username}"?`)) return;
          try {
            const res = await fetch(`/api/admin/usuario/${userId}`, { method: 'DELETE' });
            if (res.ok) {
              await this.cargarAdminUsuarios();
            } else {
              alert('No se pudo eliminar el usuario');
            }
          } catch(e) { alert('Error de conexión'); }
        },

        // --- MÉTODOS DE BUZÓN Y GESTIÓN DE INCIDENCIAS ---

        async cargarFeedback() {
          this.feedbackCargando = true;
          try {
            const res = await fetch('/api/feedback');
            if (res.ok) {
              const data = await res.json();
              this.feedbackLista = data.reportes || [];
              this.feedbackPendientesCount = data.pendientes || 0;
            }
          } catch(e) {
            console.error('Error al cargar feedback', e);
          } finally {
            this.feedbackCargando = false;
          }
        },

        async enviarFeedback() {
          if (!this.feedbackNuevoTitulo.trim() || !this.feedbackNuevoDesc.trim()) {
            this.feedbackMensajeError = 'Por favor completa el título y la descripción del reporte.';
            return;
          }
          this.feedbackEnviando = true;
          this.feedbackMensajeError = '';
          this.feedbackMensajeExito = '';
          try {
            const res = await fetch('/api/feedback', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({
                tipo: this.feedbackNuevoTipo,
                asignatura: this.feedbackNuevoAsig,
                titulo: this.feedbackNuevoTitulo,
                descripcion: this.feedbackNuevoDesc
              })
            });
            if (res.ok) {
              this.feedbackNuevoTitulo = '';
              this.feedbackNuevoDesc = '';
              this.feedbackNuevoAsig = '';
              this.feedbackMensajeExito = '¡Gracias! Tu reporte o sugerencia se ha registrado con éxito en el buzón.';
              await this.cargarFeedback();
              setTimeout(() => { this.feedbackMensajeExito = ''; }, 6000);
            } else {
              const err = await res.json();
              this.feedbackMensajeError = err.detail || 'Error al enviar el reporte.';
            }
          } catch(e) {
            this.feedbackMensajeError = 'Error de conexión con el servidor.';
          } finally {
            this.feedbackEnviando = false;
          }
        },

        async adminCambiarEstadoFeedback(reporteId, nuevoEstado, respuesta = null) {
          try {
            const payload = {
              feedback_id: reporteId,
              estado: nuevoEstado
            };
            if (respuesta !== null) {
              payload.respuesta_admin = respuesta;
            }
            const res = await fetch('/api/admin/feedback/estado', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify(payload)
            });
            if (res.ok) {
              await this.cargarFeedback();
            } else {
              alert('Error al actualizar el reporte.');
            }
          } catch(e) {
            alert('Error de conexión');
          }
        },

        async adminGuardarRespuestaFeedback(reporteId, estadoActual) {
          if (this.feedbackTextoRespTemp === null) return;
          await this.adminCambiarEstadoFeedback(reporteId, estadoActual, this.feedbackTextoRespTemp);
          this.feedbackEditandoRespId = null;
          this.feedbackTextoRespTemp = '';
        },

        async adminEliminarFeedback(reporteId) {
          if (!confirm('¿Seguro que deseas eliminar este reporte de incidencia o propuesta?')) return;
          try {
            const res = await fetch(`/api/admin/feedback/${reporteId}`, { method: 'DELETE' });
            if (res.ok) {
              await this.cargarFeedback();
            } else {
              alert('No se pudo eliminar el reporte.');
            }
          } catch(e) {
            alert('Error de conexión');
          }
        },

        async cerrarSesionUsuario() {
          if (!confirm('¿Cerrar sesión en NOVA Study Hub?')) return;
          try {
            localStorage.removeItem('unir_user_gemini_key');
          } catch(e) {}
          try {
            await fetch('/api/auth/logout', { method: 'POST' });
          } catch(e) {}
          window.location.reload();
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

        ordenarNatural(lista) {
          if (!lista || !Array.isArray(lista)) return [];
          return [...lista].sort((a, b) => {
            const parse = (str) => {
              const s = (str || '').replace(/_/g, ' ').trim();
              const mTema = s.match(/(?:tema|unit|m[óo]dulo)\\s*[\\._-]?\\s*(\\d+)/i);
              if (mTema) return [0, parseInt(mTema[1], 10), s.toLowerCase()];
              const mNum = s.match(/\\d+/);
              if (mNum) return [1, parseInt(mNum[0], 10), s.toLowerCase()];
              return [2, 999, s.toLowerCase()];
            };
            const pa = parse(a);
            const pb = parse(b);
            if (pa[0] !== pb[0]) return pa[0] - pb[0];
            if (pa[1] !== pb[1]) return pa[1] - pb[1];
            return pa[2].localeCompare(pb[2], undefined, { numeric: true, sensitivity: 'base' });
          });
        },

        async cargarCatalogo() {
          try {
            const res = await fetch('/api/catalogo');
            const data = await res.json();
            if (Array.isArray(data)) {
              data.forEach(asig => {
                if (Array.isArray(asig.guias)) asig.guias = this.ordenarNatural(asig.guias);
                if (Array.isArray(asig.tests)) asig.tests = this.ordenarNatural(asig.tests);
                if (Array.isArray(asig.materiales)) asig.materiales = this.ordenarNatural(asig.materiales);
              });
            }
            this.catalogoTotal = data;
            this.aplicarFiltroCatalogo();
            if (this.asigSeleccionada) {
              const asigActualizada = this.catalogoTotal.find(a => a.nombre === this.asigSeleccionada.nombre);
              if (asigActualizada) {
                this.asigSeleccionada = asigActualizada;
              }
            }
          } catch(e) { console.error(e); }
        },

        async cargarProgreso() {
          try {
            const res = await fetch('/api/progreso');
            this.progreso = await res.json();
          } catch(e) { console.error(e); }
        },

        seleccionarPrograma(progId) {
          if (window.innerWidth < 768) {
            this.sidebarVisible = false;
          }
          this.programaActivoId = progId;
          this.cursoFiltro = 'todos';
          this.asigSeleccionada = null;
          this.irASeccion('dashboard');
          this.aplicarFiltroCatalogo();
          this.actualizarMensajeBienvenidaChat();
          if (this.tab === 'flashcards') {
            this.cargarFlashcards();
          }
        },

        filtrarCurso(cursoId) {
          if (window.innerWidth < 768) {
            this.sidebarVisible = false;
          }
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
          if (this.mostrarSoloFavoritas && this.asignaturasFavoritas.length > 0) {
            lista = lista.filter(a => this.asignaturasFavoritas.includes(a.nombre));
          }
          if (this.busquedaTexto.trim()) {
            const q = this.busquedaTexto.toLowerCase();
            lista = lista.filter(a =>
              a.nombre.toLowerCase().includes(q) ||
              a.materiales.some(m => m.toLowerCase().includes(q)) ||
              (a.guias || []).some(g => g.toLowerCase().includes(q)) ||
              (a.tests || []).some(t => t.toLowerCase().includes(q))
            );
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
          if (window.innerWidth >= 768) {
            try {
              localStorage.setItem('unir_sidebar_visible', this.sidebarVisible);
            } catch(e) {}
          }
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
          this.chatInput = '¿Qué explicó el profesor en la sesión ' + clase.titulo + ' (' + this.formatearFechaDDMMAAAA(clase.fecha) + ') y qué indicaciones concretas dio?';
          this.enviarMensajeChat();
        },

        async abrirResumenClase(clase) {
          if (!clase.has_resumen && !this.userGeminiKey && !this.userOpenrouterKey && !this.userGroqKey && this.usuarioActual?.rol !== 'admin') {
            this.abrirModalApiKey();
            return;
          }
          this.resumenClaseActual = clase;
          this.modalResumenClase = true;
          this.cargandoResumenClase = true;
          this.resumenClaseTexto = '';
          this.resumenClaseCached = false;
          try {
            const res = await fetch('/api/resumen-clase', {
              method: 'POST',
              headers: { 
                'Content-Type': 'application/json',
                ...(this.userGeminiKey ? { 'X-User-Gemini-Key': this.userGeminiKey } : {})
              },
              body: JSON.stringify({
                asignatura: clase.asignatura,
                nombre_archivo: clase.nombre_archivo,
                api_key: (this.userGeminiKey && this.userGeminiKey.trim()) || null
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
          if (!this.userGeminiKey && !this.userOpenrouterKey && !this.userGroqKey && this.usuarioActual?.rol !== 'admin') {
            this.modalResumenClase = false;
            this.abrirModalApiKey();
            return;
          }
          this.cargandoResumenClase = true;
          this.resumenClaseTexto = '';
          try {
            const res = await fetch('/api/resumen-clase', {
              method: 'POST',
              headers: { 
                'Content-Type': 'application/json',
                ...(this.userGeminiKey ? { 'X-User-Gemini-Key': this.userGeminiKey } : {})
              },
              body: JSON.stringify({
                asignatura: this.resumenClaseActual.asignatura,
                nombre_archivo: this.resumenClaseActual.nombre_archivo,
                forzar: true,
                api_key: (this.userGeminiKey && this.userGeminiKey.trim()) || null
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
          this.generadorProfundidad = 'robusta';
          this.generadorNumPreguntas = 15;
          this.generadorTipo = 'ambos';
          this.modalGenerador = true;
        },

        async ejecutarGeneracionIa() {
          if (!this.asigSeleccionada || !this.generadorTemaNombre.trim()) return;
          if (!this.userGeminiKey && !this.userOpenrouterKey && !this.userGroqKey && this.usuarioActual?.rol !== 'admin') {
            this.modalGenerador = false;
            this.abrirModalApiKey();
            return;
          }
          this.generandoIa = true;
          this.generandoIaPaso = 1;
          const timerPaso2 = setTimeout(() => { if (this.generandoIa) this.generandoIaPaso = 2; }, 3500);
          const timerPaso3 = setTimeout(() => { if (this.generandoIa) this.generandoIaPaso = 3; }, 11000);
          try {
            const res = await fetch('/api/generar-guia', {
              method: 'POST',
              headers: { 
                'Content-Type': 'application/json',
                ...(this.userGeminiKey ? { 'X-User-Gemini-Key': this.userGeminiKey } : {})
              },
              body: JSON.stringify({
                asignatura: this.asigSeleccionada.nombre,
                nombre_tema: this.generadorTemaNombre.trim(),
                archivo_pdf: this.generadorPdfSeleccionado || null,
                profundidad: this.generadorProfundidad || 'media',
                num_preguntas: parseInt(this.generadorNumPreguntas) || 10,
                tipo_generacion: this.generadorTipo || 'ambos',
                api_key: (this.userGeminiKey && this.userGeminiKey.trim()) || null
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
              alert('¡Guía de estudio oficial generada con éxito!');
            } else if (data.tiene_test) {
              alert('¡Simulacro tipo test oficial generado con éxito!');
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
          const userPrefix = (this.usuarioActual && this.usuarioActual.username) ? this.usuarioActual.username.toLowerCase() : 'anon';
          const asigNorm = (asig || '').trim().toLowerCase().replace(/[^a-z0-9]/g, '_');
          const progId = this.programaActivoId || 'dam';
          return asigNorm ? `${userPrefix}_${progId}_${asigNorm}` : `${userPrefix}_${progId}__global_`;
        },

        obtenerMensajeBienvenida(asig) {
          const nombreUsuario = (this.usuarioActual && (this.usuarioActual.nombre || this.usuarioActual.username))
            ? (this.usuarioActual.nombre || this.usuarioActual.username).split(' ')[0]
            : 'Alex';

          if (asig && asig.trim()) {
            return `¡Hola ${nombreUsuario}! Soy **NOVA**, tu mentora y copiloto académica en **${asig}**. ¿Qué tema, videoclase o duda técnica quieres que trabajemos hoy?`;
          }
          const p = this.programaActual;
          if (p?.id === 'ciber') {
            return `¡Hola ${nombreUsuario}! Soy **NOVA**, tu copiloto en **Ciberseguridad y Auditorías**. ¿Qué módulo o temática (OWASP, SIEM, Forense, Malware o Hacking Ético) repasamos hoy?`;
          } else if (p?.id === 'pfu' || p?.id === 'grado') {
            return `¡Hola ${nombreUsuario}! Soy **NOVA**, tu mentora en **Ingeniería del Software & PFU**. Lista para resolver dudas de algoritmia, estructuras de datos o diseño formal.`;
          } else if (p?.id === 'bootcamp') {
            return `¡Hola ${nombreUsuario}! Soy **NOVA**, tu mentora en **Gestión Ágil y Habilidades Digitales**. ¿Repasamos Scrum, Kanban o transformación digital?`;
          } else if (p?.id === 'python') {
            return `¡Hola ${nombreUsuario}! Soy **NOVA**, tu profesora de **Programación en Python**. ¿Qué script, estructura de datos o ejercicio de ESIT ponemos a prueba hoy?`;
          } else if (p?.id === 'aws') {
            return `¡Hola ${nombreUsuario}! Soy **NOVA**, tu instructora para la certificación **AWS Cloud Practitioner (CLF-C02)**. ¿Hacemos un test rápido o repasamos servicios de AWS?`;
          } else if (p?.id === 'azure') {
            return `¡Hola ${nombreUsuario}! Soy **NOVA**, tu instructora para la certificación **Microsoft Azure Fundamentals (AZ-900)**. ¿Repasamos modelos cloud, seguridad o preguntas de examen?`;
          }
          return `¡Hola ${nombreUsuario}! Soy **NOVA**, tu copiloto para **FP DAM**. ¿Qué duda de examen o de código quieres que resolvamos hoy?`;
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
          const url = '/?tab=tutor' + (this.chatAsignatura ? '&asig=' + encodeURIComponent(this.chatAsignatura) : '');
          if (window.history && window.history.replaceState) {
            window.history.replaceState(null, '', url);
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

        abrirAsignaturaPorNombre(nombre) {
          if (!nombre || nombre === 'General' || nombre === 'Personal') return;
          this.modalItemAgenda = false;
          const normalizar = (str) => (str || '').toLowerCase()
            .replace(/\\(grupo\\s*\\d+\\)/gi, '')
            .replace(/\\[.*?\\]/g, '')
            .replace(/[^\\w\\sáéíóúüñ]/gi, '')
            .trim();
          const target = normalizar(nombre);
          
          const lista = (this.catalogoTotal && this.catalogoTotal.length > 0) ? this.catalogoTotal : ((this.catalogo && this.catalogo.length > 0) ? this.catalogo : (this.catalogoFiltrado || []));
          let match = lista.find(a => normalizar(a.nombre) === target);
          if (!match) {
            match = lista.find(a => {
              const aNom = normalizar(a.nombre);
              return aNom.includes(target) || target.includes(aNom);
            });
          }
          if (!match) {
            const acronimos = {
              ad: 'Acceso a Datos',
              di: 'Desarrollo de Interfaces',
              pmdm: 'Programación Multimedia y Dispositivos Móviles',
              psp: 'Programación de Servicios y Procesos',
              sge: 'Sistemas de Gestión Empresarial',
              nube: 'Arquitecturas en la Nube',
              proy: 'Proyecto Intermodular DAM',
              ffe: 'Formación en Empresa (FFE)',
              dig: 'Digitalización Aplicada',
              sost: 'Sostenibilidad Aplicada',
              ing: 'Inglés Profesional',
              ipe: 'Itinerario para la Empleabilidad II'
            };
            const acr = acronimos[target];
            if (acr) {
              match = lista.find(a => normalizar(a.nombre) === normalizar(acr));
            }
          }
          if (match) {
            this.abrirAsignatura(match);
            window.scrollTo({ top: 0, behavior: 'smooth' });
          }
        },

        abrirAsignatura(asig) {
          if (window.innerWidth < 768) {
            this.sidebarVisible = false;
          }
          this.asigSeleccionada = asig;
          this.tab = 'asignaturas';
          this.modalVisor = false;
          this.modalTest = false;
          this.modalVisorPdf = false;
          const url = '/?asig=' + encodeURIComponent(asig.nombre);
          if (window.history && window.history.pushState) {
            window.history.pushState({ asig: asig.nombre }, '', url);
          }
        },

        abrirChatConAsignatura(nombre) {
          if (window.innerWidth < 768) {
            this.sidebarVisible = false;
          }
          this.guardarHistorialActual();
          this.chatAsignatura = nombre || '';
          this.tab = 'tutor';
          this.asigSeleccionada = null;
          this.modalVisor = false;
          this.modalTest = false;
          this.modalVisorPdf = false;
          this.cargarHistorialParaAsignatura(this.chatAsignatura);
          const url = '/?tab=tutor' + (nombre ? '&asig=' + encodeURIComponent(nombre) : '');
          if (window.history && window.history.pushState) {
            window.history.pushState({ tab: 'tutor', asig: nombre }, '', url);
          }
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
            this.modalTest = false;
            this.modalVisorPdf = false;
            this.registrarActividadReciente('guia', this.asigSeleccionada?.nombre || 'Guía', tituloLimpio.replace(/ guia$/i, ''), ruta);
            const url = '/?guia=' + encodeURIComponent(ruta) + (this.asigSeleccionada ? '&asig=' + encodeURIComponent(this.asigSeleccionada.nombre) : '');
            if (window.history && window.history.pushState) {
              window.history.pushState({ guia: ruta }, '', url);
            }
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
            if (!res.ok) {
              const err = await res.json().catch(() => ({}));
              alert(err.detail || 'No se pudo cargar el test solicitado');
              this.modalTest = false;
              return;
            }
            const data = await res.json();
            if (!Array.isArray(data) || data.length === 0) {
              alert('El test está vacío o no contiene preguntas válidas');
              this.modalTest = false;
              return;
            }
            this.testPreguntas = data;
            this.testActivoAsig = asignatura;
            this.testActivoNombre = testNombre;
            this.testIndice = 0;
            this.testRespuestasUsuario = {};
            this.testRespuestaSeleccionada = null;
            this.testFinalizado = false;
            this.testMostrarPista = false;
            this.testModo = 'notebook';
            this.modalTest = true;
            this.modalVisor = false;
            this.modalVisorPdf = false;
            this.registrarActividadReciente('test', asignatura, testNombre.replace('_test', '').replaceAll('_', ' '), testNombre);
            const url = '/?test=' + encodeURIComponent(testNombre) + '&asig=' + encodeURIComponent(asignatura);
            if (window.history && window.history.pushState) {
              window.history.pushState({ test: testNombre, asig: asignatura }, '', url);
            }
          } catch(e) {
            console.error('Error al cargar test:', e);
            alert('Error de red al cargar el simulacro');
            this.modalTest = false;
          }
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

        escapeHtml(str) {
          if (!str) return '';
          return String(str)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#039;');
        },

        formatInlineCode(str) {
          if (!str) return '';
          return str.replace(/`([^`]+)`/g, '<code class="px-1.5 py-0.5 mx-0.5 rounded bg-gray-900 border border-cyan-500/30 text-cyan-300 font-mono text-xs">$1</code>');
        },

        beautifyCode(code) {
          if (!code) return '';
          const NL = String.fromCharCode(10);
          let trimmed = code.trim();
          // Si el código está en una sola línea o con saltos insuficientes
          if (!trimmed.includes(NL) || trimmed.split(NL).length <= 2) {
            let tokens = trimmed
              .replace(/[{]/g, ' {' + NL)
              .replace(/[}]/g, NL + '}' + NL)
              .replace(/[;](?![^()]*[)])/g, ';' + NL);
            let lines = tokens.split(NL).map(l => l.trim()).filter(l => l.length > 0);
            let result = [];
            let indent = 0;
            for (let line of lines) {
              if (line.startsWith('}')) indent = Math.max(0, indent - 1);
              result.push('  '.repeat(indent) + line);
              if (line.endsWith('{')) indent++;
            }
            return result.join(NL);
          }
          return trimmed;
        },

        renderEnunciadoTest(raw) {
          if (!raw) return '';
          // Detectar bloque de código ```lang ... ```
          const codeRegex = new RegExp("```([a-zA-Z0-9_-]*)\\\\s*([\\\\s\\\\S]*?)```");
          const match = raw.match(codeRegex);

          if (match) {
            const antes = raw.substring(0, match.index).trim();
            const lang = (match[1] || 'código').toLowerCase().trim();
            const rawCode = match[2];
            const despues = raw.substring(match.index + match[0].length).trim();

            const formattedCode = this.escapeHtml(this.beautifyCode(rawCode));

            let html = '';
            if (antes) {
              html += `<p class="text-sm text-gray-200 font-medium leading-relaxed mb-3">${this.formatInlineCode(this.escapeHtml(antes))}</p>`;
            }

            html += `
              <div class="my-3 rounded-2xl overflow-hidden border border-gray-700/80 bg-gray-950 shadow-xl">
                <div class="flex items-center justify-between px-3.5 py-2 bg-gray-900/90 border-b border-gray-800 text-xs">
                  <div class="flex items-center space-x-2">
                    <span class="w-2.5 h-2.5 rounded-full bg-rose-500/80 inline-block"></span>
                    <span class="w-2.5 h-2.5 rounded-full bg-amber-500/80 inline-block"></span>
                    <span class="w-2.5 h-2.5 rounded-full bg-emerald-500/80 inline-block"></span>
                    <span class="font-mono text-[11px] font-bold text-cyan-300 uppercase ml-1.5 tracking-wider">${lang}</span>
                  </div>
                  <span class="text-[10px] text-gray-400 font-mono">Fragmento de código</span>
                </div>
                <pre class="p-4 text-xs font-mono text-emerald-300 bg-gray-950 overflow-x-auto leading-relaxed selection:bg-cyan-900 selection:text-white"><code>${formattedCode}</code></pre>
              </div>
            `;

            if (despues) {
              html += `<div class="text-base font-bold text-white pt-2 leading-snug">${this.formatInlineCode(this.escapeHtml(despues))}</div>`;
            }

            return html;
          }

          return `<div class="text-base font-bold text-white leading-snug">${this.formatInlineCode(this.escapeHtml(raw))}</div>`;
        },

        renderTextoOpcion(str) {
          if (!str) return '';
          return this.formatInlineCode(this.escapeHtml(str));
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
            const res = await fetch('/api/agenda?_t=' + Date.now());
            this.agendaEventos = await res.json();
          } catch(e) { console.error(e); }
        },

        async sincronizarAgenda() {
          this.sincronizandoAgenda = true;
          try {
            const res = await fetch('/api/sincronizar-agenda', { method: 'POST' });
            if (!res.ok) {
              const errData = await res.json().catch(() => ({}));
              throw new Error(errData.detail || `HTTP ${res.status}`);
            }
            const data = await res.json();
            this.agendaEventos = data.eventos || [];
            alert(`Sincronización completada. ${this.agendaEventos.length} entregas y eventos en tu calendario de Canvas.`);
          } catch(e) {
            console.error('Error sincronizando agenda:', e);
            alert('Error al sincronizar la agenda: ' + (e.message || e));
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
          this.flashcardSesionCompletada = false;
          this.flashcardStatsSesion = { facil: 0, medio: 0, dificil: 0 };
        },

        reiniciarMazoFlashcards() {
          this.flashcardIndice = 0;
          this.flashcardVolteada = false;
          this.flashcardSesionCompletada = false;
          this.flashcardStatsSesion = { facil: 0, medio: 0, dificil: 0 };
        },

        siguienteFlashcard() {
          if (this.flashcardIndice < this.flashcardsFiltradas.length - 1) {
            this.flashcardIndice++;
            this.flashcardVolteada = false;
          } else {
            this.flashcardSesionCompletada = true;
          }
        },

        anteriorFlashcard() {
          if (this.flashcardSesionCompletada) {
            this.flashcardSesionCompletada = false;
            return;
          }
          if (this.flashcardIndice > 0) {
            this.flashcardIndice--;
            this.flashcardVolteada = false;
          }
        },

        async calificarTarjeta(dificultad) {
          if (!this.tarjetaActual) return;
          if (this.flashcardStatsSesion[dificultad] !== undefined) {
            this.flashcardStatsSesion[dificultad]++;
          }
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
          if (!this.userGeminiKey && !this.userOpenrouterKey && !this.userGroqKey && this.usuarioActual?.rol !== 'admin') {
            this.modalGenerarFlashcards = false;
            this.abrirModalApiKey();
            return;
          }
          this.generandoFlashcards = true;
          try {
            const res = await fetch('/api/flashcards/generar', {
              method: 'POST',
              headers: { 
                'Content-Type': 'application/json',
                ...(this.userGeminiKey ? { 'X-User-Gemini-Key': this.userGeminiKey } : {})
              },
              body: JSON.stringify({
                asignatura: this.nuevaCardAsig || (this.todasLasAsignaturasPrograma[0] || 'Acceso a Datos'),
                programa: this.programaActivoId,
                texto: this.nuevaCardTema.trim(),
                cantidad: this.nuevaCardCantidad || 4,
                api_key: (this.userGeminiKey && this.userGeminiKey.trim()) || null
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
          this.chatInput = `En la sesión ${clase.titulo} (${this.formatearFechaDDMMAAAA(clase.fecha)}), en el minuto [${cue.start_str}], ${cue.speaker} explicó: "${cue.text}". ¿Me lo puedes explicar con más detalle técnico y darme un ejemplo práctico?`;
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
          this.modalVisor = false;
          this.modalTest = false;
          this.registrarActividadReciente('pdf', this.asigSeleccionada?.nombre || 'Temario', titulo, ruta);
          const url = '/?pdf=' + encodeURIComponent(ruta) + (this.asigSeleccionada ? '&asig=' + encodeURIComponent(this.asigSeleccionada.nombre) : '');
          if (window.history && window.history.pushState) {
            window.history.pushState({ pdf: ruta }, '', url);
          }
        },

        consultarNovaPdf() {
          this.modalVisorPdf = false;
          this.chatAsignatura = this.asigSeleccionada?.nombre || '';
          this.tab = 'tutor';
          this.chatInput = `Estoy estudiando el documento "${this.modalTitulo}". ¿Cuáles son los puntos clave más importantes de cara al examen y qué trampas o preguntas típicas suelen poner sobre este temario?`;
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

          const nombreUsuario = (this.usuarioActual && (this.usuarioActual.nombre || this.usuarioActual.username))
            ? (this.usuarioActual.nombre || this.usuarioActual.username).split(' ')[0]
            : 'Alex';

          const bienvenida = [{
            rol: 'model',
            texto: `¡Memoria reiniciada para ${asig || 'NOVA'}! Hola ${nombreUsuario}, ¿en qué nos enfocamos ahora?`
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

        limpiarTextoParaVoz(md) {
          if (!md) return '';
          let t = md;
          t = t.replace(/```[\\s\\S]*?```/g, ' [Bloque de código omitido]. ');
          t = t.replace(/`[^`]+`/g, ' ');
          t = t.replace(/#{1,6}\\s+/g, '');
          t = t.replace(/\\*\\*([^*]+)\\*\\*/g, '$1');
          t = t.replace(/\\*([^*]+)\\*/g, '$1');
          t = t.replace(/__([^_]+)__/g, '$1');
          t = t.replace(/_([^_]+)_/g, '$1');
          t = t.replace(/!\\[.*?\\]\\(.*?\\)/g, '');
          t = t.replace(/\\[(.*?)\\]\\(.*?\\)/g, '$1');
          t = t.replace(/^>\\s+/gm, '');
          t = t.replace(/^[-*+]\\s+/gm, '');
          t = t.replace(/^\\d+\\.\\s+/gm, '');
          t = t.replace(/\\|.*?\\|/g, '');
          t = t.replace(/[-:]{2,}/g, '');
          t = t.replace(/\\n+/g, '. ');
          t = t.replace(/\\s{2,}/g, ' ');
          return t.trim();
        },

        detenerVoz() {
          if (this.reproductorAudio) {
            this.reproductorAudio.pause();
            this.reproductorAudio = null;
          }
          if ('speechSynthesis' in window) {
            window.speechSynthesis.cancel();
          }
          this.vozMensajeActivoIdx = null;
        },

        async alternarVozMensaje(idx, texto) {
          if (this.vozMensajeActivoIdx === idx) {
            this.detenerVoz();
            return;
          }

          this.detenerVoz();
          const textoLimpio = this.limpiarTextoParaVoz(texto);
          if (!textoLimpio) return;

          this.vozMensajeActivoIdx = idx;

          // 1. Solicitar audio MP3 de alta fidelidad al servidor
          try {
            const res = await fetch('/api/tts', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ texto: textoLimpio.slice(0, 3000) })
            });

            if (res.ok) {
              const blob = await res.blob();
              if (blob && blob.size > 200) {
                const url = URL.createObjectURL(blob);
                const audio = new Audio();
                audio.src = url;
                this.reproductorAudio = audio;

                audio.onended = () => {
                  if (this.vozMensajeActivoIdx === idx) {
                    this.vozMensajeActivoIdx = null;
                  }
                  URL.revokeObjectURL(url);
                };

                audio.onerror = (e) => {
                  console.warn('Error en elemento de audio:', e);
                  if (this.vozMensajeActivoIdx === idx) {
                    this.vozMensajeActivoIdx = null;
                  }
                  URL.revokeObjectURL(url);
                };

                try {
                  await audio.play();
                  return;
                } catch (playErr) {
                  console.warn('Error al iniciar audio.play():', playErr);
                  this.vozMensajeActivoIdx = null;
                  URL.revokeObjectURL(url);
                  return;
                }
              }
            }
          } catch(err) {
            console.warn('Error conectando con /api/tts:', err);
          }

          // 2. Si falló el servidor, probar síntesis del navegador si está disponible
          if ('speechSynthesis' in window && window.speechSynthesis.getVoices().length > 0) {
            try {
              const utterance = new SpeechSynthesisUtterance(textoLimpio.slice(0, 500));
              utterance.lang = 'es-ES';
              utterance.onend = () => { if (this.vozMensajeActivoIdx === idx) this.vozMensajeActivoIdx = null; };
              utterance.onerror = () => { this.vozMensajeActivoIdx = null; };
              window.speechSynthesis.speak(utterance);
              return;
            } catch(e) {
              console.warn('SpeechSynthesis error:', e);
            }
          }

          // Si nada funcionó, resetear el estado del botón
          this.vozMensajeActivoIdx = null;
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

        detenerConsultaChat() {
          if (this.chatAbortController) {
            try {
              this.chatAbortController.abort();
            } catch (err) {}
            this.chatAbortController = null;
          }
          this.chatCargando = false;
        },

        async enviarMensajeChat() {
          if (!this.chatInput.trim() || this.chatCargando) return;
          if (!this.userGeminiKey && !this.userOpenrouterKey && !this.userGroqKey && this.usuarioActual?.rol !== 'admin') {
            this.abrirModalApiKey();
            return;
          }
          const texto = this.chatInput.trim();
          const asigOrigen = this.chatAsignatura;
          const programaOrigen = this.programaActivoId;
          const claveOrigen = this.obtenerClaveChat(asigOrigen);

          // Capturar historial previo completo (hasta 16 turnos) para mantener contexto
          const historialPrevio = this.chatHistorial.map(m => ({
            rol: m.rol,
            texto: m.texto
          })).slice(-16);

          this.chatHistorial.push({ rol: 'user', texto });
          this.guardarHistorialActual();
          this.chatInput = '';
          this.chatCargando = true;

          // Crear AbortController para permitir cancelar la consulta si el usuario lo solicita
          this.chatAbortController = new AbortController();
          const abortSignal = this.chatAbortController.signal;

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

          const chatHeaders = { 'Content-Type': 'application/json' };
          if (this.userGeminiKey && this.userGeminiKey.trim()) {
            chatHeaders['X-User-Gemini-Key'] = this.userGeminiKey.trim();
          }
          if (this.userOpenrouterKey && this.userOpenrouterKey.trim()) {
            chatHeaders['X-User-Openrouter-Key'] = this.userOpenrouterKey.trim();
          }
          if (this.userGroqKey && this.userGroqKey.trim()) {
            chatHeaders['X-User-Groq-Key'] = this.userGroqKey.trim();
          }

          const chatPayload = {
            mensaje: texto,
            asignatura: asigOrigen,
            programa: programaOrigen,
            historial: historialPrevio,
            modelo: this.chatModeloSeleccionado || 'auto',
            api_key: (this.userGeminiKey && this.userGeminiKey.trim()) || null
          };

          let fueCancelado = false;
          let acumulado = '';

          try {
            const res = await fetch('/api/chat-stream', {
              method: 'POST',
              headers: chatHeaders,
              body: JSON.stringify(chatPayload),
              signal: abortSignal
            });

            if (!res.ok || !res.body) {
              throw new Error('Streaming no disponible');
            }

            const reader = res.body.getReader();
            const decoder = new TextDecoder('utf-8');
            let buffer = '';
            let primerChunkRecibido = false;

            while (true) {
              if (abortSignal.aborted) {
                fueCancelado = true;
                break;
              }
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
            if (!fueCancelado && !acumulado.trim()) {
              throw new Error('Stream vacío');
            }
            if (!fueCancelado) {
              actualizarMensajeEnOrigen(acumulado, true);
            }
          } catch(e) {
            if (e.name === 'AbortError' || abortSignal.aborted) {
              fueCancelado = true;
            } else {
              try {
                const resFallback = await fetch('/api/chat', {
                  method: 'POST',
                  headers: chatHeaders,
                  body: JSON.stringify(chatPayload),
                  signal: abortSignal
                });
                const data = await resFallback.json();
                if (data.modelo_nombre) {
                  this.chatModeloActualNombre = data.modelo_nombre;
                }
                actualizarMensajeEnOrigen(data.respuesta || 'Sin respuesta de NOVA.', true);
              } catch (errFallback) {
                if (errFallback.name === 'AbortError' || abortSignal.aborted) {
                  fueCancelado = true;
                } else {
                  actualizarMensajeEnOrigen('Error al contactar con NOVA.', true);
                }
              }
            }
          } finally {
            if (fueCancelado || abortSignal.aborted) {
              if (acumulado.trim()) {
                actualizarMensajeEnOrigen(acumulado + '\\n\\n*(⏹ Consulta detenida por el usuario)*', true);
              } else {
                actualizarMensajeEnOrigen('*(⏹ Consulta detenida por el usuario)*', true);
              }
            }
            this.chatAbortController = null;
            this.chatCargando = false;
            this.cargarModelosIa();
            this.$nextTick(() => {
              const el = document.getElementById('chatBox');
              if (el) el.scrollTop = el.scrollHeight;
              if (!fueCancelado && this.autoVozActiva && this.chatHistorial.length > 0) {
                const ultimoIdx = this.chatHistorial.length - 1;
                const ultMsg = this.chatHistorial[ultimoIdx];
                if (ultMsg && ultMsg.rol === 'model' && ultMsg.texto) {
                  this.alternarVozMensaje(ultimoIdx, ultMsg.texto);
                }
              }
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
    console.print(f"\n[bold green]🚀 Iniciando Interfaz Gráfica Web de NOVA Study Hub (Multi-Titulación)...[/bold green]")
    console.print(f"[bold cyan]💻 En tu ordenador (acceso directo):[/bold cyan]      [bold white underline]http://localhost:{port}[/bold white underline]")
    console.print(f"[bold yellow]📱 En tu móvil / Nube (con token seguro):[/bold yellow] [bold white underline]http://{ip_local}:{port}/?token={HUB_ACCESS_TOKEN}[/bold white underline]\n")
    uvicorn.run(app, host=host, port=port, log_level="warning")


if __name__ == "__main__":
    iniciar_servidor_web()
