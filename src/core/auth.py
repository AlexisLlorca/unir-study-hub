"""
Sistema de gestión de usuarios, roles, sesiones y autenticación para UNIR Study Hub.
Utiliza SQLite estándar, contraseñas protegidas con PBKDF2-HMAC-SHA256 (100k iteraciones)
y respaldo automático al dataset de Hugging Face.
"""

import hashlib
import hmac
import os
import re
import secrets
import sqlite3
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from src.core.config import DATA_DIR

DB_PATH = DATA_DIR / "usuarios.db"


def _obtener_conexion() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def _notificar_respaldo_nube():
    try:
        from src.core.cloud_sync import respaldar_archivo_en_hf_async
        respaldar_archivo_en_hf_async(DB_PATH)
    except Exception:
        pass


def inicializar_bd_usuarios() -> None:
    """Crea las tablas de usuarios y sesiones si no existen, y asegura el usuario administrador."""
    with _obtener_conexion() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS usuarios (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                nombre TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                salt TEXT NOT NULL,
                rol TEXT NOT NULL DEFAULT 'estudiante',
                activo INTEGER NOT NULL DEFAULT 0,
                creado_en TEXT NOT NULL,
                ultimo_acceso TEXT
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS sesiones (
                token TEXT PRIMARY KEY,
                username TEXT NOT NULL,
                creado_en REAL NOT NULL,
                expira_en REAL NOT NULL
            )
            """
        )
        conn.commit()

        # Si no hay ningún usuario creado, crear el usuario admin inicial
        cursor.execute("SELECT COUNT(*) FROM usuarios")
        count = cursor.fetchone()[0]
        if count == 0:
            admin_pass = os.getenv("HUB_ADMIN_PASSWORD", "").strip() or "AdminUNIR2026!"
            salt = secrets.token_hex(16)
            pwd_hash = _hash_password(admin_pass, salt)
            ahora = time.strftime("%Y-%m-%d %H:%M:%S")
            cursor.execute(
                """
                INSERT INTO usuarios (username, nombre, password_hash, salt, rol, activo, creado_en)
                VALUES (?, ?, ?, ?, 'admin', 1, ?)
                """,
                ("admin", "Alex (Administrador)", pwd_hash, salt, ahora),
            )
            conn.commit()
            _notificar_respaldo_nube()


def _hash_password(password: str, salt: str) -> str:
    """Genera hash criptográfico seguro PBKDF2-HMAC-SHA256."""
    return hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        100_000
    ).hex()


def verificar_password(password: str, salt: str, password_hash: str) -> bool:
    """Compara en tiempo constante la contraseña introducida."""
    calculado = _hash_password(password, salt)
    return hmac.compare_digest(calculado, password_hash)


def registrar_usuario(
    username: str,
    nombre: str,
    password: str,
    rol: str = "estudiante",
    activo: int = 0
) -> Tuple[bool, str]:
    """
    Registra un nuevo usuario en la base de datos.
    Por defecto queda con activo=0 (pendiente de aprobación por el admin).
    """
    username = (username or "").strip().lower()
    nombre = (nombre or "").strip()
    password = (password or "").strip()

    if not username or not re.match(r"^[a-z0-9_.-]{3,30}$", username):
        return False, "El nombre de usuario debe tener entre 3 y 30 caracteres (solo letras, números, puntos y guiones)."

    if not nombre or len(nombre) < 2:
        return False, "El nombre debe tener al menos 2 caracteres."

    if not password or len(password) < 6:
        return False, "La contraseña debe tener al menos 6 caracteres."

    with _obtener_conexion() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM usuarios WHERE username = ?", (username,))
        if cursor.fetchone():
            return False, "Ese nombre de usuario ya está registrado."

        salt = secrets.token_hex(16)
        pwd_hash = _hash_password(password, salt)
        ahora = time.strftime("%Y-%m-%d %H:%M:%S")

        cursor.execute(
            """
            INSERT INTO usuarios (username, nombre, password_hash, salt, rol, activo, creado_en)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (username, nombre, pwd_hash, salt, rol, activo, ahora),
        )
        conn.commit()

    _notificar_respaldo_nube()
    return True, "ok"


