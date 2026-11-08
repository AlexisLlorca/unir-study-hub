"""
Sincronizador de Agenda, PACs y Calendario Académico de Canvas para UNIR Study Hub.
Extrae fechas límite de entrega, actividades individuales y grupales, y próximas clases en directo.
Genera alertas de plazos con cuenta atrás para el Dashboard de estudio.
"""

import os
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Dict, Any, Optional
from rich.table import Table

from src.core.config import (
    console,
    BASE_DIR,
    SESSION_STATE_FILE,
    UNIR_CAMPUS_URL,
    PROCESADO_DIR,
    ensure_directories,
    obtener_espacio_usuario,
    escribir_json_atomico,
)
from src.core.academic_structure import normalizar_nombre_asignatura

AGENDA_CACHE_FILE = PROCESADO_DIR / "agenda_cache.json"
AGENDA_BACKUP_FILE = BASE_DIR / "data" / "agenda_backup.json"


def calcular_dias_restantes(fecha_iso: Optional[str]) -> tuple[int, str]:
    """Calcula días restantes hasta la fecha límite y una etiqueta legible."""
    if not fecha_iso:
        return 999, "Sin fecha límite"
    try:
        # Parse ISO fecha (ej: 2026-10-12T21:59:59Z)
        limite = datetime.fromisoformat(fecha_iso.replace("Z", "+00:00"))
        ahora = datetime.now(timezone.utc)
        diff = limite - ahora
        total_secs = diff.total_seconds()
        dias_reales = int(total_secs // 86400)
        horas = int(diff.seconds // 3600)

        if total_secs < 0:
            return dias_reales, "Vencida"
        elif dias_reales == 0:
            return 0, f"¡Hoy! (en {horas}h)"
        elif dias_reales == 1:
            return 1, "Mañana"
        elif dias_reales <= 7:
            return dias_reales, f"En {dias_reales} días ({limite.strftime('%d/%m/%Y')})"
        else:
            return dias_reales, f"En {dias_reales} días ({limite.strftime('%d/%m/%Y')})"
    except Exception:
        return 999, "Fecha no disponible"


def sincronizar_agenda_canvas() -> List[Dict[str, Any]]:
    """
    Sincroniza el calendario completo DAM 2026-2027 (302 ítems de Clases, Temas, Tests y Actividades PAC)
    y lo guarda en caché local para acceso instantáneo desde el servidor web.
    """
    ensure_directories()
    console.print("[cyan]☁️ Sincronizando calendario oficial DAM 2026-2027 (Clases + Temas + Tests + Entregas PAC)...[/cyan]")
    sincronizar_calendario_notion_dam()
    return cargar_agenda_cache()

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        console.print("[yellow]⚠ Playwright no instalado. Usando agenda en caché.[/yellow]")
        return cargar_agenda_cache()

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                storage_state=str(SESSION_STATE_FILE),
                viewport={"width": 1280, "height": 720},
                user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
            )
            page = context.new_page()
            page.goto(UNIR_CAMPUS_URL, wait_until="domcontentloaded", timeout=20000)
            page.wait_for_timeout(1200)

            if "login" in page.url.lower():
                console.print("[yellow]⚠ La sesión de Canvas ha caducado.[/yellow]")
                browser.close()
                return cargar_agenda_cache()

            # Consultar upcoming_events y calendario
            datos_raw = page.evaluate("""async () => {
                let items = [];
                try {
                    const res = await fetch('/api/v1/users/self/upcoming_events');
                    if (res.ok) {
                        const data = await res.json();
                        if (Array.isArray(data)) items = items.concat(data);
                    }
                } catch(e) {}

                try {
                    // Consultar calendario de próximos 45 días
                    const hoy = new Date().toISOString().split('T')[0];
                    const en45 = new Date(Date.now() + 45*86400000).toISOString().split('T')[0];
                    const resCal = await fetch(`/api/v1/calendar_events?start_date=${hoy}&end_date=${en45}&per_page=50`);
                    if (resCal.ok) {
                        const dataCal = await resCal.json();
                        if (Array.isArray(dataCal)) items = items.concat(dataCal);
                    }
                } catch(e) {}

                return items;
            }""")
            browser.close()

            # Procesar y desduplicar eventos
            vistos = set()
            for ev in datos_raw:
                ev_id = str(ev.get("id"))
                if ev_id in vistos:
                    continue
                vistos.add(ev_id)

                tipo = "tarea" if (ev.get("type") == "assignment" or "assignment" in ev) else "clase_directo"
                asig_raw = ev.get("context_name") or ""
                asignatura = normalizar_nombre_asignatura(asig_raw)

                asig_obj = ev.get("assignment", {})
                titulo = asig_obj.get("name") or ev.get("title") or "Evento académico"
                fecha_iso = asig_obj.get("due_at") or ev.get("start_at") or ev.get("end_at")
                url_html = ev.get("html_url") or asig_obj.get("html_url") or ""
                puntos = asig_obj.get("points_possible")

                desc_html = asig_obj.get("description") or ev.get("description") or ""
                desc_texto = re.sub(r"<[^>]+>", " ", desc_html).strip()
                desc_resumida = (desc_texto[:180] + "...") if len(desc_texto) > 180 else desc_texto

                dias_restantes, plazo_texto = calcular_dias_restantes(fecha_iso)

                if dias_restantes < 0:
                    urgencia = "vencida"
                elif dias_restantes <= 2:
                    urgencia = "critica"
                elif dias_restantes <= 7:
                    urgencia = "alta"
                elif dias_restantes <= 15:
                    urgencia = "media"
                else:
                    urgencia = "baja"

                eventos_limpios.append({
                    "id": ev_id,
                    "tipo": tipo,
                    "titulo": titulo,
                    "asignatura": asignatura,
                    "fecha_iso": fecha_iso,
                    "dias_restantes": dias_restantes,
                    "plazo_texto": plazo_texto,
                    "urgencia": urgencia,
                    "puntos": puntos,
                    "url": url_html,
                    "descripcion": desc_resumida,
                })

            # Ordenar por fecha más próxima (las más urgentes primero)
            eventos_limpios.sort(key=lambda x: (x["dias_restantes"] if x["dias_restantes"] >= 0 else 9999))

            # Guardar en cache local y en backup versionado
            raw_json = json.dumps(eventos_limpios, indent=2, ensure_ascii=False)
            AGENDA_CACHE_FILE.write_text(raw_json, encoding="utf-8")
            try:
                AGENDA_BACKUP_FILE.write_text(raw_json, encoding="utf-8")
            except Exception:
                pass

            # Respaldar en Hugging Face en segundo plano
            try:
                from src.core.cloud_sync import respaldar_archivo_en_hf_async
                respaldar_archivo_en_hf_async(AGENDA_CACHE_FILE)
            except Exception:
                pass

            console.print(f"[bold green]✔ Sincronizados {len(eventos_limpios)} eventos y entregas de Canvas.[/bold green]")
            return cargar_agenda_cache()

    except Exception as e:
        console.print(f"[red]Error al sincronizar agenda de Canvas: {e}[/red]")
        return cargar_agenda_cache()


def sincronizar_calendario_notion_dam() -> List[Dict[str, Any]]:
    """
    Sincroniza las 132+ sesiones de clase en directo, tutorías y entregas PAC oficiales
    del calendario DAM 2026-2027 (Notion / UNIR) y las guarda en caché.
    Funciona tanto en PC local como en Render Cloud sin requerir navegador headless.
    """
    import urllib.request
    SPACE_ID = "5d60eef0-9028-8104-a452-0003a24f1af7"
    headers = {"Content-Type": "application/json", "User-Agent": "NOVA-Study-Hub/3.5"}

    def _post_notion(payload: dict) -> dict:
        req = urllib.request.Request(
            "https://phrygian-title-f6d.notion.site/api/v3/queryCollection",
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))

    try:
        asig_data = _post_notion({
            "collection": {"id": "bc9436e7-6188-4fb6-8497-da6c403ddcc0", "spaceId": SPACE_ID},
            "collectionView": {"id": "3e20eef0-9028-8094-9ce1-000c96731d1a", "spaceId": SPACE_ID},
            "loader": {"type": "reducer", "reducers": {"collection_group_results": {"type": "results", "limit": 100}}, "searchQuery": "", "userTimeZone": "Europe/Madrid"},
        })
        asig_blocks = asig_data.get("recordMap", {}).get("block", {})
        asig_map = {}
        nombres_completos_limpios = {
            "acceso a datos": "Acceso a Datos",
            "arquitectura en la nube": "Arquitecturas en la Nube",
            "programacion de servicios y procesos": "Programación de Servicios y Procesos",
            "programacion multimedia y dispositivos moviles": "Programación Multimedia y Dispositivos Móviles",
            "sistemas de gestion empresarial": "Sistemas de Gestión Empresarial",
            "desarrollo de interfaces": "Desarrollo de Interfaces",
            "ingles profesional": "Inglés Profesional",
            "digitalizacion aplicada a sectores productivos": "Digitalización Aplicada a los Sectores Productivos",
            "sostenibilidad aplicada al sistema productivo": "Sostenibilidad Aplicada al Sistema Productivo",
            "fase de formacion en empresa": "Fase de Formación en Empresa (FFE)",
            "proyecto intermodular": "Proyecto Intermodular DAM",
        }
        for bid, b in asig_blocks.items():
            val = b.get("value", {}).get("value", {})
            props = val.get("properties", {})
            t = "".join([x[0] for x in props.get("title", []) if isinstance(x, list) and x]).strip()
            if t:
                asig_map[bid] = nombres_completos_limpios.get(t.lower(), t)

        cal_data = _post_notion({
            "collection": {"id": "df31319d-29ea-4407-8989-fbb783963692", "spaceId": SPACE_ID},
            "collectionView": {"id": "3e30eef0-9028-8070-bbb7-000c957a88b4", "spaceId": SPACE_ID},
            "loader": {"type": "reducer", "reducers": {"collection_group_results": {"type": "results", "limit": 1000}}, "searchQuery": "", "userTimeZone": "Europe/Madrid"},
        })
        block_ids = cal_data.get("result", {}).get("reducerResults", {}).get("collection_group_results", {}).get("blockIds", [])
        cal_blocks = cal_data.get("recordMap", {}).get("block", {})

        eventos = []
        for bid in block_ids:
            val = cal_blocks.get(bid, {}).get("value", {}).get("value", {})
            props = val.get("properties", {})
            tipo_notion = "".join([t[0] for t in props.get("kcol", []) if isinstance(t, list) and t]).strip()
            # El calendario oficial solo debe contener las Clases en directo y las Entregas reales (PACs/Proyectos).
            # Los temas teóricos y tests no deben saturar el calendario salvo que el usuario los añada manualmente.
            if tipo_notion not in ("Clase", "Actividad", "Proyecto", "FFE", "Formulario"):
                continue

            title = "".join([t[0] for t in props.get("title", []) if isinstance(t, list) and t]).strip()
            sub = "".join([t[0] for t in props.get("cWR{", []) if isinstance(t, list) and t]).strip()
            pts_raw = "".join([t[0] for t in props.get("Gmgv", []) if isinstance(t, list) and t]).strip()
            url = "".join([t[0] for t in props.get(":OtF", []) if isinstance(t, list) and t]).strip()
            rel = props.get("vz=s", [])
            asig_id = rel[0][1][0][1] if (rel and len(rel[0]) > 1 and rel[0][1]) else ""
            asig = asig_map.get(asig_id, "General")

            fecha = props.get("Q{yH", [])
            f_obj = fecha[0][1][0][1] if (fecha and len(fecha[0]) > 1 and fecha[0][1]) else {}
            start_date = f_obj.get("start_date") or ""
            start_time = f_obj.get("start_time") or ("23:59" if tipo_notion != "Clase" else "19:00")
            end_time = f_obj.get("end_time") or ""
            fecha_iso = f"{start_date}T{start_time}:00Z" if start_date else ""

            es_entrega = tipo_notion in ("Actividad", "Proyecto", "FFE", "Formulario")
            es_clase = tipo_notion == "Clase"
            es_tema = tipo_notion == "Tema"
            estado_default = "No vista" if es_clase else ("No entregado" if es_entrega else "No hecho")

            dias_restantes, plazo_texto = calcular_dias_restantes(fecha_iso) if fecha_iso else (999, "Sin fecha")
            urgencia = "vencida" if dias_restantes < 0 else ("critica" if dias_restantes <= 2 else ("alta" if dias_restantes <= 7 else ("media" if dias_restantes <= 15 else "baja")))

            horario_str = f"{start_time} - {end_time}" if end_time else start_time
            descripcion = sub if sub else (f"Sesión en directo ({horario_str})" if es_clase else f"{tipo_notion} de {asig}")
            tipo_interno = "tarea" if es_entrega else ("clase_directo" if es_clase else ("tema" if es_tema else "test_campus"))

            eventos.append({
                "id": f"notion_{bid.replace('-', '')}",
                "tipo": tipo_interno,
                "categoria": tipo_notion,
                "titulo": title,
                "asignatura": asig,
                "fecha_iso": fecha_iso,
                "fecha_dia": start_date,
                "hora_inicio": start_time if es_clase else "23:59",
                "hora_fin": end_time,
                "dias_restantes": dias_restantes,
                "plazo_texto": plazo_texto,
                "urgencia": urgencia,
                "estado": estado_default,
                "puntos": float(pts_raw) if pts_raw.replace(".", "", 1).isdigit() else None,
                "nota_personal": None,
                "comentario": "",
                "url": url or "https://campusfp.unir.net",
                "descripcion": descripcion,
            })

        if eventos:
            eventos.sort(key=lambda x: (x["fecha_dia"], x["hora_inicio"]))
            raw_json = json.dumps(eventos, indent=2, ensure_ascii=False)
            AGENDA_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
            AGENDA_CACHE_FILE.write_text(raw_json, encoding="utf-8")
            try:
                AGENDA_BACKUP_FILE.write_text(raw_json, encoding="utf-8")
            except Exception:
                pass
            try:
                from src.core.cloud_sync import respaldar_archivo_en_hf_async
                respaldar_archivo_en_hf_async(AGENDA_CACHE_FILE)
            except Exception:
                pass
            return eventos
    except Exception as e:
        console.print(f"[yellow]⚠ No se pudo sincronizar calendario Notion en directo: {e}[/yellow]")
    return []


