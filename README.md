# 🎓 NOVA Study Hub — Plataforma Académica Inteligente (Auto-alojada y 100% Gratuita)

**NOVA Study Hub** es un entorno de estudio multi-titulación con inteligencia artificial integrada (**Tutor NOVA**) diseñado para ejecutarse de forma **100% gratuita en tu propio ordenador** (Windows, macOS o Linux) y adaptable a cualquier carrera, ciclo formativo, máster o certificación.

---

## ✨ Características Principales

- 📚 **Visor de Temarios y Resúmenes Inteligentes:** Lectura estructurada de temas con síntesis generada por IA, modo lectura limpia, índice interactivo y audio TTS.
- 🧪 **Tests Oficiales y Verdadero/Falso Persistentes:** Simulacros de examen autocorregibles con justificación técnica que se guardan automáticamente en tu disco para no gastar cuota regenerándolos.
- 🗂️ **Flashcards (Active Recall):** Tarjetas de memoria espaciada por tema y asignatura.
- 🤖 **Tutor IA NOVA (BYOK — Bring Your Own Key):** Funciona con tu propia clave gratuita de [Google AI Studio (Gemini)](https://aistudio.google.com/app/apikey), sin suscripciones ni pagos.
- 📅 **Calendario, To-Do y Conexión con Canvas (.ics / Token):** Sincroniza entregas y clases en tu propio espacio local.
- 📱 **Modo App (PWA):** Instalable en escritorio o accesible desde el móvil estando en la misma red Wi-Fi de casa.

---

## 🚀 Instalación y Arranque en 1 Clic (Coste 0 €)

### Requisitos previos
- Tener instalado **Python 3.10 o superior** ([descargar de python.org](https://www.python.org/downloads/)).
- Una clave gratuita de API de **Google Gemini** (consíguela gratis en 30 segundos en [aistudio.google.com/app/apikey](https://aistudio.google.com/app/apikey)).

### Opción A: En Windows
1. Descarga este repositorio (botón **Code → Download ZIP** y descomprímelo, o mediante `git clone`).
2. Haz doble clic en **`iniciar_windows.bat`**.
3. Se creará automáticamente el entorno virtual, instalará las librerías necesarias y abrirá tu navegador en **`http://localhost:8080`**.

### Opción B: En Linux / macOS
```bash
chmod +x iniciar.sh
./iniciar.sh
```
Después abre **`http://localhost:8080`** en tu navegador.

---

## 🔑 Configurar tu Clave Gratuita de IA (Gemini)

Tienes dos formas de poner tu clave de Gemini (`AIzaSy...`):
1. **Desde la propia interfaz web:** Arriba en la barra superior pulsa el icono de la **llave (`🔑`)** y pega tu clave de Google AI Studio.
2. **O en el archivo `.env`:** Abre el archivo `.env` en la carpeta del proyecto y rellena:
   ```env
   GEMINI_API_KEY=tu_clave_de_gemini_aqui
   ```

---

## 🛠️ Cómo Adaptar el Proyecto a tus Asignaturas y Temarios

Puedes personalizar completamente las titulaciones, asignaturas y documentos a tu gusto:

1. **Añadir tus propios PDFs y apuntes:**
   - Coloca tus PDFs dentro de la carpeta `data/materiales/<Nombre de tu Asignatura>/`.
   - Por ejemplo: `data/materiales/Acceso a Datos/Tema 1. Ficheros.pdf`.
2. **Modificar o crear nuevas Titulaciones y Asignaturas:**
   - Abre el archivo [`src/core/academic_structure.py`](src/core/academic_structure.py).
   - En los diccionarios `PROGRAMAS`, `ASIGNATURAS_2DAM`, `ASIGNATURAS_1DAM`, `MODULOS_CIBER`, `ASIGNATURAS_PFU`, puedes cambiar nombres, iconos, colores, cuatrimestres o añadir tus propias carreras y asignaturas.
3. **Resúmenes, Tests y Flashcards generados:**
   - Todo lo que generes se guarda automáticamente en tu ordenador dentro de `data/procesado/` (`guias/`, `examenes/`, `flashcards/`, `progreso.json`), por lo que es instantáneo y permanente.
