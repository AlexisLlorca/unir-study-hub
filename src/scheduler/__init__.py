"""
Módulo de Automatización y Temporizadores Programados para UNIR Study Hub.
Gestiona la ejecución periódica y desatendida mediante systemd user timers.
"""

from .sync_scheduler import SyncScheduler

__all__ = ["SyncScheduler"]