def cargar_agenda_cache() -> List[Dict[str, Any]]:
    """Carga todos los eventos del calendario (clases y entregas) y recalcula dinámicamente los días restantes."""
    items = []
    archivo_origen = None

    if AGENDA_BACKUP_FILE.exists() and AGENDA_BACKUP_FILE.stat().st_size > 100:
        archivo_origen = AGENDA_BACKUP_FILE
    elif AGENDA_CACHE_FILE.exists() and AGENDA_CACHE_FILE.stat().st_size > 100:
        archivo_origen = AGENDA_CACHE_FILE

    if archivo_origen:
        try:
            items = json.loads(archivo_origen.read_text(encoding="utf-8"))
        except Exception:
            items = []

    if not items or len(items) < 50:
        items = sincronizar_calendario_notion_dam()

    if items:
        actualizados = []
        for ev in items:
            fecha_iso = ev.get("fecha_iso") or ""
            fecha_dia = ev.get("fecha_dia") or (fecha_iso[:10] if len(fecha_iso) >= 10 else "")
            hora_inicio = ev.get("hora_inicio") or (fecha_iso[11:16] if len(fecha_iso) >= 16 else "23:59")
            dias_restantes, plazo_texto = calcular_dias_restantes(fecha_iso)

            if dias_restantes < 0:
                urgencia = "vencida"
            elif dias_restantes <= 2:
                urgencia = "critica"
            elif dias_restantes <= 7:
                urgencia = "alta"
            elif dias_restantes <= 15:
                urgencia = "media"
            else:
                urgencia = "baja"

            ev_copia = dict(ev)
            ev_copia["fecha_dia"] = fecha_dia
            ev_copia["hora_inicio"] = hora_inicio
            ev_copia["dias_restantes"] = dias_restantes
            if not ev_copia.get("entregada") and not ev_copia.get("calificada"):
                ev_copia["plazo_texto"] = plazo_texto
                ev_copia["urgencia"] = urgencia
            actualizados.append(ev_copia)

        # Ordenar: futuros/hoy primero por cercanía, y luego pasados por fecha
        actualizados.sort(key=lambda x: (0 if x["dias_restantes"] >= 0 else 1, x["dias_restantes"] if x["dias_restantes"] >= 0 else -x["dias_restantes"], x.get("hora_inicio", "00:00")))
        return actualizados

    return []


