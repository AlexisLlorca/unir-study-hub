#!/usr/bin/env python3
"""
Genera la baraja completa oficial de Flashcards para todas las materias de 1º y 2º DAM.
"""
import json
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
MAZOS_FILE = BASE_DIR / "data" / "procesado" / "flashcards" / "mazos.json"

TARJETAS_DAM = [
    # ACCESO A DATOS
    {
        "id": "dam_ad_1",
        "asignatura": "Acceso a Datos",
        "anverso": "¿Qué es la anotación `@Entity` y `@Id` en JPA / Hibernate?",
        "reverso": "`@Entity` indica que la clase Java se mapea a una tabla relacional de la BD. `@Id` define la clave primaria (Primary Key) de dicha entidad.",
        "nivel": "básico",
        "estado": "nuevo",
        "repasos": 0
    },
    {
        "id": "dam_ad_2",
        "asignatura": "Acceso a Datos",
        "anverso": "¿Cuál es la diferencia entre `FetchType.LAZY` y `FetchType.EAGER`?",
        "reverso": "**LAZY:** Los datos relacionados solo se cargan de la BD cuando se accede a ellos explícitamente (ahorra memoria). **EAGER:** Se cargan inmediatamente mediante un JOIN (riesgo de problemas de rendimiento N+1).",
        "nivel": "avanzado",
        "estado": "nuevo",
        "repasos": 0
    },
    {
        "id": "dam_ad_3",
        "asignatura": "Acceso a Datos",
        "anverso": "¿Por qué debe utilizarse `try-with-resources` con flujos de ficheros (`FileReader`, `FileWriter`)?",
        "reverso": "Garantiza el **cierre automático (`close`)** del flujo al salir del bloque `try` implementando `AutoCloseable`, evitando fugas de memoria y bloqueos de ficheros en el sistema operativo.",
        "nivel": "intermedio",
        "estado": "nuevo",
        "repasos": 0
    },

    # PROGRAMACIÓN DE SERVICIOS Y PROCESOS
    {
        "id": "dam_psp_1",
        "asignatura": "Programación de Servicios y Procesos",
        "anverso": "¿Qué diferencia existe entre un Proceso y un Hilo (Thread)?",
        "reverso": "Un **proceso** es un programa en ejecución con su propio espacio de memoria aislado y recursos independientes. Un **hilo** es una unidad de ejecución ligera dentro de un proceso que comparte memoria y recursos con otros hilos del mismo proceso.",
        "nivel": "básico",
        "estado": "nuevo",
        "repasos": 0
    },
    {
        "id": "dam_psp_2",
        "asignatura": "Programación de Servicios y Procesos",
        "anverso": "¿Qué ocurre si no sincronizas una variable compartida modificada por múltiples hilos?",
        "reverso": "Se produce una **condición de carrera (Race Condition)** e inconsistencia de datos, ya que las operaciones no son atómicas. Se soluciona usando `synchronized`, `ReentrantLock` o clases atómicas (`AtomicInteger`).",
        "nivel": "intermedio",
        "estado": "nuevo",
        "repasos": 0
    },
    {
        "id": "dam_psp_3",
        "asignatura": "Programación de Servicios y Procesos",
        "anverso": "¿Qué diferencia hay entre un Socket TCP y un Socket UDP en Java?",
        "reverso": "**TCP (`ServerSocket` / `Socket`):** Protocolo orientado a conexión, fiable, garantiza entrega ordenada de paquetes y control de flujo. **UDP (`DatagramSocket` / `DatagramPacket`):** No orientado a conexión, más rápido y ligero pero sin confirmación de entrega (streaming/videojuegos).",
        "nivel": "intermedio",
        "estado": "nuevo",
        "repasos": 0
    },

    # PROGRAMACIÓN MULTIMEDIA Y DISPOSITIVOS MÓVILES
    {
        "id": "dam_pmdm_1",
        "asignatura": "Programación Multimedia y Dispositivos Móviles",
        "anverso": "¿Cuál es el ciclo de vida de un `Activity` en Android desde que se abre hasta segundo plano?",
        "reverso": "`onCreate()` ➔ `onStart()` ➔ `onResume()` (activa). Si pasa a segundo plano: `onPause()` ➔ `onStop()`. Si el sistema la destruye por memoria: `onDestroy()`.",
        "nivel": "intermedio",
        "estado": "nuevo",
        "repasos": 0
    },
    {
        "id": "dam_pmdm_2",
        "asignatura": "Programación Multimedia y Dispositivos Móviles",
        "anverso": "¿Qué ventaja principal ofrece Jetpack Compose frente al sistema tradicional de vistas XML?",
        "reverso": "Es un kit de herramientas **declarativo en Kotlin**, que elimina el desacople entre XML y código, reduce el código repetitivo (`findViewById`, binding) y recompone la UI automáticamente cuando cambia el estado.",
        "nivel": "intermedio",
        "estado": "nuevo",
        "repasos": 0
    },

    # DESARROLLO DE INTERFACES
    {
        "id": "dam_di_1",
        "asignatura": "Desarrollo de Interfaces",
        "anverso": "¿Qué es JavaFX y cuál es el rol de un archivo FXML con Scene Builder?",
        "reverso": "JavaFX es el framework moderno de UI para escritorio en Java. Los archivos **FXML** definen la estructura visual de la interfaz en formato XML desacoplándola de la lógica del controlador (`@FXML initialize()`).",
        "nivel": "básico",
        "estado": "nuevo",
        "repasos": 0
    },
    {
        "id": "dam_di_2",
        "asignatura": "Desarrollo de Interfaces",
        "anverso": "¿Qué diferencia hay entre los layouts `VBox`, `HBox` y `GridPane` en JavaFX?",
        "reverso": "**VBox:** Apila elementos en una sola columna vertical. **HBox:** Coloca elementos en una fila horizontal. **GridPane:** Organiza controles en una rejilla bidimensional flexible por filas y columnas.",
        "nivel": "básico",
        "estado": "nuevo",
        "repasos": 0
    },

    # SISTEMAS DE GESTIÓN EMPRESARIAL
    {
        "id": "dam_sge_1",
        "asignatura": "Sistemas de Gestión Empresarial",
        "anverso": "¿Qué es un sistema ERP y cuál es la diferencia entre un módulo core y uno personalizado?",
        "reverso": "Un **ERP (Enterprise Resource Planning)** centraliza los procesos del negocio (facturación, compras, inventario, RRHH). Los módulos **core** son nativos y estándar, mientras que los **custom** adaptan flujos específicos sin alterar el núcleo.",
        "nivel": "básico",
        "estado": "nuevo",
        "repasos": 0
    },
    {
        "id": "dam_sge_2",
        "asignatura": "Sistemas de Gestión Empresarial",
        "anverso": "En Odoo ERP, ¿cuál es el papel del archivo `__manifest__.py` en un módulo?",
        "reverso": "Es el fichero descriptor de configuración en Python: declara el nombre del módulo, versión, dependencias con otros módulos (`depends`), autores, vistas XML que debe cargar (`data`) y licencia.",
        "nivel": "intermedio",
        "estado": "nuevo",
        "repasos": 0
    },

    # ARQUITECTURAS EN LA NUBE
    {
        "id": "dam_nube_1",
        "asignatura": "Arquitecturas en la Nube",
        "anverso": "¿Qué diferencia existe entre los modelos de servicio IaaS, PaaS y SaaS?",
        "reverso": "**IaaS (Infraestructura):** Alquilas máquinas virtuales, red y almacenamiento (ej. EC2). **PaaS (Plataforma):** El proveedor gestiona el SO y runtime, tú solo despliegas código. **SaaS (Software):** Aplicación completa lista para usar como usuario final.",
        "nivel": "básico",
        "estado": "nuevo",
        "repasos": 0
    },

    # PROYECTO INTERMODULAR DAM
    {
        "id": "dam_pi_1",
        "asignatura": "Proyecto Intermodular DAM",
        "anverso": "¿Cuáles son las fases y entregas oficiales del Proyecto Intermodular?",
        "reverso": "1. **Anteproyecto** (propuesta y equipo).\n2. **3 Entregas mensuales** de avance y código.\n3. **Depósito final** (70% de la nota, rúbrica oficial).\n4. **Defensa oral ante tribunal** (30% de la nota).",
        "nivel": "básico",
        "estado": "nuevo",
        "repasos": 0
    },

    # ITINERARIO PARA LA EMPLEABILIDAD II
    {
        "id": "dam_ipe_1",
        "asignatura": "Itinerario para la Empleabilidad II",
        "anverso": "¿Qué diferencia existe entre un despido objetivo y un despido disciplinario?",
        "reverso": "**Despido objetivo:** Por causas económicas, técnicas, organizativas o de producción (indemnización de 20 días por año trabajado). **Despido disciplinario:** Por incumplimiento grave del trabajador (sin indemnización económica).",
        "nivel": "básico",
        "estado": "nuevo",
        "repasos": 0
    },

    # INGLÉS PROFESIONAL
    {
        "id": "dam_ing_1",
        "asignatura": "Inglés Profesional",
        "anverso": "What are the essential components of an IT Incident / Bug Report?",
        "reverso": "1. **Summary / Title** (short and clear).\n2. **Environment** (OS, browser, app version).\n3. **Steps to Reproduce** (numbered sequence).\n4. **Expected Result** vs **Actual Result**.\n5. **Severity / Priority** and attachments (logs/screenshots).",
        "nivel": "intermedio",
        "estado": "nuevo",
        "repasos": 0
    },

    # DIGITALIZACIÓN APLICADA
    {
        "id": "dam_dig_1",
        "asignatura": "Digitalización Aplicada",
        "anverso": "¿Qué diferencia existe entre Cloud Computing y Edge Computing?",
        "reverso": "**Cloud Computing:** El procesamiento se centraliza en servidores remotos en la nube. **Edge Computing:** El procesamiento se realiza en el borde, cerca de los dispositivos IoT donde se generan los datos (reduce latencia y ancho de banda).",
        "nivel": "intermedio",
        "estado": "nuevo",
        "repasos": 0
    },

    # SOSTENIBILIDAD APLICADA
    {
        "id": "dam_sost_1",
        "asignatura": "Sostenibilidad Aplicada",
        "anverso": "¿Qué mide el indicador PUE (Power Usage Effectiveness) en un Centro de Datos?",
        "reverso": "Mide la **eficiencia energética** dividiendo la energía total consumida por el centro de datos entre la energía consumida por el equipamiento IT de cómputo. El valor ideal teórico es 1.0.",
        "nivel": "intermedio",
        "estado": "nuevo",
        "repasos": 0
    },

    # 1º DAM - PROGRAMACIÓN
    {
        "id": "dam_p1_1",
        "asignatura": "1º DAM - Programación",
        "anverso": "¿Cuáles son los 4 pilares de la Programación Orientada a Objetos (POO)?",
        "reverso": "1. **Encapsulamiento:** Ocultación del estado interno y control de acceso vía métodos.\n2. **Abstracción:** Modelar lo esencial simplificando la complejidad.\n3. **Herencia:** Reutilización y jerarquía de clases padre/hijo.\n4. **Polimorfismo:** Capacidad de objetos de responder de forma distinta al mismo mensaje.",
        "nivel": "básico",
        "estado": "nuevo",
        "repasos": 0
    },
    {
        "id": "dam_p1_2",
        "asignatura": "1º DAM - Programación",
        "anverso": "¿Qué diferencia hay en Java entre una clase abstracta y una interfaz (`interface`)?",
        "reverso": "Una **clase abstracta** puede tener estado (atributos con valor) y métodos implementados junto a métodos abstractos (herencia simple). Una **interfaz** define un contrato de comportamiento puro que una clase puede implementar de forma múltiple.",
        "nivel": "intermedio",
        "estado": "nuevo",
        "repasos": 0
    },

    # 1º DAM - BASES DE DATOS
    {
        "id": "dam_bd_1",
        "asignatura": "1º DAM - Bases de Datos",
        "anverso": "¿Qué significa el acrónimo ACID en transacciones de bases de datos relacionales?",
        "reverso": "**A (Atomicidad):** Todo o nada en la transacción.\n**C (Consistencia):** La BD pasa de un estado válido a otro válido.\n**I (Aislamiento):** Las transacciones concurrentes no interfieren entre sí.\n**D (Durabilidad):** Los cambios confirmados son permanentes tras un fallo.",
        "nivel": "intermedio",
        "estado": "nuevo",
        "repasos": 0
    },
    {
        "id": "dam_bd_2",
        "asignatura": "1º DAM - Bases de Datos",
        "anverso": "¿Qué diferencia hay entre `INNER JOIN` y `LEFT JOIN` en SQL?",
        "reverso": "**INNER JOIN:** Devuelve solo las filas que coinciden en ambas tablas según la condición.\n**LEFT JOIN:** Devuelve todas las filas de la tabla izquierda y las coincidentes de la derecha (rellenando con `NULL` si no hay coincidencia).",
        "nivel": "básico",
        "estado": "nuevo",
        "repasos": 0
    },

    # 1º DAM - SISTEMAS INFORMÁTICOS
    {
        "id": "dam_si_1",
        "asignatura": "1º DAM - Sistemas Informáticos",
        "anverso": "En Linux, ¿qué permisos otorga el comando `chmod 755 script.sh`?",
        "reverso": "Propietario: **7 (rwx = Lectura, Escritura, Ejecución)**.\nGrupo: **5 (r-x = Lectura y Ejecución)**.\nOtros: **5 (r-x = Lectura y Ejecución)**.",
        "nivel": "básico",
        "estado": "nuevo",
        "repasos": 0
    },

    # 1º DAM - LENGUAJES DE MARCAS
    {
        "id": "dam_lm_1",
        "asignatura": "1º DAM - Lenguajes de Marcas",
        "anverso": "¿Cuál es la diferencia entre un documento XML bien formado y uno válido?",
        "reverso": "**Bien formado:** Cumple las reglas sintácticas básicas de XML (etiquetas cerradas, jerarquía, un único elemento raíz).\n**Válido:** Además de estar bien formado, se ajusta estrictamente a una gramática predefinida mediante un esquema **DTD o XSD**.",
        "nivel": "básico",
        "estado": "nuevo",
        "repasos": 0
    },

    # 1º DAM - DESARROLLO DE ENTORNOS
    {
        "id": "dam_ed_1",
        "asignatura": "1º DAM - Desarrollo de Entornos",
        "anverso": "En Git, ¿cuál es la diferencia entre `git fetch` y `git pull`?",
        "reverso": "**`git fetch`:** Descarga los cambios y ramas del repositorio remoto a tu máquina local sin fusionarlos con tu código actual.\n**`git pull`:** Realiza un `git fetch` seguido automáticamente de un `git merge` integrando los cambios en tu rama actual.",
        "nivel": "básico",
        "estado": "nuevo",
        "repasos": 0
    },

    # 1º DAM - ITINERARIO PARA LA EMPLEABILIDAD I
    {
        "id": "dam_fol_1",
        "asignatura": "1º DAM - Itinerario para la Empleabilidad I",
        "anverso": "¿Qué principio de jerarquía normativa laboral establece la regla 'in dubio pro operario'?",
        "reverso": "Establece que en caso de duda o ambigüedad en la interpretación de una norma laboral aplicable, los jueces deben interpretarla en el sentido **más favorable para el trabajador**.",
        "nivel": "básico",
        "estado": "nuevo",
        "repasos": 0
    },

    # 1º DAM - INGLÉS TÉCNICO I
    {
        "id": "dam_ing1_1",
        "asignatura": "1º DAM - Inglés Técnico I",
        "anverso": "In computer architecture, what is the purpose of the CPU Cache memory?",
        "reverso": "It is a small, ultra-fast volatile memory built directly inside the CPU that stores frequently accessed instructions and data to minimize wait times from RAM (**L1, L2, L3 caches**).",
        "nivel": "básico",
        "estado": "nuevo",
        "repasos": 0
    }
]

def actualizar():
    datos = {}
    if MAZOS_FILE.exists():
        try:
            datos = json.loads(MAZOS_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    datos["dam"] = TARJETAS_DAM
    MAZOS_FILE.parent.mkdir(parents=True, exist_ok=True)
    MAZOS_FILE.write_text(json.dumps(datos, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"✔ Mazos actualizados: {len(TARJETAS_DAM)} flashcards para DAM en mazos.json.")

if __name__ == "__main__":
    actualizar()
