# SIMULACRO DE EXAMEN — PSP (TEMA1)
### Gestión de Procesos y Concurrencia en Java
> **Total preguntas:** 3 | **Formato:** Tipo Test UNIR (4 opciones)

---

### ❓ Pregunta 1
**¿Cuál es la principal diferencia entre un proceso y un hilo (thread)?**

- **A)** Un proceso comparte memoria con otros procesos, mientras que los hilos tienen memoria aislada.
- **B)** Un proceso tiene su propio espacio de direcciones de memoria aislado, mientras que los hilos de un mismo proceso comparten memoria.
- **C)** Java no permite crear procesos nativos, solo hilos gestionados por la JVM.
- **D)** Los procesos siempre se ejecutan en modo kernel y los hilos en modo usuario.

<details>
<summary>👉 Toca para ver la respuesta correcta y justificación</summary>

**Respuesta Correcta:** `B`

**Justificación:** Los procesos poseen aislamiento de memoria en el sistema operativo, lo que aporta robustez. Los hilos comparten el segmento de datos del proceso padre.
</details>

---

### ❓ Pregunta 2
**En Java, ¿qué método de la clase Process bloquea el hilo actual hasta que el subproceso finaliza y devuelve su código de retorno?**

- **A)** p.start()
- **B)** p.destroy()
- **C)** p.waitFor()
- **D)** p.getInputStream()

<details>
<summary>👉 Toca para ver la respuesta correcta y justificación</summary>

**Respuesta Correcta:** `C`

**Justificación:** waitFor() causa que el hilo invocador espere hasta que el proceso representado por el objeto Process haya finalizado, devolviendo el código de salida (exit code).
</details>

---

### ❓ Pregunta 3
**¿Qué ventaja principal ofrece la clase ProcessBuilder frente a Runtime.getRuntime().exec() en Java?**

- **A)** ProcessBuilder permite configurar el entorno de variables, el directorio de trabajo y redirigir flujos de E/S de forma más flexible y segura.
- **B)** ProcessBuilder ejecuta los comandos directamente en la GPU para mayor velocidad.
- **C)** ProcessBuilder solo funciona en Windows y MacOS, no en distribuciones Linux.
- **D)** ProcessBuilder no genera excepciones verificadas de tipo IOException.

<details>
<summary>👉 Toca para ver la respuesta correcta y justificación</summary>

**Respuesta Correcta:** `A`

**Justificación:** ProcessBuilder introdujo en Java 5 una API modular orientada a objetos para configurar argumentos, variables de entorno y redirección de flujos estándar antes de iniciar el proceso.
</details>

---