def _archivo_organizador_usuario(username: str) -> Path:
    return obtener_espacio_usuario(username) / "organizador_notion.json"


def obtener_organizador_usuario(username: str) -> Dict[str, Any]:
    """
    Devuelve las personalizaciones del calendario/to-do del estudiante:
    - overrides: { item_id: { "estado": ..., "fecha_dia": ..., "nota_personal": ..., "comentario": ... } }
    - items_propios: [ { id, tipo, categoria, titulo, asignatura, fecha_dia, hora_inicio, estado, ... } ]
    """
    f = _archivo_organizador_usuario(username)
    if f.exists():
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                data.setdefault("overrides", {})
                data.setdefault("items_propios", [])
                return data
        except Exception:
            pass
    return {"overrides": {}, "items_propios": []}


def actualizar_item_organizador_usuario(username: str, item_id: str, cambios: Dict[str, Any]) -> Dict[str, Any]:
    """Guarda el estado, fecha movida, nota o comentario personal de un ítem del calendario para este estudiante."""
    f = _archivo_organizador_usuario(username)
    org = obtener_organizador_usuario(username)

    # Comprobar si es un ítem creado por el propio usuario
    actualizado_propio = False
    for it in org["items_propios"]:
        if it.get("id") == item_id:
            for k in ("estado", "fecha_dia", "nota_personal", "comentario", "titulo", "hora_inicio", "asignatura", "categoria", "recordatorio"):
                if k in cambios and cambios[k] is not None:
                    it[k] = cambios[k]
            actualizado_propio = True
            break

    if not actualizado_propio:
        ov = org["overrides"].get(item_id, {})
        for k in ("estado", "fecha_dia", "nota_personal", "comentario", "titulo", "asignatura", "categoria", "hora_inicio", "recordatorio", "oculto"):
            if k in cambios and cambios[k] is not None:
                ov[k] = cambios[k]
        org["overrides"][item_id] = ov

    escribir_json_atomico(f, org)
    try:
        from src.core.cloud_sync import respaldar_archivo_en_hf_async
        respaldar_archivo_en_hf_async(f)
    except Exception:
        pass
    return org


