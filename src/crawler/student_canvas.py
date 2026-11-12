"""
Conector Personal del Campus UNIR (Canvas LMS) por Estudiante.
Permite que cada cuenta de usuario conecte su propio perfil de Canvas UNIR de dos formas seguras
(sin contraseñas ni 2FA, y con 0 consumo de RAM en Render):
1. Token Personal de Acceso de Canvas (`https://campusfp.unir.net/profile/settings` -> Nuevo token de acceso)
   -> Sincroniza asignaturas matriculadas, estado real de entregas (Entregada / Pendiente / Calificada),
      notas oficiales puestas por los profesores en cada PAC y calendario personal.
2. Enlace de Alimentación de Calendario (.ics) (`https://campusfp.unir.net/feeds/calendars/user_...ics`)
   -> Sincroniza el calendario personal de entregas y sesiones del alumno.
"""

from datetime import datetime, timedelta, timezone
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from src.core.academic_structure import normalizar_nombre_asignatura
from src.core.config import obtener_espacio_usuario, obtener_rutas_usuario, escribir_json_atomico, MATERIALES_DIR, USUARIOS_ESPACIOS_DIR
from src.crawler.agenda_sync import calcular_dias_restantes

DEFAULT_CANVAS_BASE_URL = "https://campusfp.unir.net"
TOKEN_DIAS_MAX = 30   # UNIR limita los tokens personales a 30 días
TOKEN_DIAS_AVISO = 7  # Avisar al alumno cuando queden 7 días o menos antes de expirar


def _archivos_canvas_usuario(username: str) -> Tuple[Path, Path]:
    espacio = obtener_espacio_usuario(username)
    return espacio / "canvas_config.json", espacio / "canvas_sync.json"


