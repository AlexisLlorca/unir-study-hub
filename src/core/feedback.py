"""
Módulo de gestión de Feedback, Reporte de Errores y Propuestas de Mejora
para alumnos y administración de NOVA Study Hub.
Persistencia dual en SQLite (data/usuarios.db) y JSON (data/feedback.json).
"""

import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.core.config import DATA_DIR

DB_PATH = DATA_DIR / "usuarios.db"
FEEDBACK_JSON_PATH = DATA_DIR / "feedback.json"


def _obtener_conexion() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def inicializar_bd_feedback() -> None:
    """Crea la tabla feedback si no existe y restaura desde JSON si es necesario."""
    with _obtener_conexion() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS feedback (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL,
                nombre TEXT NOT NULL,
                tipo TEXT NOT NULL DEFAULT 'fallo',
                asignatura TEXT DEFAULT '',
                titulo TEXT NOT NULL,
                descripcion TEXT NOT NULL,
                estado TEXT NOT NULL DEFAULT 'pendiente',
                respuesta_admin TEXT DEFAULT '',
                creado_en TEXT NOT NULL,
                actualizado_en TEXT NOT NULL
            )
            """
        )
        conn.commit()
        cursor.execute("PRAGMA wal_checkpoint(FULL);")

    importar_feedback_desde_json()
    exportar_feedback_a_json()


def exportar_feedback_a_json() -> None:
    """Exporta todos los reportes a feedback.json para respaldo persistente en la nube."""
    try:
        with _obtener_conexion() as conn:
            cursor = conn.cursor()
            cursor.execute("PRAGMA wal_checkpoint(FULL);")
            cursor.execute(
                """
                SELECT id, username, nombre, tipo, asignatura, titulo, descripcion, estado, respuesta_admin, creado_en, actualizado_en
                FROM feedback
                ORDER BY id DESC
                """
            )
            filas = [dict(r) for r in cursor.fetchall()]

        FEEDBACK_JSON_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(FEEDBACK_JSON_PATH, "w", encoding="utf-8") as f:
            json.dump(filas, f, indent=2, ensure_ascii=False)
    except Exception:
        pass


def importar_feedback_desde_json() -> None:
    """Restaura los reportes desde feedback.json si la base de datos está vacía o reiniciada."""
    if not FEEDBACK_JSON_PATH.exists():
        try:
            from src.core.cloud_sync import _descargar_archivo_hf_directo
            _descargar_archivo_hf_directo("data/feedback.json", FEEDBACK_JSON_PATH)
        except Exception:
            pass

    if not FEEDBACK_JSON_PATH.exists():
        return

    try:
        with open(FEEDBACK_JSON_PATH, "r", encoding="utf-8") as f:
            items = json.load(f)
        if not isinstance(items, list):
            return

        with _obtener_conexion() as conn:
            cursor = conn.cursor()
            for it in items:
                cursor.execute("SELECT id FROM feedback WHERE id = ?", (it.get("id"),))
                if not cursor.fetchone():
                    cursor.execute(
                        """
                        INSERT INTO feedback (id, username, nombre, tipo, asignatura, titulo, descripcion, estado, respuesta_admin, creado_en, actualizado_en)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            it.get("id"),
                            it.get("username", "anonimo"),
                            it.get("nombre", "Alumno"),
                            it.get("tipo", "fallo"),
                            it.get("asignatura", ""),
                            it.get("titulo", "Sin título"),
                            it.get("descripcion", ""),
                            it.get("estado", "pendiente"),
                            it.get("respuesta_admin", ""),
                            it.get("creado_en", time.strftime("%Y-%m-%d %H:%M:%S")),
                            it.get("actualizado_en", time.strftime("%Y-%m-%d %H:%M:%S")),
                        ),
                    )
            conn.commit()
            cursor.execute("PRAGMA wal_checkpoint(FULL);")
    except Exception:
        pass


def _notificar_respaldo_cloud():
    try:
        exportar_feedback_a_json()
        from src.core.cloud_sync import respaldar_archivo_en_hf_async
        respaldar_archivo_en_hf_async(FEEDBACK_JSON_PATH)
        respaldar_archivo_en_hf_async(DB_PATH)
    except Exception:
        pass