def crear_item_organizador_usuario(username: str, nuevo: Dict[str, Any]) -> Dict[str, Any]:
    """Permite al estudiante añadir un Tema, Tarea, Cita o Recordatorio propio a su calendario personal."""
    import secrets
    f = _archivo_organizador_usuario(username)
    org = obtener_organizador_usuario(username)
    cat = nuevo.get("categoria") or "Tema"
    es_entrega = cat in ("Actividad", "Entrega", "Práctica", "Examen", "Proyecto", "FFE", "Formulario")
    es_clase = cat in ("Clase", "Tutoría")
    es_cita = cat in ("Cita", "Recordatorio", "Otro")
    tipo_interno = "tarea" if es_entrega else ("clase_directo" if es_clase else "tema")
    fecha_dia = (nuevo.get("fecha_dia") or datetime.now().strftime("%Y-%m-%d"))[:10]
    hora_ini = (nuevo.get("hora_inicio") or "").strip() or ("19:00" if es_clase else ("10:00" if es_cita else "23:59"))
    rec_val = nuevo.get("recordatorio")
    if rec_val is None:
        rec_val = True if (es_cita or cat == "Examen") else False

    item = {
        "id": f"user_{secrets.token_hex(4)}",
        "es_propio": True,
        "tipo": tipo_interno,
        "categoria": cat,
        "titulo": (nuevo.get("titulo") or "Nuevo evento").strip()[:120],
        "asignatura": (nuevo.get("asignatura") or ("Personal / General" if es_cita else "General")).strip(),
        "fecha_iso": f"{fecha_dia}T{hora_ini}:00Z",
        "fecha_dia": fecha_dia,
        "hora_inicio": hora_ini,
        "hora_fin": nuevo.get("hora_fin") or "",
        "estado": nuevo.get("estado") or ("No vista" if es_clase else ("No entregado" if es_entrega else "Pendiente de empezar")),
        "recordatorio": bool(rec_val),
        "puntos": nuevo.get("puntos"),
        "nota_personal": nuevo.get("nota_personal"),
        "comentario": (nuevo.get("comentario") or "").strip(),
        "url": nuevo.get("url") or "https://campusfp.unir.net",
        "descripcion": (nuevo.get("descripcion") or f"{cat} personal").strip(),
    }
    org["items_propios"].append(item)
    escribir_json_atomico(f, org)
    try:
        from src.core.cloud_sync import respaldar_archivo_en_hf_async
        respaldar_archivo_en_hf_async(f)
    except Exception:
        pass
    return item