def obtener_config_canvas_usuario(username: str) -> Dict[str, Any]:
    cfg_file, _ = _archivos_canvas_usuario(username)
    if cfg_file.exists():
        try:
            return json.loads(cfg_file.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {
        "conectado": False,
        "modo": None,
        "base_url": DEFAULT_CANVAS_BASE_URL,
        "canvas_token_masked": "",
        "ics_url": "",
        "alumno_nombre": "",
        "alumno_email": "",
        "ultima_sincronizacion": None,
        "asignaturas_favoritas": [],
    }


def guardar_asignaturas_favoritas_usuario(username: str, asignaturas: List[str]) -> Dict[str, Any]:
    cfg_file, _ = _archivos_canvas_usuario(username)
    cfg = obtener_config_canvas_usuario(username)
    cfg["asignaturas_favoritas"] = sorted(list({str(a).strip() for a in asignaturas if str(a).strip()}))
    escribir_json_atomico(cfg_file, cfg)
    try:
        from src.core.cloud_sync import respaldar_archivo_en_hf_async
        respaldar_archivo_en_hf_async(cfg_file)
    except Exception:
        pass
    return cfg


def _canvas_api_get(base_url: str, token: str, endpoint: str) -> Any:
    url = f"{base_url.rstrip('/')}{endpoint}"
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {token.strip()}",
            "Accept": "application/json",
            "User-Agent": "NOVA-Study-Hub/3.5",
        },
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _parsear_calendario_ics(ics_text: str) -> List[Dict[str, Any]]:
    """Extrae eventos VEVENT de un feed .ics de Canvas UNIR."""
    eventos = []
    bloques = re.findall(r"BEGIN:VEVENT(.*?)END:VEVENT", ics_text, re.DOTALL)
    for idx, bloque in enumerate(bloques):
        # Unfold lines RFC 5545
        limpio = re.sub(r"\r?\n[ \t]", "", bloque)
        uid_m = re.search(r"^UID:(.*)$", limpio, re.MULTILINE)
        sum_m = re.search(r"^SUMMARY:(.*)$", limpio, re.MULTILINE)
        dt_m = re.search(r"^DTSTART(?:;[^:]*)?:(\d{8}(?:T\d{6}Z?)?)", limpio, re.MULTILINE)
        end_m = re.search(r"^DTEND(?:;[^:]*)?:(\d{8}(?:T\d{6}Z?)?)", limpio, re.MULTILINE)
        url_m = re.search(r"^URL:(.*)$", limpio, re.MULTILINE)
        desc_m = re.search(r"^DESCRIPTION:(.*)$", limpio, re.MULTILINE)

        summary_raw = (sum_m.group(1).strip() if sum_m else "Evento académico").replace("\\,", ",").replace("\\;", ";")
        dt_raw = (end_m or dt_m).group(1).strip() if (end_m or dt_m) else ""
        fecha_iso = None
        if len(dt_raw) >= 8:
            try:
                if "T" in dt_raw:
                    dt_obj = datetime.strptime(dt_raw.rstrip("Z"), "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc)
                else:
                    dt_obj = datetime.strptime(dt_raw[:8], "%Y%m%d").replace(hour=21, minute=59, second=59, tzinfo=timezone.utc)
                fecha_iso = dt_obj.isoformat()
            except Exception:
                fecha_iso = None

        # En Canvas el summary suele traer "Título [Nombre del Curso]"
        asig_raw = "General"
        titulo = summary_raw
        m_corchete = re.search(r"^(.*?)\s*\[([^\]]+)\]\s*$", summary_raw)
        if m_corchete:
            titulo = m_corchete.group(1).strip()
            asig_raw = m_corchete.group(2).strip()

        asignatura = normalizar_nombre_asignatura(asig_raw) or asig_raw
        dias_restantes, plazo_texto = calcular_dias_restantes(fecha_iso)
        if dias_restantes < -1:
            continue

        urgencia = "critica" if dias_restantes <= 2 else ("alta" if dias_restantes <= 7 else ("media" if dias_restantes <= 15 else "baja"))
        desc_txt = (desc_m.group(1).strip() if desc_m else "").replace("\\n", " ").replace("\\,", ",")
        desc_txt = re.sub(r"<[^>]+>", " ", desc_txt).strip()[:200]

        eventos.append({
            "id": (uid_m.group(1).strip() if uid_m else f"ics_{idx}"),
            "tipo": "tarea" if any(k in titulo.lower() for k in ("actividad", "pac", "entrega", "examen", "proyecto", "test")) else "clase_directo",
            "titulo": titulo,
            "asignatura": asignatura,
            "fecha_iso": fecha_iso,
            "dias_restantes": dias_restantes,
            "plazo_texto": plazo_texto,
            "urgencia": urgencia,
            "puntos": None,
            "url": (url_m.group(1).strip() if url_m else DEFAULT_CANVAS_BASE_URL),
            "descripcion": desc_txt,
            "entregada": False,
            "calificada": False,
            "nota": None,
        })

    eventos.sort(key=lambda x: (x["dias_restantes"] if x["dias_restantes"] >= 0 else 9999))
    return eventos


def _parse_fecha(valor: Optional[str]) -> Optional[datetime]:
    """Convierte 'YYYY-MM-DD', 'DD/MM/YYYY' o ISO 8601 a datetime (naive, hora local) o None."""
    if not valor:
        return None
    try:
        txt = str(valor).strip().replace("Z", "+00:00")
        if "/" in txt and len(txt) == 10:
            partes = txt.split("/")
            if len(partes) == 3:
                # Si viene en formato DD/MM/YYYY o YYYY/MM/DD
                if len(partes[0]) == 4:
                    return datetime.strptime(txt, "%Y/%m/%d").replace(hour=23, minute=59)
                return datetime.strptime(txt, "%d/%m/%Y").replace(hour=23, minute=59)
        if len(txt) == 10:
            return datetime.strptime(txt, "%Y-%m-%d").replace(hour=23, minute=59)
        dt = datetime.fromisoformat(txt)
        return dt.astimezone().replace(tzinfo=None) if dt.tzinfo else dt
    except Exception:
        return None


def _detectar_caducidad_token(campus_url: str, token: str) -> Optional[str]:
    """
    Intenta leer la fecha de vencimiento real del token desde la API de Canvas
    (`/api/v1/users/self/tokens/<token_hint>`). Si UNIR no lo permite, devuelve None
    y se usa la fecha que indicó el alumno (o una estimación de 30 días).
    """
    hint = token.strip()[:5]
    if not hint:
        return None
    try:
        info = _canvas_api_get(campus_url, token, f"/api/v1/users/self/tokens/{urllib.parse.quote(hint)}")
        if isinstance(info, dict) and info.get("expires_at"):
            return str(info["expires_at"])
    except Exception:
        pass
    return None


def estado_token_canvas(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Resume el estado del token para la interfaz: ok / por_caducar / caducado / sin_token."""
    if cfg.get("modo") == "oauth":
        # Con OAuth la app renueva el acceso sola con el refresh token: nunca caduca para el alumno.
        return {"estado": "oauth", "dias_restantes": None, "expira": None, "estimada": False}
    if not cfg.get("_canvas_token_raw") and not cfg.get("canvas_token_masked"):
        return {"estado": "sin_token", "dias_restantes": None, "expira": None, "estimada": False}
    expira_dt = _parse_fecha(cfg.get("token_expira"))
    dias = None
    if expira_dt:
        dias = (expira_dt.date() - datetime.now().date()).days
    if cfg.get("token_caducado") or (dias is not None and dias < 0):
        estado = "caducado"
    elif dias is not None and dias <= TOKEN_DIAS_AVISO:
        estado = "por_caducar"
    else:
        estado = "ok"
    return {
        "estado": estado,
        "dias_restantes": dias,
        "expira": expira_dt.strftime("%Y-%m-%d") if expira_dt else None,
        "estimada": bool(cfg.get("token_expira_estimada")),
    }


def _descargar_eventos_ics(feed_ics: str) -> Tuple[bool, str, List[Dict[str, Any]]]:
    if not feed_ics.startswith("http") or ".ics" not in feed_ics.lower():
        return False, "El enlace de calendario debe ser una URL válida terminada en .ics (Alimentación del calendario de Canvas).", []
    try:
        req = urllib.request.Request(feed_ics, headers={"User-Agent": "NOVA-Study-Hub/3.5"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            ics_text = resp.read().decode("utf-8", errors="ignore")
    except Exception as e:
        return False, f"No se pudo descargar el calendario .ics: {e}", []
    if "BEGIN:VCALENDAR" not in ics_text:
        return False, "El enlace proporcionado no contiene un calendario iCal (.ics) válido de Canvas.", []
    return True, "", _parsear_calendario_ics(ics_text)


def _fusionar_eventos_ics(entregas: List[Dict[str, Any]], eventos_ics: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Añade al listado del token los eventos del .ics (clases, sesiones...) que no estén ya como tarea."""
    claves = {((e.get("titulo") or "").strip().lower(), (e.get("fecha_iso") or "")[:10]) for e in entregas}
    extra = [
        ev for ev in eventos_ics
        if ((ev.get("titulo") or "").strip().lower(), (ev.get("fecha_iso") or "")[:10]) not in claves
    ]
    return entregas + extra


def _guardar_cfg_y_sync(cfg_file: Path, sync_file: Path, cfg: Dict[str, Any], datos: Optional[Dict[str, Any]]) -> None:
    escribir_json_atomico(cfg_file, cfg)
    if datos is not None:
        escribir_json_atomico(sync_file, datos)
    try:
        from src.core.cloud_sync import respaldar_archivo_en_hf_async
        respaldar_archivo_en_hf_async(cfg_file)
        if datos is not None:
            respaldar_archivo_en_hf_async(sync_file)
    except Exception:
        pass


def _leer_sync_previo(sync_file: Path) -> Dict[str, Any]:
    if sync_file.exists():
        try:
            return json.loads(sync_file.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


# ==================== OAUTH2 OFICIAL DE CANVAS (PREPARADO, REQUIERE DEVELOPER KEY DE UNIR) ====================

def oauth_config() -> Dict[str, str]:
    """Lee la Developer Key de Canvas desde variables de entorno (la proporciona el departamento TIC de UNIR)."""
    return {
        "client_id": os.environ.get("CANVAS_OAUTH_CLIENT_ID", "").strip(),
        "client_secret": os.environ.get("CANVAS_OAUTH_CLIENT_SECRET", "").strip(),
        "redirect_uri": os.environ.get("CANVAS_OAUTH_REDIRECT_URI", "").strip(),
        "base_url": (os.environ.get("CANVAS_BASE_URL", "") or DEFAULT_CANVAS_BASE_URL).strip().rstrip("/"),
    }


def oauth_disponible() -> bool:
    c = oauth_config()
    return bool(c["client_id"] and c["client_secret"] and c["redirect_uri"])


def oauth_url_autorizacion(state: str) -> str:
    c = oauth_config()
    params = urllib.parse.urlencode({
        "client_id": c["client_id"],
        "response_type": "code",
        "redirect_uri": c["redirect_uri"],
        "state": state,
    })
    return f"{c['base_url']}/login/oauth2/auth?{params}"


def _oauth_pedir_token(datos: Dict[str, str]) -> Dict[str, Any]:
    c = oauth_config()
    cuerpo = urllib.parse.urlencode({**datos, "client_id": c["client_id"], "client_secret": c["client_secret"]}).encode()
    req = urllib.request.Request(
        f"{c['base_url']}/login/oauth2/token",
        data=cuerpo,
        headers={"Content-Type": "application/x-www-form-urlencoded", "User-Agent": "NOVA-Study-Hub/3.5"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _guardar_tokens_oauth(username: str, respuesta: Dict[str, Any]) -> None:
    cfg_file, _ = _archivos_canvas_usuario(username)
    cfg = obtener_config_canvas_usuario(username)
    expira = datetime.now() + timedelta(seconds=int(respuesta.get("expires_in") or 3600))
    cfg.update({
        "modo": "oauth",
        "base_url": oauth_config()["base_url"],
        "_canvas_token_raw": respuesta.get("access_token", ""),
        "_refresh_token": respuesta.get("refresh_token") or cfg.get("_refresh_token", ""),
        "_access_expira": expira.isoformat(timespec="seconds"),
        "canvas_token_masked": "Inicio de sesión UNIR",
        "token_expira": None,
        "token_expira_estimada": False,
        "token_caducado": False,
    })
    escribir_json_atomico(cfg_file, cfg)


def oauth_intercambiar_codigo(username: str, code: str) -> Tuple[bool, str]:
    if not oauth_disponible():
        return False, "El inicio de sesión con UNIR no está configurado en este servidor."
    try:
        resp = _oauth_pedir_token({
            "grant_type": "authorization_code",
            "redirect_uri": oauth_config()["redirect_uri"],
            "code": code,
        })
    except Exception as e:
        return False, f"UNIR rechazó el inicio de sesión: {e}"
    if not resp.get("access_token"):
        return False, "UNIR no devolvió un token de acceso."
    _guardar_tokens_oauth(username, resp)
    ok, msg, _ = sincronizar_canvas_estudiante(username)
    return ok, msg


def _token_vigente(username: str, cfg: Dict[str, Any]) -> str:
    """Devuelve el token a usar. En modo OAuth lo renueva automáticamente con el refresh token si va a caducar."""
    token = (cfg.get("_canvas_token_raw") or "").strip()
    if cfg.get("modo") != "oauth" or not cfg.get("_refresh_token"):
        return token
    expira = _parse_fecha(cfg.get("_access_expira"))
    if token and expira and expira - datetime.now() > timedelta(minutes=5):
        return token
    try:
        resp = _oauth_pedir_token({"grant_type": "refresh_token", "refresh_token": cfg["_refresh_token"]})
        if resp.get("access_token"):
            _guardar_tokens_oauth(username, resp)
            return resp["access_token"]
    except Exception:
        pass
    return token


def sincronizar_canvas_estudiante(
    username: str,
    canvas_token: Optional[str] = None,
    ics_url: Optional[str] = None,
    base_url: Optional[str] = None,
    token_expira: Optional[str] = None,
) -> Tuple[bool, str, Dict[str, Any]]:
    """
    Conecta o resincroniza el Campus UNIR (Canvas) de un estudiante concreto y guarda sus datos en su espacio privado.
    - Token (o OAuth): asignaturas, estado de entregas y notas. Si hay .ics, se fusionan también sus eventos.
    - Solo .ics: calendario personal (no caduca nunca).
    - Si el token ha caducado pero hay .ics, el calendario sigue actualizándose y se conservan las últimas notas.
    """
    cfg_file, sync_file = _archivos_canvas_usuario(username)
    cfg_actual = obtener_config_canvas_usuario(username)

    token_nuevo = bool(canvas_token and canvas_token.strip())
    if token_nuevo:
        token = canvas_token.strip()
    else:
        token = _token_vigente(username, cfg_actual)
        cfg_actual = obtener_config_canvas_usuario(username)  # puede haberse renovado (OAuth)
    feed_ics = (ics_url if ics_url is not None else cfg_actual.get("ics_url", "")).strip()
    campus_url = (base_url or cfg_actual.get("base_url") or DEFAULT_CANVAS_BASE_URL).strip().rstrip("/")
    if not campus_url.startswith("http"):
        campus_url = "https://" + campus_url

    ahora_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    modo_oauth = cfg_actual.get("modo") == "oauth" and not token_nuevo

    # Validar el .ics por adelantado (si se ha indicado) para poder usarlo como respaldo
    eventos_ics: List[Dict[str, Any]] = []
    error_ics = ""
    if feed_ics:
        ok_ics, error_ics, eventos_ics = _descargar_eventos_ics(feed_ics)
        if not ok_ics:
            eventos_ics = []

    # MODO 1: Token de API de Canvas / OAuth (Completo: Perfil, Cursos, Entregas, Calificaciones reales)
    if token:
        try:
            perfil = _canvas_api_get(campus_url, token, "/api/v1/users/self/profile")
        except urllib.error.HTTPError as e:
            if e.code == 401:
                if token_nuevo:
                    return False, "Ese token no es válido o ya ha caducado. Genera uno nuevo en Campus UNIR → Cuenta → Configuraciones → «+ Nuevo token de acceso».", {}
                # Token guardado caducado: marcarlo y seguir con el .ics si existe
                cfg_actual["token_caducado"] = True
                if feed_ics and not error_ics:
                    previo = _leer_sync_previo(sync_file)
                    cfg_actual.update({"conectado": True, "ics_url": feed_ics, "ultima_sincronizacion": ahora_str})
                    datos_sync = {
                        "ultima_sincronizacion": ahora_str,
                        "alumno": previo.get("alumno") or {"nombre": cfg_actual.get("alumno_nombre") or username, "email": ""},
                        "cursos": previo.get("cursos", []),
                        "entregas": eventos_ics,
                        "calificaciones_campus": previo.get("calificaciones_campus", []),
                    }
                    _guardar_cfg_y_sync(cfg_file, sync_file, cfg_actual, datos_sync)
                    return (
                        True,
                        f"Tu token ha caducado, pero el calendario se ha actualizado con tu enlace .ics ({len(eventos_ics)} eventos). "
                        "Tus PDFs y notas anteriores siguen guardados; renueva el token para traer los nuevos.",
                        datos_sync,
                    )
                _guardar_cfg_y_sync(cfg_file, sync_file, cfg_actual, None)
                return False, "Tu token del Campus ha caducado (UNIR los limita a 30 días). Tus PDFs y notas siguen guardados: genera uno nuevo y pégalo aquí para seguir sincronizando.", {}
            return False, f"Error HTTP {e.code} conectando con {campus_url}.", {}
        except Exception as e:
            return False, f"No se pudo conectar con {campus_url}: {e}", {}

        alumno_nombre = perfil.get("name") or perfil.get("short_name") or username
        alumno_email = perfil.get("primary_email") or perfil.get("login_id") or ""

        cursos_raw = []
        try:
            cursos_raw = _canvas_api_get(
                campus_url,
                token,
                "/api/v1/courses?enrollment_state=active&include[]=total_scores&per_page=50",
            )
        except Exception:
            cursos_raw = []

        cursos_limpios = []
        entregas_totales = []
        calificaciones_reales = []

        for c in cursos_raw if isinstance(cursos_raw, list) else []:
            if not isinstance(c, dict) or not c.get("id") or not c.get("name"):
                continue
            cid = c["id"]
            cname_raw = c.get("name", "")
            asig_norm = normalizar_nombre_asignatura(cname_raw) or cname_raw

            # Extraer nota actual en Canvas si está disponible en enrollments
            nota_curso = None
            for enr in c.get("enrollments", []) or []:
                if isinstance(enr, dict) and enr.get("computed_current_score") is not None:
                    nota_curso = enr.get("computed_current_score")
                    break

            cursos_limpios.append({
                "course_id": cid,
                "nombre_campus": cname_raw,
                "asignatura": asig_norm,
                "nota_actual_campus": nota_curso,
            })

            # Obtener tareas/PACs y calificaciones del estudiante en este curso
            try:
                tareas = _canvas_api_get(
                    campus_url,
                    token,
                    f"/api/v1/courses/{cid}/assignments?include[]=submission&per_page=50",
                )
            except Exception:
                tareas = []

            for t in tareas if isinstance(tareas, list) else []:
                if not isinstance(t, dict):
                    continue
                titulo_t = t.get("name") or "Actividad"
                due_at = t.get("due_at")
                puntos_max = t.get("points_possible")
                html_url = t.get("html_url") or f"{campus_url}/courses/{cid}/assignments"
                desc_html = t.get("description") or ""
                desc_txt = re.sub(r"<[^>]+>", " ", desc_html).strip()[:180]

                sub = t.get("submission") or {}
                workflow = sub.get("workflow_state") or "unsubmitted"
                score = sub.get("score")
                submitted_at = sub.get("submitted_at")
                entregada = bool(submitted_at) or workflow in ("submitted", "graded", "pending_review")
                calificada = workflow == "graded" and score is not None

                nota_sobre_10 = None
                if calificada and puntos_max and float(puntos_max) > 0:
                    nota_sobre_10 = round((float(score) / float(puntos_max)) * 10.0, 2)
                elif calificada and score is not None:
                    nota_sobre_10 = round(float(score), 2)

                if calificada:
                    calificaciones_reales.append({
                        "asignatura": asig_norm,
                        "titulo": titulo_t,
                        "score": score,
                        "puntos_max": puntos_max,
                        "nota_sobre_10": nota_sobre_10,
                        "fecha_calificacion": (sub.get("graded_at") or submitted_at or "")[:10],
                        "url": html_url,
                    })

                dias_restantes, plazo_texto = calcular_dias_restantes(due_at)
                if dias_restantes < -7 and not entregada:
                    continue

                urgencia = "critica" if (dias_restantes <= 2 and not entregada) else (
                    "alta" if (dias_restantes <= 7 and not entregada) else (
                        "media" if dias_restantes <= 15 else "baja"
                    )
                )

                entregas_totales.append({
                    "id": f"asig_{t.get('id')}",
                    "tipo": "tarea",
                    "titulo": titulo_t,
                    "asignatura": asig_norm,
                    "fecha_iso": due_at,
                    "dias_restantes": dias_restantes,
                    "plazo_texto": "✅ Entregada" if (entregada and not calificada) else (
                        f"🏆 {nota_sobre_10}/10" if calificada else plazo_texto
                    ),
                    "urgencia": "completada" if (entregada or calificada) else urgencia,
                    "puntos": puntos_max,
                    "url": html_url,
                    "descripcion": desc_txt,
                    "entregada": entregada,
                    "calificada": calificada,
                    "nota": nota_sobre_10,
                })

        if eventos_ics:
            entregas_totales = _fusionar_eventos_ics(entregas_totales, eventos_ics)
        entregas_totales.sort(key=lambda x: (1 if x.get("entregada") else 0, x["dias_restantes"] if x["dias_restantes"] >= 0 else 9999))

        # Fecha de vencimiento del token: real (API) > indicada por el alumno > estimación de 30 días
        if modo_oauth:
            expira_txt, estimada = None, False
        elif token_nuevo:
            expira_real = _detectar_caducidad_token(campus_url, token)
            if expira_real:
                expira_txt, estimada = expira_real, False
            elif token_expira and _parse_fecha(token_expira):
                expira_txt, estimada = token_expira, False
            else:
                expira_txt, estimada = (datetime.now() + timedelta(days=TOKEN_DIAS_MAX)).strftime("%Y-%m-%d"), True
        else:
            expira_txt = token_expira or cfg_actual.get("token_expira")
            estimada = False if token_expira else bool(cfg_actual.get("token_expira_estimada"))

        masked = cfg_actual.get("canvas_token_masked") if modo_oauth else (
            f"{token[:5]}...{token[-4:]}" if len(token) > 10 else "Token activo"
        )
        nueva_cfg = {
            **{k: v for k, v in cfg_actual.items() if k.startswith("_refresh") or k.startswith("_access")},
            "conectado": True,
            "modo": "oauth" if modo_oauth else "token",
            "base_url": campus_url,
            "_canvas_token_raw": token,
            "canvas_token_masked": masked,
            "token_expira": expira_txt,
            "token_expira_estimada": estimada,
            "token_caducado": False,
            "ics_url": feed_ics if not error_ics else cfg_actual.get("ics_url", ""),
            "alumno_nombre": alumno_nombre,
            "alumno_email": alumno_email,
            "ultima_sincronizacion": ahora_str,
            "ultima_sync_auto": cfg_actual.get("ultima_sync_auto"),
            "sync_auto": cfg_actual.get("sync_auto", True),
            "asignaturas_favoritas": cfg_actual.get("asignaturas_favoritas", []),
        }
        datos_sync = {
            "ultima_sincronizacion": ahora_str,
            "alumno": {"nombre": alumno_nombre, "email": alumno_email},
            "cursos": cursos_limpios,
            "entregas": entregas_totales,
            "calificaciones_campus": calificaciones_reales,
        }
        _guardar_cfg_y_sync(cfg_file, sync_file, nueva_cfg, datos_sync)

        aviso_ics = f" (Aviso: el enlace .ics no se pudo usar: {error_ics})" if error_ics else ""
        return (
            True,
            f"¡Campus UNIR conectado con éxito ({alumno_nombre})! {len(cursos_limpios)} asignaturas y {len(entregas_totales)} actividades sincronizadas.{aviso_ics}",
            datos_sync,
        )

    # MODO 2: Enlace de Calendario .ics de Canvas (no caduca)
    if feed_ics:
        if error_ics:
            return False, error_ics, {}
        asigs_detectadas = sorted(list({e["asignatura"] for e in eventos_ics if e.get("asignatura")}))

        nueva_cfg = {
            "conectado": True,
            "modo": "ics",
            "base_url": campus_url,
            "_canvas_token_raw": "",
            "canvas_token_masked": "",
            "token_expira": None,
            "token_expira_estimada": False,
            "token_caducado": False,
            "ics_url": feed_ics,
            "alumno_nombre": cfg_actual.get("alumno_nombre") or username,
            "alumno_email": "",
            "ultima_sincronizacion": ahora_str,
            "ultima_sync_auto": cfg_actual.get("ultima_sync_auto"),
            "sync_auto": cfg_actual.get("sync_auto", True),
            "asignaturas_favoritas": cfg_actual.get("asignaturas_favoritas", []),
        }
        datos_sync = {
            "ultima_sincronizacion": ahora_str,
            "alumno": {"nombre": nueva_cfg["alumno_nombre"], "email": ""},
            "cursos": [{"course_id": i, "nombre_campus": a, "asignatura": a, "nota_actual_campus": None} for i, a in enumerate(asigs_detectadas)],
            "entregas": eventos_ics,
            "calificaciones_campus": [],
        }
        _guardar_cfg_y_sync(cfg_file, sync_file, nueva_cfg, datos_sync)

        return (
            True,
            f"¡Calendario personal de Canvas conectado! {len(eventos_ics)} entregas y eventos sincronizados.",
            datos_sync,
        )

    return False, "Indica tu enlace de Calendario (.ics) y/o tu Token de Acceso de Canvas UNIR.", {}


def sincronizacion_automatica_todos(forzar: bool = False) -> Dict[str, Any]:
    """
    Recorre todas las cuentas con el Campus conectado y sincroniza calendario, notas y PDFs nuevos.
    Pensado para ejecutarse una vez cada noche desde el servidor (ver `iniciar_programador_sincronizacion`).
    Cada alumno se procesa con SU propio token/.ics y se guarda solo en SU espacio privado.
    """
    resumen = {"revisados": 0, "ok": 0, "caducados": 0, "errores": 0}
    hoy = datetime.now().strftime("%Y-%m-%d")
    for cfg_file in sorted(USUARIOS_ESPACIOS_DIR.glob("*/canvas_config.json")):
        username = cfg_file.parent.name
        try:
            cfg = json.loads(cfg_file.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not cfg.get("conectado") or cfg.get("sync_auto") is False:
            continue
        if not forzar and str(cfg.get("ultima_sync_auto") or "").startswith(hoy):
            continue
        resumen["revisados"] += 1
        try:
            ok, _msg, _ = sincronizar_canvas_estudiante(username)
            cfg2 = obtener_config_canvas_usuario(username)
            if ok and cfg2.get("_canvas_token_raw") and not cfg2.get("token_caducado"):
                sincronizar_materiales_canvas_estudiante(username)
                cfg2 = obtener_config_canvas_usuario(username)
            if cfg2.get("token_caducado"):
                resumen["caducados"] += 1
            resumen["ok" if ok else "errores"] += 1
            cfg2["ultima_sync_auto"] = datetime.now().strftime("%Y-%m-%d %H:%M")
            escribir_json_atomico(cfg_file, cfg2)
        except Exception:
            resumen["errores"] += 1
    return resumen


_programador_iniciado = False


def iniciar_programador_sincronizacion(hora_inicio: int = 3, hora_fin: int = 6) -> None:
    """
    Lanza (una sola vez) un hilo ligero que cada 30 min comprueba si toca la sincronización nocturna.
    Corre entre las 3:00 y las 6:00; si el servidor estuvo apagado a esa hora (p. ej. Render dormido),
    se recupera en cuanto pasen más de 36 h desde la última pasada.
    Desactivable con la variable de entorno NOVA_SYNC_AUTO=0.
    """
    global _programador_iniciado
    if _programador_iniciado or os.environ.get("NOVA_SYNC_AUTO", "0") != "1":
        return
    _programador_iniciado = True

    import threading
    import time

    marca = USUARIOS_ESPACIOS_DIR / ".ultima_sync_auto_global"

    def _bucle():
        time.sleep(90)  # no competir con el arranque del servidor
        while True:
            try:
                ahora = datetime.now()
                ultima = None
                if marca.exists():
                    ultima = _parse_fecha(marca.read_text(encoding="utf-8").strip())
                horas_desde = (ahora - ultima).total_seconds() / 3600 if ultima else 9999
                en_ventana = hora_inicio <= ahora.hour < hora_fin
                if (en_ventana and horas_desde > 12) or horas_desde > 36:
                    sincronizacion_automatica_todos()
                    marca.write_text(ahora.isoformat(timespec="seconds"), encoding="utf-8")
            except Exception:
                pass
            time.sleep(1800)

    threading.Thread(target=_bucle, name="nova-sync-campus", daemon=True).start()


_CACHE_CANVAS_SYNC: Dict[str, tuple[float, Dict[str, Any]]] = {}


def obtener_datos_canvas_estudiante(username: str) -> Dict[str, Any]:
    """Devuelve el estado de conexión y los datos sincronizados del Campus UNIR para este usuario."""
    cfg_file, sync_file = _archivos_canvas_usuario(username)
    cfg = obtener_config_canvas_usuario(username)
    cfg_publica = {k: v for k, v in cfg.items() if not k.startswith("_")}
    cfg_publica["token_estado"] = estado_token_canvas(cfg)
    cfg_publica["oauth_disponible"] = oauth_disponible()

    datos = {
        "config": cfg_publica,
        "cursos": [],
        "entregas": [],
        "calificaciones_campus": [],
    }
    if sync_file.exists():
        try:
            mtime = sync_file.stat().st_mtime
            cached = _CACHE_CANVAS_SYNC.get(username)
            if cached and cached[0] == mtime:
                raw = cached[1]
            else:
                raw = json.loads(sync_file.read_text(encoding="utf-8"))
                _CACHE_CANVAS_SYNC[username] = (mtime, raw)
            datos["cursos"] = raw.get("cursos", [])
            datos["entregas"] = raw.get("entregas", [])
            datos["calificaciones_campus"] = raw.get("calificaciones_campus", [])
        except Exception:
            pass
    return datos


def desconectar_canvas_estudiante(username: str) -> bool:
    cfg_file, sync_file = _archivos_canvas_usuario(username)
    cfg = obtener_config_canvas_usuario(username)
    favoritas = cfg.get("asignaturas_favoritas", [])
    nueva_cfg = {
        "conectado": False,
        "modo": None,
        "base_url": DEFAULT_CANVAS_BASE_URL,
        "canvas_token_masked": "",
        "token_expira": None,
        "token_expira_estimada": False,
        "token_caducado": False,
        "ics_url": "",
        "alumno_nombre": "",
        "alumno_email": "",
        "ultima_sincronizacion": None,
        "asignaturas_favoritas": favoritas,
    }
    escribir_json_atomico(cfg_file, nueva_cfg)
    if sync_file.exists():
        sync_file.unlink(missing_ok=True)
    return True


def _limpiar_nombre_pdf(nombre: str) -> str:
    limpio = re.sub(r'[\\/*?:"<>|]', "_", (nombre or "documento.pdf").strip())
    if not limpio.lower().endswith(".pdf"):
        limpio += ".pdf"
    return limpio


def sincronizar_materiales_canvas_estudiante(username: str) -> Tuple[bool, str, Dict[str, Any]]:
    """
    Descarga los PDFs de las asignaturas del estudiante usando su Token Personal de Canvas.
    - Guarda los documentos nuevos o exclusivos del alumno en su carpeta privada:
      `data/usuarios_espacios/<username>/materiales/<Asignatura>/`
    - Deduplicación inteligente: si el PDF ya existe en el repositorio base de la titulación,
      no lo vuelve a descargar para no consumir RAM ni disco en el servidor.
    """
    cfg = obtener_config_canvas_usuario(username)
    token = _token_vigente(username, cfg)
    campus_url = (cfg.get("base_url") or DEFAULT_CANVAS_BASE_URL).strip().rstrip("/")
    if not token:
        return (
            False,
            "Para descargar los PDFs de tus asignaturas necesitas añadir un Token de Acceso de Canvas en «Mi Campus» (el enlace .ics solo trae el calendario).",
            {},
        )
    if cfg.get("token_caducado"):
        return (
            False,
            "Tu token del Campus ha caducado. Tus PDFs ya descargados siguen aquí; para traer los nuevos, genera un token nuevo y pégalo en «Mi Campus».",
            {},
        )

    rutas_u = obtener_rutas_usuario(username)
    dir_mat_usuario = rutas_u["materiales"]

    try:
        cursos_raw = _canvas_api_get(
            campus_url,
            token,
            "/api/v1/courses?enrollment_state=active&per_page=50",
        )
    except urllib.error.HTTPError as e:
        if e.code == 401:
            cfg_file, _ = _archivos_canvas_usuario(username)
            cfg["token_caducado"] = True
            escribir_json_atomico(cfg_file, cfg)
            return (
                False,
                "Tu token del Campus ha caducado. Tus PDFs ya descargados siguen aquí; para traer los nuevos, genera un token nuevo y pégalo en «Mi Campus».",
                {},
            )
        return False, f"No se pudo consultar tus cursos en Canvas (HTTP {e.code}).", {}
    except Exception as e:
        return False, f"No se pudo consultar tus cursos en Canvas: {e}", {}

    descargados = 0
    omitidos_existentes = 0
    cursos_revisados = 0
    MAX_PDF_BYTES = 15 * 1024 * 1024  # Límite seguro de 15 MB por PDF para proteger RAM en Render

    for c in cursos_raw if isinstance(cursos_raw, list) else []:
        if not isinstance(c, dict) or not c.get("id") or not c.get("name"):
            continue
        cid = c["id"]
        cname_raw = c.get("name", "")
        asig_norm = normalizar_nombre_asignatura(cname_raw) or re.sub(r'[\\/*?:"<>|]', "", cname_raw).strip()
        if not asig_norm:
            continue
        cursos_revisados += 1

        archivos_candidatos: List[Dict[str, Any]] = []
        # 1. Intentar vía endpoint directo de files del curso
        try:
            files_list = _canvas_api_get(
                campus_url,
                token,
                f"/api/v1/courses/{cid}/files?content_types[]=application/pdf&per_page=50",
            )
            if isinstance(files_list, list):
                archivos_candidatos.extend([f for f in files_list if isinstance(f, dict)])
        except Exception:
            pass

        # 2. Intentar también vía Módulos del curso (muchos profesores en UNIR publican PDFs dentro de Módulos)
        try:
            modulos = _canvas_api_get(
                campus_url,
                token,
                f"/api/v1/courses/{cid}/modules?include[]=items&per_page=30",
            )
            for mod in modulos if isinstance(modulos, list) else []:
                for item in (mod.get("items") or []) if isinstance(mod, dict) else []:
                    if isinstance(item, dict) and item.get("type") == "File" and item.get("url"):
                        try:
                            # Consultar metadata del archivo desde su api url relativa
                            rel_api = item["url"].replace(campus_url, "")
                            f_meta = _canvas_api_get(campus_url, token, rel_api)
                            if isinstance(f_meta, dict) and str(f_meta.get("filename", "")).lower().endswith(".pdf"):
                                archivos_candidatos.append(f_meta)
                        except Exception:
                            pass
        except Exception:
            pass

        vistos_ids = set()
        for f_obj in archivos_candidatos:
            fid = f_obj.get("id")
            if fid in vistos_ids:
                continue
            vistos_ids.add(fid)

            nombre_pdf = _limpiar_nombre_pdf(f_obj.get("display_name") or f_obj.get("filename") or f"documento_{fid}.pdf")
            size_bytes = int(f_obj.get("size") or 0)
            download_url = f_obj.get("url")
            if not download_url or (size_bytes and size_bytes > MAX_PDF_BYTES):
                continue

            # Deduplicación: ¿existe ya en la biblioteca base de la asignatura (incluso como placeholder en cloud) o en la carpeta privada?
            ruta_base = MATERIALES_DIR / asig_norm / nombre_pdf
            ruta_privada = dir_mat_usuario / asig_norm / nombre_pdf
            if ruta_base.exists() or (ruta_privada.exists() and ruta_privada.stat().st_size > 0):
                omitidos_existentes += 1
                continue

            # Descargar por streaming en bloques de 64 KB para consumo nulo de RAM
            try:
                ruta_privada.parent.mkdir(parents=True, exist_ok=True)
                req = urllib.request.Request(
                    download_url,
                    headers={"Authorization": f"Bearer {token}", "User-Agent": "NOVA-Study-Hub/3.5"},
                )
                with urllib.request.urlopen(req, timeout=25) as resp, open(ruta_privada, "wb") as out_f:
                    total_leido = 0
                    while True:
                        chunk = resp.read(65536)
                        if not chunk:
                            break
                        total_leido += len(chunk)
                        if total_leido > MAX_PDF_BYTES:
                            break
                        out_f.write(chunk)
                if ruta_privada.exists() and ruta_privada.stat().st_size > 100:
                    descargados += 1
                    try:
                        from src.core.cloud_sync import respaldar_archivo_en_hf_async
                        respaldar_archivo_en_hf_async(ruta_privada)
                    except Exception:
                        pass
                else:
                    ruta_privada.unlink(missing_ok=True)
            except Exception:
                ruta_privada.unlink(missing_ok=True)

    return (
        True,
        f"Sincronización completada en {cursos_revisados} asignaturas: {descargados} PDF(s) nuevos añadidos a tu espacio privado ({omitidos_existentes} ya estaban al día).",
        {"descargados": descargados, "omitidos": omitidos_existentes, "cursos": cursos_revisados},
    )

