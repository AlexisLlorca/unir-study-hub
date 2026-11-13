"""
Sistema de gestión de usuarios, roles, sesiones y autenticación para UNIR Study Hub.
Utiliza SQLite estándar, contraseñas protegidas con PBKDF2-HMAC-SHA256 (100k iteraciones)
y respaldo automático al dataset de Hugging Face.
"""

import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from src.core.config import DATA_DIR, console

DB_PATH = DATA_DIR / "usuarios.db"
USERS_JSON_PATH = DATA_DIR / "usuarios.json"


def _obtener_conexion() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def exportar_usuarios_a_json() -> None:
    """Exporta todos los usuarios a un archivo JSON seguro para persistencia definitiva en la nube."""
    try:
        with _obtener_conexion() as conn:
            cursor = conn.cursor()
            cursor.execute("PRAGMA wal_checkpoint(FULL);")
            cursor.execute("PRAGMA table_info(usuarios);")
            cols = [c[1] for c in cursor.fetchall()]
            key_col = ", COALESCE(gemini_api_key, '') as gemini_api_key" if "gemini_api_key" in cols else ", '' as gemini_api_key"
            cursor.execute(
                f"""
                SELECT username, nombre, password_hash, salt, rol, activo, creado_en{key_col}
                FROM usuarios
                """
            )
            filas = cursor.fetchall()
            usuarios = [dict(r) for r in filas]

        USERS_JSON_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(USERS_JSON_PATH, "w", encoding="utf-8") as f:
            json.dump(usuarios, f, indent=2, ensure_ascii=False)
    except Exception as e:
        pass


