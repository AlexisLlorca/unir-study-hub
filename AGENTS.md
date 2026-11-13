# AGENTS.md — unir-study-hub (NOVA Study Hub)

App personal de estudio: FastAPI + frontend Alpine.js/Tailwind, IA (Gemini/OpenRouter/Groq BYOK),
sincronización local ↔ Hugging Face Datasets y despliegue en Render (plan free).
Proyecto de un solo usuario (no multiusuario comercial). Comentarios y UI en español.

## Comandos

- Arrancar la web: `.venv/bin/python main.py --web` → http://localhost:8080.
  El acceso por loopback salta el token `HUB_ACCESS_TOKEN`; desde móvil/red sí hace falta.
- Tests: `.venv/bin/python -m unittest discover -s tests -v` (unittest de stdlib).
  **No usar pytest**: no está instalado y no se instala sin permiso del usuario.
- Test único: `.venv/bin/python -m unittest tests.test_srs -v`.
- Sintaxis: `.venv/bin/python -m py_compile <fichero(s)>`.
- Servicio local: `systemctl --user restart unir-study-hub-web.service`
  (unidad en `~/.config/systemd/user/`); errores en `journalctl --user -u unir-study-hub-web.service`.
- Limpieza de HF: `scripts/limpiar_hf_credenciales.py` — **simula por defecto**; solo borra con `--confirmar`.

## Arquitectura

- `main.py`: menú CLI; con `--web` crea directorios, restaura desde HF y lanza
  `src/ui/web_server.py:iniciar_servidor_web()`.
- `src/ui/web_server.py` (~15.000 líneas) es un **monolito**: FastAPI y **todo el HTML/JS del
  frontend como strings de Python** (Alpine.js + Tailwind por CDN). Hacer ediciones quirúrgicas,
  nunca reescrituras del fichero.
- Validar el JS del frontend con el código **servido**, no el fuente: extraer los `<script>` de
  `curl http://localhost:8080/` y pasarles `node --check`. En el `.py` los backslashes están
  escapados a nivel Python y fallarían la comprobación directa.
- Catálogo de titulaciones/asignaturas: diccionarios (`PROGRAMAS`, `ASIGNATURAS_2DAM`,
  `ASIGNATURAS_1DAM`, `MODULOS_CIBER`, `ASIGNATURAS_PFU`) en `src/core/academic_structure.py`.
- Contenido generado (guías, tests, flashcards, progreso) persiste en `data/procesado/` y
  `data/usuarios_espacios/<usuario>/`; es instantáneo y permanente, no hay base de datos de contenido.
- Flashcards: motor SM-2 propio en `src/ai/flashcards.py` (`calificar_tarjeta` es wrapper legado).
- Cachés por mtime+`deepcopy` en `progress_tracker.py` y `flashcards.py`; otras cachés TTL en
  `web_server.py` (`_CACHE_CATALOGO`, `_CACHE_AGENDA_COMBINADA`...). Se invalidan al escribir vía
  `escribir_json_atomico`; no introducir rutas de datos fuera de este patrón sin revisar caché.

## Datos personales y seguridad (crítico)

- `data/` contiene datos reales del usuario: `usuarios.db` (hashes + API keys), tokens de Canvas,
  agenda. Nunca a git ni a HF; permisos 600.
- Filtro de subidas a la nube: `_es_ruta_sensible()` en `src/core/cloud_sync.py`. Cualquier fichero
  nuevo con credenciales debe entrar en esa lista.
- **Las claves de API jamás vuelven al navegador**: respuestas con `_usuario_publico()` /
  `enmascarar_clave()` (config.py). El frontend solo maneja flags booleanos; no guardar claves en
  `localStorage` (se migran al servidor una vez si quedan de versiones antiguas).
- Peticiones HTTP a URLs controladas por usuario → validar con `_url_bloqueada()` /
  `_normalizar_url_campus()` (`src/crawler/student_canvas.py`), y usar `_OPENER_SEGURO` (valida
  redirecciones). HTTPS obligatorio.
- `.env` tiene secretos reales: no imprimir valores, no pegarlos en comandos ni en el chat.

## Gotcha de la nube (dura de descubrir)

- Al arrancar, `descargar_datos_desde_hf_si_vacio()` re-descarga el "estado vivo" (`mazos.json`,
  `canvas_sync.json`) de HF **siempre**, pisando lo local. Un cambio local en esos ficheros
  desaparece al reiniciar si no se refleja también en HF (borrar ahí, o dejar que
  `respaldar_archivo_en_hf_async` lo suba). El bundle `procesado_bundle.tar.gz` se reconstruye con
  `crear_y_subir_bundle_rapido()`.
- Dataset: `AlexLlorca/unir-study-hub-data` (privado). Si es público, el código avisa pero conviene
  cambiarlo.

## Tests

- unittest stdlib, sin fixtures externos ni servicios. Aíslan con `object.__new__(Gestor...)` y
  directorios temporales; nunca tocan los datos reales de `data/`.
- Suites: SRS/SM-2 (`test_srs`), seguridad de nube y tar (`test_cloud_seguridad`), cachés mtime
  (`test_cache`), anti-SSRF y enmascaramiento (`test_ssrf_y_claves`), resumen semanal
  (`test_resumen_semanal`).

## Despliegue y flujo

- `master` → GitHub (`AlexisLlorca/unir-study-hub`) → Render auto-despliega con `render.yaml`.
- Variables críticas en Render: `HUB_ADMIN_PASSWORD` (si falta, se genera aleatoria solo en logs),
  `GEMINI_API_KEY`, `HF_TOKEN`, `HUB_ACCESS_TOKEN`, `HUB_TRUSTED_PROXIES`.
- El usuario exige copia previa antes de tocar ficheros: `~/backups/<fecha>/unir-study-hub/`.
- Cambios relevantes: commitear y hacer `git push origin master` para que lleguen a Render.