def eliminar_item_organizador_usuario(username: str, item_id: str) -> bool:
    """Elimina un ítem propio creado por el estudiante, o lo oculta de su calendario personal si es del temario base."""
    f = _archivo_organizador_usuario(username)
    org = obtener_organizador_usuario(username)
    antes = len(org["items_propios"])
    org["items_propios"] = [x for x in org["items_propios"] if x.get("id") != item_id]
    if len(org["items_propios"]) == antes:
        ov = org["overrides"].get(item_id, {})
        ov["oculto"] = True
        org["overrides"][item_id] = ov
    escribir_json_atomico(f, org)
    try:
        from src.core.cloud_sync import respaldar_archivo_en_hf_async
        respaldar_archivo_en_hf_async(f)
    except Exception:
        pass
    return True


def importar_calendario_ics_usuario(
    username: str,
    ics_texto: str,
    asignatura_por_defecto: str = "General",
) -> Dict[str, Any]:
    """
    Parsea un archivo de calendario (.ics) procedente de Google Calendar, Canvas,
    Outlook o Apple Calendar e importa sus eventos a los items propios del estudiante.
    Evita duplicar eventos existentes en la agenda oficial o en el organizador.
    """
    import secrets
    if not ics_texto or "BEGIN:VCALENDAR" not in ics_texto:
        return {
            "status": "error",
            "mensaje": "El archivo proporcionado no contiene un formato de calendario iCal (.ics) válido.",
            "importados": 0,
            "omitidos": 0,
            "total": 0,
        }

    f = _archivo_organizador_usuario(username)
    org = obtener_organizador_usuario(username)
    items_actuales = org.get("items_propios", [])

    # Obtener eventos base oficiales para comparar duplicados
    base_eventos = []
    if AGENDA_CACHE_FILE.exists():
        try:
            base_eventos = json.loads(AGENDA_CACHE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass

    # Claves existentes para evitar duplicados
    def _clave_evento(asig: str, f_dia: str, tit: str) -> tuple:
        a_limpia = re.sub(r"[^a-zA-Z0-9áéíóúÁÉÍÓÚñÑ]", "", asig or "").lower()
        t_limpio = re.sub(r"[^a-zA-Z0-9áéíóúÁÉÍÓÚñÑ]", "", tit or "").lower()
        return (a_limpia[:15], (f_dia or "")[:10], t_limpio[:25])

    existentes = set()
    uids_existentes = set()

    for ev in base_eventos + items_actuales:
        f_dia = ev.get("fecha_dia") or (ev.get("fecha_iso") or "")[:10]
        asig = ev.get("asignatura") or ""
        tit = ev.get("titulo") or ""
        existentes.add(_clave_evento(asig, f_dia, tit))
        if ev.get("uid_ics"):
            uids_existentes.add(str(ev["uid_ics"]).strip())
        if ev.get("id"):
            uids_existentes.add(str(ev["id"]).strip())

    # Extraer bloques VEVENT
    bloques = re.findall(r"BEGIN:VEVENT(.*?)END:VEVENT", ics_texto, re.DOTALL)
    importados = 0
    omitidos = 0

    for idx, bloque in enumerate(bloques):
        # Unfold lines RFC 5545
        limpio = re.sub(r"\r?\n[ \t]", "", bloque)
        uid_m = re.search(r"^UID:(.*)$", limpio, re.MULTILINE)
        sum_m = re.search(r"^SUMMARY:(.*)$", limpio, re.MULTILINE)
        dt_m = re.search(r"^DTSTART(?:;[^:]*)?:(\d{8}(?:T\d{6}Z?)?)", limpio, re.MULTILINE)
        end_m = re.search(r"^DTEND(?:;[^:]*)?:(\d{8}(?:T\d{6}Z?)?)", limpio, re.MULTILINE)
        url_m = re.search(r"^URL:(.*)$", limpio, re.MULTILINE)
        desc_m = re.search(r"^DESCRIPTION:(.*)$", limpio, re.MULTILINE)
        loc_m = re.search(r"^LOCATION:(.*)$", limpio, re.MULTILINE)

        uid_val = uid_m.group(1).strip() if uid_m else f"ics_{secrets.token_hex(4)}_{idx}"
        if uid_val in uids_existentes:
            omitidos += 1
            continue

        summary_raw = (sum_m.group(1).strip() if sum_m else "Evento importado").replace("\\,", ",").replace("\\;", ";")
        dt_raw = (dt_m or end_m).group(1).strip() if (dt_m or end_m) else ""
        if len(dt_raw) < 8:
            continue

        fecha_dia = f"{dt_raw[:4]}-{dt_raw[4:6]}-{dt_raw[6:8]}"
        hora_ini = f"{dt_raw[9:11]}:{dt_raw[11:13]}" if "T" in dt_raw and len(dt_raw) >= 13 else ""
        hora_fin = ""
        if end_m:
            end_raw = end_m.group(1).strip()
            if "T" in end_raw and len(end_raw) >= 13:
                hora_fin = f"{end_raw[9:11]}:{end_raw[11:13]}"

        # Extraer asignatura del summary tipo "Título [Asignatura]" o de título
        titulo = summary_raw
        asig_detectada = None
        m_corchete = re.search(r"^(.*?)\s*\[([^\]]+)\]\s*$", summary_raw)
        if m_corchete:
            titulo = m_corchete.group(1).strip()
            asig_detectada = m_corchete.group(2).strip()

        if asig_detectada:
            asig_final = normalizar_nombre_asignatura(asig_detectada) or asig_detectada
        else:
            asig_final = normalizar_nombre_asignatura(titulo) or asignatura_por_defecto or "General"

        # Categoría
        tit_lower = titulo.lower()
        if any(k in tit_lower for k in ("actividad", "pac", "entrega", "práctica", "practica", "proyecto", "ffe")):
            cat = "Actividad"
            tipo_interno = "tarea"
        elif any(k in tit_lower for k in ("examen", "test", "autoevaluación", "cuestionario")):
            cat = "Examen"
            tipo_interno = "tarea"
        elif any(k in tit_lower for k in ("clase", "tutoría", "tutoria", "sesión", "sesion", "webinar", "directo")):
            cat = "Clase"
            tipo_interno = "clase_directo"
        elif any(k in tit_lower for k in ("cita", "médico", "medico", "reunión", "reunion", "entrevista", "recordatorio")):
            cat = "Cita"
            tipo_interno = "tema"
        else:
            cat = "Tema"
            tipo_interno = "tema"

        if not hora_ini:
            hora_ini = "19:00" if tipo_interno == "clase_directo" else ("10:00" if cat == "Cita" else "23:59")

        # Comprobar clave duplicada
        clave = _clave_evento(asig_final, fecha_dia, titulo)
        if clave in existentes:
            omitidos += 1
            continue

        existentes.add(clave)
        uids_existentes.add(uid_val)

        desc_txt = (desc_m.group(1).strip() if desc_m else "").replace("\\n", " ").replace("\\,", ",")
        desc_txt = re.sub(r"<[^>]+>", " ", desc_txt).strip()[:200]
        if loc_m and loc_m.group(1).strip():
            loc_val = loc_m.group(1).strip().replace("\\,", ",")
            desc_txt = f"{loc_val} • {desc_txt}".strip(" •")

        nuevo_item = {
            "id": f"ics_{secrets.token_hex(4)}",
            "uid_ics": uid_val,
            "es_propio": True,
            "origen": "ics_importado",
            "tipo": tipo_interno,
            "categoria": cat,
            "titulo": titulo[:120],
            "asignatura": asig_final,
            "fecha_iso": f"{fecha_dia}T{hora_ini}:00Z",
            "fecha_dia": fecha_dia,
            "hora_inicio": hora_ini,
            "hora_fin": hora_fin,
            "estado": "No vista" if tipo_interno == "clase_directo" else ("No entregado" if tipo_interno == "tarea" else "Pendiente de empezar"),
            "recordatorio": (cat in ("Cita", "Examen")),
            "url": (url_m.group(1).strip() if url_m else "https://campusfp.unir.net"),
            "descripcion": desc_txt or f"Importado de calendario .ics ({cat})",
        }

        items_actuales.append(nuevo_item)
        importados += 1

    org["items_propios"] = items_actuales
    escribir_json_atomico(f, org)
    try:
        from src.core.cloud_sync import respaldar_archivo_en_hf_async
        respaldar_archivo_en_hf_async(f)
    except Exception:
        pass

    mensaje = f"¡Se han importado {importados} eventos nuevos a tu calendario!"
    if omitidos > 0:
        mensaje += f" ({omitidos} eventos duplicados fueron omitidos automáticamente)."

    return {
        "status": "ok",
        "mensaje": mensaje,
        "importados": importados,
        "omitidos": omitidos,
        "total": len(bloques),
    }