def importar_usuarios_desde_json() -> None:
    """Restaura usuarios desde el JSON persistente si no están en la BD SQLite (descarga y fusiona con HF)."""
    try:
        from src.core.cloud_sync import _descargar_archivo_hf_directo
        temp_remote = USERS_JSON_PATH.with_suffix(".remote.json")
        if _descargar_archivo_hf_directo("data/usuarios.json", temp_remote) and temp_remote.exists():
            try:
                with open(temp_remote, "r", encoding="utf-8") as f:
                    usuarios_remotos = json.load(f)
                if isinstance(usuarios_remotos, list) and len(usuarios_remotos) > 0:
                    usuarios_locales = []
                    if USERS_JSON_PATH.exists():
                        try:
                            with open(USERS_JSON_PATH, "r", encoding="utf-8") as fl:
                                usuarios_locales = json.load(fl)
                        except Exception:
                            pass
                    mapa = {u["username"].strip().lower(): u for u in usuarios_locales if isinstance(u, dict) and u.get("username")}
                    for ur in usuarios_remotos:
                        if isinstance(ur, dict) and ur.get("username"):
                            mapa[ur["username"].strip().lower()] = ur
                    with open(USERS_JSON_PATH, "w", encoding="utf-8") as fw:
                        json.dump(list(mapa.values()), fw, indent=2, ensure_ascii=False)
            finally:
                temp_remote.unlink(missing_ok=True)
    except Exception:
        pass

    if not USERS_JSON_PATH.exists():
        return

    try:
        with open(USERS_JSON_PATH, "r", encoding="utf-8") as f:
            usuarios = json.load(f)
        if not isinstance(usuarios, list):
            return

        with _obtener_conexion() as conn:
            cursor = conn.cursor()
            cursor.execute("PRAGMA table_info(usuarios);")
            cols = [c[1] for c in cursor.fetchall()]
            if "gemini_api_key" not in cols:
                cursor.execute("ALTER TABLE usuarios ADD COLUMN gemini_api_key TEXT DEFAULT '';")
                conn.commit()

            for u in usuarios:
                username = u.get("username", "").strip().lower()
                if not username:
                    continue
                cursor.execute("SELECT id, activo, COALESCE(gemini_api_key, '') as gemini_api_key FROM usuarios WHERE username = ?", (username,))
                row = cursor.fetchone()
                val_activo = int(u.get("activo", 0) if "activo" in u else 1)
                if not row:
                    cursor.execute(
                        """
                        INSERT INTO usuarios (username, nombre, password_hash, salt, rol, activo, creado_en, gemini_api_key)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            username,
                            u.get("nombre", username),
                            u.get("password_hash", ""),
                            u.get("salt", ""),
                            u.get("rol", "estudiante"),
                            val_activo,
                            u.get("creado_en", time.strftime("%Y-%m-%d %H:%M:%S")),
                            u.get("gemini_api_key", ""),
                        ),
                    )
                else:
                    updates = []
                    params = []
                    if u.get("gemini_api_key") and not row["gemini_api_key"]:
                        updates.append("gemini_api_key = ?")
                        params.append(u["gemini_api_key"])
                    if val_activo == 1 and row["activo"] != 1:
                        updates.append("activo = ?")
                        params.append(1)
                    if updates:
                        params.append(row["id"])
                        cursor.execute(f"UPDATE usuarios SET {', '.join(updates)} WHERE id = ?", params)
            conn.commit()
            cursor.execute("PRAGMA wal_checkpoint(FULL);")
    except Exception:
        pass


def _notificar_respaldo_nube():
    try:
        exportar_usuarios_a_json()
        from src.core.cloud_sync import respaldar_archivo_en_hf_async
        respaldar_archivo_en_hf_async(USERS_JSON_PATH)
        respaldar_archivo_en_hf_async(DB_PATH)
    except Exception:
        pass


def inicializar_bd_usuarios() -> None:
    """Crea las tablas de usuarios y sesiones si no existen, y asegura el usuario administrador."""
    if not DB_PATH.exists():
        try:
            from src.core.cloud_sync import _descargar_archivo_hf_directo
            _descargar_archivo_hf_directo("data/usuarios.db", DB_PATH)
        except Exception:
            pass

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
                ultimo_acceso TEXT,
                gemini_api_key TEXT DEFAULT ''
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

        # Asegurar columnas de claves de IA si la tabla ya existía
        cursor.execute("PRAGMA table_info(usuarios);")
        cols = [c[1] for c in cursor.fetchall()]
        if "gemini_api_key" not in cols:
            cursor.execute("ALTER TABLE usuarios ADD COLUMN gemini_api_key TEXT DEFAULT '';")
            conn.commit()
        if "openrouter_api_key" not in cols:
            cursor.execute("ALTER TABLE usuarios ADD COLUMN openrouter_api_key TEXT DEFAULT '';")
            conn.commit()
        if "groq_api_key" not in cols:
            cursor.execute("ALTER TABLE usuarios ADD COLUMN groq_api_key TEXT DEFAULT '';")
            conn.commit()

        # 1. Asegurar la cuenta de administrador.
        # La contraseña NUNCA se escribe en el código: se toma de HUB_ADMIN_PASSWORD.
        admin_user = (os.getenv("HUB_ADMIN_USER", "").strip() or "alexking44").lower()
        admin_pass = os.getenv("HUB_ADMIN_PASSWORD", "").strip()

        cursor.execute(
            "SELECT id, salt, password_hash FROM usuarios WHERE username = ?",
            (admin_user,),
        )
        admin_row = cursor.fetchone()
        ahora = time.strftime("%Y-%m-%d %H:%M:%S")

        if not admin_row:
            # Primera vez: si no hay contraseña configurada, generamos una aleatoria
            # y la mostramos una sola vez en el log para poder entrar.
            if not admin_pass:
                admin_pass = secrets.token_urlsafe(18)
                console.print(
                    "\n[bold yellow]⚠ Contraseña de administrador generada:[/bold yellow] "
                    f"[bold]{admin_pass}[/bold]\n"
                    "Guárdala y define HUB_ADMIN_PASSWORD en .env para fijarla.\n"
                )
            salt = secrets.token_hex(16)
            pwd_hash = _hash_password(admin_pass, salt)
            cursor.execute(
                """
                INSERT INTO usuarios (username, nombre, password_hash, salt, rol, activo, creado_en)
                VALUES (?, ?, ?, ?, 'admin', 1, ?)
                """,
                (admin_user, "Alex (Administrador)", pwd_hash, salt, ahora),
            )
        else:
            # La cuenta ya existe: solo aseguramos rol y estado, sin pisar su contraseña.
            cursor.execute(
                "UPDATE usuarios SET rol = 'admin', activo = 1 WHERE username = ?",
                (admin_user,),
            )
            # Si hay contraseña configurada y no coincide con la guardada, la actualizamos.
            if admin_pass and not verificar_password(
                admin_pass, admin_row["salt"], admin_row["password_hash"]
            ):
                salt = secrets.token_hex(16)
                pwd_hash = _hash_password(admin_pass, salt)
                cursor.execute(
                    "UPDATE usuarios SET password_hash = ?, salt = ? WHERE username = ?",
                    (pwd_hash, salt, admin_user),
                )

        # 2. Degradar cualquier otra cuenta que se haya puesto como admin por error
        cursor.execute(
            """
            UPDATE usuarios 
            SET rol = 'estudiante', activo = 0 
            WHERE username != ? AND rol = 'admin'
            """,
            (admin_user,),
        )

        # 3. Invalidar sesiones únicamente de usuarios inactivos o bloqueados (los alumnos aprobados conservan su sesión)
        cursor.execute(
            """
            DELETE FROM sesiones 
            WHERE username IN (SELECT username FROM usuarios WHERE activo != 1)
            """
        )

        conn.commit()
        cursor.execute("PRAGMA wal_checkpoint(FULL);")

    # 4. Restaurar usuario desde el JSON persistente
    importar_usuarios_desde_json()

    # 5. En modo personal privado (por defecto), mantener exclusivamente la cuenta del propietario administrador
    if os.getenv("HUB_ALLOW_MULTIUSER", "0") != "1":
        admin_user = (os.getenv("HUB_ADMIN_USER", "").strip() or "alexking44").lower()
        with _obtener_conexion() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM sesiones WHERE username != ?", (admin_user,))
            cursor.execute("DELETE FROM usuarios WHERE username != ?", (admin_user,))
            conn.commit()
            cursor.execute("PRAGMA wal_checkpoint(FULL);")

    exportar_usuarios_a_json()


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
    activo: int = 0,
    gemini_api_key: str = "",
) -> Tuple[bool, str]:
    """
    Registra un nuevo usuario en la base de datos.
    Por defecto queda con activo=0 (pendiente de aprobación por el admin).
    """
    if os.getenv("HUB_ALLOW_MULTIUSER", "0") != "1":
        return False, "Esta instancia es de uso personal privado. Si deseas usar NOVA Study Hub, descarga el proyecto desde GitHub y ejecútalo en tu propio ordenador."

    username = (username or "").strip().lower()
    nombre = (nombre or "").strip()
    password = (password or "").strip()
    gemini_api_key = (gemini_api_key or "").strip()

    if not username or not re.match(r"^[a-z0-9_.-]{3,30}$", username):
        return False, "El nombre de usuario debe tener entre 3 y 30 caracteres (solo letras, números, puntos y guiones)."

    if not nombre or len(nombre) < 2:
        return False, "El nombre debe tener al menos 2 caracteres."

    if not password or len(password) < 6:
        return False, "La contraseña debe tener al menos 6 caracteres."

    admin_user = (os.getenv("HUB_ADMIN_USER", "").strip() or "alexking44").lower()
    if username == admin_user:
        return False, "Ese nombre de usuario está reservado."

    # Por seguridad estricta: todo usuario registrado es siempre estudiante y pendiente (activo=0)
    rol = "estudiante"
    activo = 0

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
            INSERT INTO usuarios (username, nombre, password_hash, salt, rol, activo, creado_en, gemini_api_key)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (username, nombre, pwd_hash, salt, rol, activo, ahora, gemini_api_key),
        )
        conn.commit()
        cursor.execute("PRAGMA wal_checkpoint(FULL);")

    _notificar_respaldo_nube()
    return True, "ok"


def actualizar_api_key_usuario(username: str, api_key: str, proveedor: str = "gemini") -> bool:
    """Actualiza la clave privada del proveedor indicado (gemini, openrouter, groq) para el usuario y la respalda."""
    username = (username or "").strip().lower()
    api_key = (api_key or "").strip()
    if not username:
        return False

    col = "openrouter_api_key" if proveedor == "openrouter" else ("groq_api_key" if proveedor == "groq" else "gemini_api_key")
    with _obtener_conexion() as conn:
        cursor = conn.cursor()
        cursor.execute("PRAGMA table_info(usuarios);")
        cols = [c[1] for c in cursor.fetchall()]
        if col not in cols:
            cursor.execute(f"ALTER TABLE usuarios ADD COLUMN {col} TEXT DEFAULT '';")
            conn.commit()
        cursor.execute(f"UPDATE usuarios SET {col} = ? WHERE username = ?", (api_key, username))
        conn.commit()
        cursor.execute("PRAGMA wal_checkpoint(FULL);")

    _notificar_respaldo_nube()
    return True


def autenticar_usuario(username: str, password: str) -> Tuple[Optional[dict], str]:
    """
    Verifica credenciales. Retorna (usuario_dict, mensaje).
    Si las credenciales son incorrectas o la cuenta no está activa, informa el motivo exacto.
    """
    username = (username or "").strip().lower()
    password = (password or "").strip()

    if not username or not password:
        return None, "Introduce usuario y contraseña."

    # El token de acceso a la red (HUB_ACCESS_TOKEN) NO sirve como contraseña.
    # Son dos cosas distintas: el token abre la puerta de la red, la contraseña
    # identifica al usuario. Mezclarlas convertía el token en una llave maestra.

    with _obtener_conexion() as conn:
        cursor = conn.cursor()
        cursor.execute("PRAGMA table_info(usuarios);")
        cols = [c[1] for c in cursor.fetchall()]
        col_gemini = "COALESCE(gemini_api_key, '') as gemini_api_key" if "gemini_api_key" in cols else "'' as gemini_api_key"
        col_or = ", COALESCE(openrouter_api_key, '') as openrouter_api_key" if "openrouter_api_key" in cols else ", '' as openrouter_api_key"
        col_groq = ", COALESCE(groq_api_key, '') as groq_api_key" if "groq_api_key" in cols else ", '' as groq_api_key"

        cursor.execute(
            f"SELECT id, username, nombre, password_hash, salt, rol, activo, {col_gemini}{col_or}{col_groq} FROM usuarios WHERE username = ?",
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
            "activo": row["activo"],
            "gemini_api_key": row["gemini_api_key"],
            "openrouter_api_key": row["openrouter_api_key"],
            "groq_api_key": row["groq_api_key"],
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
        cursor.execute("PRAGMA table_info(usuarios);")
        cols = [c[1] for c in cursor.fetchall()]
        col_gemini = "COALESCE(u.gemini_api_key, '') as gemini_api_key" if "gemini_api_key" in cols else "'' as gemini_api_key"
        col_or = ", COALESCE(u.openrouter_api_key, '') as openrouter_api_key" if "openrouter_api_key" in cols else ", '' as openrouter_api_key"
        col_groq = ", COALESCE(u.groq_api_key, '') as groq_api_key" if "groq_api_key" in cols else ", '' as groq_api_key"

        cursor.execute(
            f"""
            SELECT u.id, u.username, u.nombre, u.rol, u.activo, {col_gemini}{col_or}{col_groq}
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
            "activo": row["activo"],
            "gemini_api_key": row["gemini_api_key"],
            "openrouter_api_key": row["openrouter_api_key"],
            "groq_api_key": row["groq_api_key"],
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
            SELECT id, username, nombre, rol, activo, creado_en, ultimo_acceso, COALESCE(gemini_api_key, '') as gemini_api_key
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
                "ultimo_acceso": r["ultimo_acceso"] or "Nunca",
                "tiene_key": bool(r["gemini_api_key"]),
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
        cursor.execute("SELECT username, rol FROM usuarios WHERE id = ?", (user_id,))
        row = cursor.fetchone()
        if not row or row["rol"] == "admin":
            # No permitir borrar a un administrador
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
