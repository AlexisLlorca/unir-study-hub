"""
Resumen semanal del entrenador personal para UNIR Study Hub.
Calcula, a partir de datos que ya existen (flashcards, progreso y agenda),
el estado de la semana del alumno: racha, repaso, tests y próximas entregas.

Es una función pura: recibe los datos y devuelve el resumen. Así se puede
probar sin tocar los ficheros reales del usuario.
"""

from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional


def _a_fecha(valor: Optional[str]) -> Optional[date]:
    """Convierte '2026-11-13 21:00', '2026-11-13T20:59:12+00:00' o '2026-11-13' en date. None si no se puede."""
    if not valor or not isinstance(valor, str):
        return None
    try:
        return datetime.fromisoformat(valor).date()
    except ValueError:
        pass
    try:
        return datetime.strptime(valor[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def calcular_resumen_semanal(
    mazos: Dict[str, Any],
    progreso: Dict[str, Any],
    eventos: List[Dict[str, Any]],
    hoy: Optional[date] = None,
) -> Dict[str, Any]:
    """
    Calcula el resumen de la semana (últimos 7 días) del entrenador.

    - mazos:    estructura {programa: [tarjetas]} de flashcards.
    - progreso: contenido de progreso.json (historial_tests, preguntas_falladas...).
    - eventos:  agenda combinada oficial + personal.
    - hoy:      fecha de referencia (por defecto, hoy de verdad; para los tests).
    """
    hoy = hoy or date.today()
    inicio_semana = hoy - timedelta(days=6)
    inicio_mes = hoy - timedelta(days=30)

    # --- Fechas con actividad: repasar una tarjeta o hacer un test cuenta como estudiar ---
    fechas_actividad: set = set()
    tarjetas_semana = 0
    for tarjetas in (mazos or {}).values():
        if not isinstance(tarjetas, list):
            continue
        for tarjeta in tarjetas:
            if not isinstance(tarjeta, dict):
                continue
            fecha = _a_fecha(tarjeta.get("ultima_revision"))
            if fecha is None:
                continue
            fechas_actividad.add(fecha)
            if inicio_semana <= fecha <= hoy:
                tarjetas_semana += 1

    historial = [r for r in (progreso or {}).get("historial_tests", []) or [] if isinstance(r, dict)]
    tests_semana: List[Dict[str, Any]] = []
    tests_mes: List[Dict[str, Any]] = []
    for resultado in historial:
        fecha = _a_fecha(resultado.get("fecha"))
        if fecha is None:
            continue
        fechas_actividad.add(fecha)
        if inicio_semana <= fecha <= hoy:
            tests_semana.append(resultado)
        if inicio_mes <= fecha <= hoy:
            tests_mes.append(resultado)

    # --- Racha: días seguidos con actividad, contando hacia atrás desde hoy.
    # Si hoy aún no has estudiado, no se rompe: se sigue contando desde ayer.
    racha_dias = 0
    candidato = hoy if hoy in fechas_actividad else hoy - timedelta(days=1)
    while candidato in fechas_actividad:
        racha_dias += 1
        candidato -= timedelta(days=1)

    notas_semana = [float(r["nota"]) for r in tests_semana if isinstance(r.get("nota"), (int, float))]
    nota_media = round(sum(notas_semana) / len(notas_semana), 1) if notas_semana else None

    # --- Tema flojo: el peor test de los últimos 30 días ---
    tema_flojo = None
    con_nota = [r for r in tests_mes if isinstance(r.get("nota"), (int, float))]
    if con_nota:
        peor = min(con_nota, key=lambda r: float(r["nota"]))
        tema_flojo = {
            "asignatura": peor.get("asignatura", ""),
            "tema": peor.get("tema", ""),
            "nota": round(float(peor["nota"]), 1),
            "fecha": str(peor.get("fecha", "")),
        }

    # --- Entregas y actividades de los próximos 7 días (las clases diarias no ensucian la lista) ---
    fin_ventana = hoy + timedelta(days=7)
    estados_descartados = {"Vista", "Hecho", "Entregado"}
    entregas_proximas = []
    for evento in eventos or []:
        if not isinstance(evento, dict):
            continue
        fecha = _a_fecha(evento.get("fecha_dia"))
        if fecha is None or not (hoy <= fecha <= fin_ventana):
            continue
        if (evento.get("categoria") or "").lower() == "clase":
            continue
        if (evento.get("estado") or "") in estados_descartados:
            continue
        entregas_proximas.append({
            "titulo": evento.get("titulo") or "Sin título",
            "asignatura": evento.get("asignatura") or "",
            "fecha": fecha.isoformat(),
            "dias_restantes": (fecha - hoy).days,
        })
    entregas_proximas.sort(key=lambda e: (e["fecha"], e["titulo"]))
    entregas_proximas = entregas_proximas[:5]

    return {
        "racha_dias": racha_dias,
        "tarjetas_semana": tarjetas_semana,
        "tests_semana": len(tests_semana),
        "nota_media_semana": nota_media,
        "tema_flojo": tema_flojo,
        "entregas_proximas": entregas_proximas,
    }


def _cortar(texto: str, maximo: int = 42) -> str:
    """Recorta un título largo para que la recomendación no sea un párrafo."""
    texto = (texto or "").strip()
    return texto if len(texto) <= maximo else texto[:maximo] + "…"


def construir_recomendacion(semana: Dict[str, Any], estadisticas: Dict[str, Any]) -> str:
    """
    Recomendación diaria del entrenador, combinando las tres señales con prioridad:
    1. Entrega próxima (lo que tiene fecha fija manda sobre lo demás).
    2. Repaso vencido (10-15 minutos que consolidan lo que se está olvidando).
    3. Tema flojo (test corto sobre el peor resultado del mes).
    """
    semana = semana or {}
    estadisticas = estadisticas or {}
    entregas = semana.get("entregas_proximas") or []
    vencidas = int(estadisticas.get("vencidas") or 0)
    tema_flojo = semana.get("tema_flojo")

    partes: List[str] = []

    # 1. Entrega próxima: si es urgente (≤2 días) es la prioridad; si cae pronto, se menciona.
    if entregas:
        primera = entregas[0]
        dias = int(primera.get("dias_restantes", 99))
        titulo = _cortar(primera.get("titulo", ""))
        asignatura = _cortar(primera.get("asignatura", ""), 30)
        if dias <= 1:
            cuando = "para hoy" if dias == 0 else "para mañana"
            partes.append(f"Prioridad: «{titulo}» ({asignatura}) es {cuando}. Arráncala hoy.")
        elif dias <= 4:
            partes.append(f"Con vista a «{titulo}» ({asignatura}), que cae en {dias} días.")

    # 2. Repaso vencido: siempre lo segundo, es el mantenimiento diario.
    if vencidas > 0:
        partes.append(f"Repaso: {vencidas} tarjeta(s) vencidas, unos 15 minutos.")

    # 3. Tema flojo: el cierre con un test corto.
    if tema_flojo:
        partes.append(
            f"Cierra con un test corto de «{_cortar(tema_flojo.get('tema', ''))}» "
            f"({tema_flojo.get('asignatura', '')}): tu nota más floja ({tema_flojo.get('nota')})."
        )

    if not partes:
        return (
            "Día tranquilo: no tienes repasos ni entregas inmediatas. Genera un test de un tema "
            "nuevo o adelanta temario para aprovechar el día."
        )

    # La racha se menciona solo si hay algo que hacer (es el empujón del final).
    racha = int(semana.get("racha_dias") or 0)
    if racha > 0:
        dias_palabra = "día" if racha == 1 else "días"
        partes.append(f"Y llevas {racha} {dias_palabra} de racha: hoy la mantienes.")

    return " ".join(partes)