def crear_reporte(
    username: str,
    nombre: str,
    tipo: str,
    titulo: str,
    descripcion: str,
    asignatura: str = "",
) -> Dict[str, Any]:
    """Crea una nueva incidencia o propuesta de mejora."""
    username = (username or "").strip()
    nombre = (nombre or "").strip() or username
    tipo = tipo if tipo in ("fallo", "mejora") else "fallo"
    titulo = (titulo or "").strip()
    descripcion = (descripcion or "").strip()
    asignatura = (asignatura or "").strip()

    if not titulo or len(titulo) < 3:
        raise ValueError("El título debe tener al menos 3 caracteres.")
    if not descripcion or len(descripcion) < 5:
        raise ValueError("La descripción debe tener al menos 5 caracteres.")

    ahora = time.strftime("%Y-%m-%d %H:%M:%S")

    with _obtener_conexion() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO feedback (username, nombre, tipo, asignatura, titulo, descripcion, estado, respuesta_admin, creado_en, actualizado_en)
            VALUES (?, ?, ?, ?, ?, ?, 'pendiente', '', ?, ?)
            """,
            (username, nombre, tipo, asignatura, titulo, descripcion, ahora, ahora),
        )
        reporte_id = cursor.lastrowid
        conn.commit()
        cursor.execute("PRAGMA wal_checkpoint(FULL);")

    _notificar_respaldo_cloud()

    return {
        "id": reporte_id,
        "username": username,
        "nombre": nombre,
        "tipo": tipo,
        "asignatura": asignatura,
        "titulo": titulo,
        "descripcion": descripcion,
        "estado": "pendiente",
        "respuesta_admin": "",
        "creado_en": ahora,
        "actualizado_en": ahora,
    }


def listar_reportes() -> List[Dict[str, Any]]:
    """Devuelve todos los reportes ordenados por fecha descendente."""
    with _obtener_conexion() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT id, username, nombre, tipo, asignatura, titulo, descripcion, estado, respuesta_admin, creado_en, actualizado_en
            FROM feedback
            ORDER BY id DESC
            """
        )
        return [dict(r) for r in cursor.fetchall()]


def actualizar_estado_reporte(reporte_id: int, estado: str, respuesta_admin: Optional[str] = None) -> bool:
    """Actualiza el estado y opcionalmente añade respuesta del administrador."""
    if estado not in ("pendiente", "en_proceso", "resuelto", "descartado"):
        raise ValueError("Estado no válido.")

    ahora = time.strftime("%Y-%m-%d %H:%M:%S")
    with _obtener_conexion() as conn:
        cursor = conn.cursor()
        if respuesta_admin is not None:
            cursor.execute(
                """
                UPDATE feedback
                SET estado = ?, respuesta_admin = ?, actualizado_en = ?
                WHERE id = ?
                """,
                (estado, respuesta_admin.strip(), ahora, reporte_id),
            )
        else:
            cursor.execute(
                """
                UPDATE feedback
                SET estado = ?, actualizado_en = ?
                WHERE id = ?
                """,
                (estado, ahora, reporte_id),
            )
        cambiados = cursor.rowcount
        conn.commit()
        cursor.execute("PRAGMA wal_checkpoint(FULL);")

    if cambiados > 0:
        _notificar_respaldo_cloud()
        return True
    return False


def eliminar_reporte(reporte_id: int) -> bool:
    """Elimina un reporte."""
    with _obtener_conexion() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM feedback WHERE id = ?", (reporte_id,))
        cambiados = cursor.rowcount
        conn.commit()
        cursor.execute("PRAGMA wal_checkpoint(FULL);")

    if cambiados > 0:
        _notificar_respaldo_cloud()
        return True
    return False


def contar_feedback_pendientes() -> int:
    """Cuenta cuántos reportes o propuestas están en estado 'pendiente'."""
    try:
        with _obtener_conexion() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM feedback WHERE estado = 'pendiente'")
            row = cursor.fetchone()
            return int(row[0]) if row else 0
    except Exception:
        return 0
