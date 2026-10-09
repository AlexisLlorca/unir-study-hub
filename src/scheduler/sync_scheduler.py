"""
Gestor de Tareas Programadas y Automatización para UNIR Study Hub.
Utiliza systemd user timers (nativo de Linux/Arch) para ejecutar la sincronización
y descarga de nuevos contenidos de forma desatendida y periódica.
"""

import sys
import subprocess
from pathlib import Path
from typing import Dict, Any, Optional

# Importación modular
BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from rich.panel import Panel
from src.core.config import console

SYSTEMD_USER_DIR = Path.home() / ".config" / "systemd" / "user"
SERVICE_FILE = SYSTEMD_USER_DIR / "unir-study-hub-sync.service"
TIMER_FILE = SYSTEMD_USER_DIR / "unir-study-hub-sync.timer"
PYTHON_VENV_BIN = BASE_DIR / ".venv" / "bin" / "python3"
MAIN_SCRIPT = BASE_DIR / "main.py"


class SyncScheduler:
    """Administra la activación, desactivación e inspección del temporizador systemd."""

    def __init__(self):
        SYSTEMD_USER_DIR.mkdir(parents=True, exist_ok=True)

    def _generar_archivos_systemd(self, on_calendar: str = "*-*-* 04:00:00"):
        """Genera los archivos .service y .timer para systemd."""
        service_content = f"""[Unit]
Description=Sincronizacion desatendida de nuevos contenidos UNIR Study Hub
After=network.target

[Service]
Type=oneshot
WorkingDirectory={BASE_DIR}
ExecStart={PYTHON_VENV_BIN} {MAIN_SCRIPT} --sync
Environment=DISPLAY=:0
Environment=WAYLAND_DISPLAY=wayland-1

[Install]
WantedBy=default.target
"""
        timer_content = f"""[Unit]
Description=Temporizador periodico de UNIR Study Hub
RefuseManualStart=no
RefuseManualStop=no

[Timer]
OnCalendar={on_calendar}
Persistent=true

[Install]
WantedBy=timers.target
"""
        with open(SERVICE_FILE, "w", encoding="utf-8") as f:
            f.write(service_content)

        with open(TIMER_FILE, "w", encoding="utf-8") as f:
            f.write(timer_content)

    def activar_programacion(self, opcion_horario: str = "1") -> bool:
        """
        Configura y activa el temporizador systemd según la preferencia del usuario.
        Opciones:
        1: Todos los días a las 04:00 AM (Recomendado)
        2: Cada 12 horas
        3: Cada 6 horas
        4: De Lunes a Viernes a las 08:00 AM
        """
        horarios = {
            "1": ("*-*-* 04:00:00", "Todos los días a las 04:00 AM"),
            "2": ("*-*-* 00,12:00:00", "Cada 12 horas (00:00 y 12:00)"),
            "3": ("*-*-* 00,06,12,18:00:00", "Cada 6 horas"),
            "4": ("Mon..Fri *-*-* 08:00:00", "De lunes a viernes a las 08:00 AM"),
        }

        cal_expr, descripcion = horarios.get(opcion_horario, horarios["1"])
        self._generar_archivos_systemd(cal_expr)

        try:
            subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
            subprocess.run(["systemctl", "--user", "enable", "--now", "unir-study-hub-sync.timer"], check=True)
            console.print(
                Panel.fit(
                    f"[bold green]✔ ¡Temporizador automático activado con éxito![/bold green]\n\n"
                    f"[cyan]Frecuencia configurada:[/cyan] [white]{descripcion}[/white]\n"
                    f"[dim]El sistema revisará automáticamente el campus y descargará los nuevos contenidos sin que tengas que hacer nada.[/dim]",
                    border_style="green",
                )
            )
            return True
        except Exception as e:
            console.print(f"[bold red]❌ Error activando temporizador systemd:[/bold red] {e}")
            return False

    def desactivar_programacion(self) -> bool:
        """Desactiva y detiene el temporizador automático."""
        try:
            subprocess.run(["systemctl", "--user", "disable", "--now", "unir-study-hub-sync.timer"], check=False)
            console.print("[yellow]✔ Temporizador automático desactivado.[/yellow]\n")
            return True
        except Exception as e:
            console.print(f"[bold red]❌ Error desactivando temporizador:[/bold red] {e}")
            return False

    def estado_programacion(self) -> Dict[str, Any]:
        """Consulta si el timer está activo y cuándo será la próxima ejecución."""
        try:
            res = subprocess.run(
                ["systemctl", "--user", "is-active", "unir-study-hub-sync.timer"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            activo = res.stdout.strip() == "active"

            proxima = "No programada"
            if activo:
                proc_list = subprocess.run(
                    ["systemctl", "--user", "list-timers", "unir-study-hub-sync.timer", "--no-legend"],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
                linea = proc_list.stdout.strip()
                if linea:
                    partes = linea.split()
                    if len(partes) >= 5:
                        proxima = f"{partes[0]} {partes[1]} {partes[2]} ({partes[4]} left)"

            return {"activo": activo, "proxima_ejecucion": proxima}
        except Exception:
            return {"activo": False, "proxima_ejecucion": "Desconocido"}