def autenticar_usuario(username: str, password: str) -> Tuple[Optional[dict], str]:
    """
    Verifica credenciales. Retorna (usuario_dict, mensaje).
    Si las credenciales son incorrectas o la cuenta no está activa, informa el motivo exacto.
    """
    username = (username or "").strip().lower()
    password = (password or "").strip()

    if not username or not password:
        return None, "Introduce usuario y contraseña."

    # Soporte para token maestro directo o clave inicial de admin
    master_token = os.getenv("HUB_ACCESS_TOKEN", "").strip()
    if username == "admin":
        if (master_token and hmac.compare_digest(password, master_token)) or password == "AdminUNIR2026!":
            return {
                "id": 1,
                "username": "admin",
                "nombre": "Alex (Administrador)",
                "rol": "admin",
                "activo": 1
            }, "ok"

    with _obtener_conexion() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, username, nombre, password_hash, salt, rol, activo FROM usuarios WHERE username = ?",
            (username,)
        )
        row = cursor.fetchone()
        if not row:
            return None, "Usuario o contraseña incorrectos."

        if not verificar_password(password, row["salt"], row["password_hash"]):
            return None, "Usuario o contraseña incorrectos."

        if row["activo"] == 0:
            return None, "Tu cuenta ha sido creada y está pendiente de aprobación por el administrador."

        if row["activo"] == -1:
            return None, "Tu cuenta ha sido desactivada o bloqueada por el administrador."

        # Actualizar último acceso
        ahora = time.strftime("%Y-%m-%d %H:%M:%S")
        cursor.execute("UPDATE usuarios SET ultimo_acceso = ? WHERE id = ?", (ahora, row["id"]))
        conn.commit()

        return {
            "id": row["id"],
            "username": row["username"],
            "nombre": row["nombre"],
            "rol": row["rol"],
            "activo": row["activo"]
        }, "ok"


def crear_sesion(username: str, duracion_dias: int = 30) -> str:
    """Crea un token de sesión criptográfico y lo persiste en la base de datos."""
    token = secrets.token_urlsafe(32)
    ahora = time.time()
    expira = ahora + (duracion_dias * 24 * 60 * 60)

    with _obtener_conexion() as conn:
        cursor = conn.cursor()
        # Limpiar sesiones expiradas
        cursor.execute("DELETE FROM sesiones WHERE expira_en < ?", (ahora,))
        cursor.execute(
            "INSERT INTO sesiones (token, username, creado_en, expira_en) VALUES (?, ?, ?, ?)",
            (token, username, ahora, expira)
        )
        conn.commit()

    return token


def obtener_usuario_por_sesion(token: Optional[str]) -> Optional[dict]:
    """Valida el token de sesión y retorna la información del usuario autenticado."""
    if not token:
        return None

    ahora = time.time()
    with _obtener_conexion() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT u.id, u.username, u.nombre, u.rol, u.activo
            FROM sesiones s
            JOIN usuarios u ON s.username = u.username
            WHERE s.token = ? AND s.expira_en > ?
            """,
            (token, ahora)
        )
        row = cursor.fetchone()
        if not row:
            return None

        if row["activo"] != 1:
            return None

        return {
            "id": row["id"],
            "username": row["username"],
            "nombre": row["nombre"],
            "rol": row["rol"],
            "activo": row["activo"]
        }


def eliminar_sesion(token: Optional[str]) -> None:
    """Cierra la sesión eliminando el token de la base de datos."""
    if not token:
        return
    with _obtener_conexion() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM sesiones WHERE token = ?", (token,))
        conn.commit()


# --- FUNCIONES DE ADMINISTRACIÓN ---

def listar_usuarios() -> List[dict]:
    """Devuelve la lista completa de usuarios para el panel de administración."""
    with _obtener_conexion() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT id, username, nombre, rol, activo, creado_en, ultimo_acceso
            FROM usuarios
            ORDER BY creado_en DESC
            """
        )
        filas = cursor.fetchall()
        return [
            {
                "id": r["id"],
                "username": r["username"],
                "nombre": r["nombre"],
                "rol": r["rol"],
                "activo": r["activo"],
                "creado_en": r["creado_en"],
                "ultimo_acceso": r["ultimo_acceso"] or "Nunca"
            }
            for r in filas
        ]


def cambiar_estado_usuario(user_id: int, nuevo_estado: int) -> bool:
    """Modifica el estado de un usuario (1: activo, 0: pendiente, -1: bloqueado)."""
    with _obtener_conexion() as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE usuarios SET activo = ? WHERE id = ?", (nuevo_estado, user_id))
        conn.commit()
    _notificar_respaldo_nube()
    return True


def cambiar_rol_usuario(user_id: int, nuevo_rol: str) -> bool:
    """Modifica el rol de un usuario ('admin' o 'estudiante')."""
    if nuevo_rol not in ("admin", "estudiante"):
        return False
    with _obtener_conexion() as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE usuarios SET rol = ? WHERE id = ?", (nuevo_rol, user_id))
        conn.commit()
    _notificar_respaldo_nube()
    return True


def eliminar_usuario(user_id: int) -> bool:
    """Elimina permanentemente a un usuario y sus sesiones asociadas."""
    with _obtener_conexion() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT username FROM usuarios WHERE id = ?", (user_id,))
        row = cursor.fetchone()
        if not row or row["username"] == "admin":
            # No permitir borrar el admin principal
            return False
        cursor.execute("DELETE FROM sesiones WHERE username = ?", (row["username"],))
        cursor.execute("DELETE FROM usuarios WHERE id = ?", (user_id,))
        conn.commit()
    _notificar_respaldo_nube()
    return True


def contar_pendientes() -> int:
    """Devuelve el número de usuarios que esperan aprobación."""
    with _obtener_conexion() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM usuarios WHERE activo = 0")
        return cursor.fetchone()[0]
