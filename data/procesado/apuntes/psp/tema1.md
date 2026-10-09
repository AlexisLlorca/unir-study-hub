# PSP — TEMA1: Gestión de Procesos y Concurrencia
> **Documento generado automáticamente por UNIR Study Hub v2.0**

---

## 📌 1. Resumen Ejecutivo del Tema
En este tema se estudian los fundamentos de la programación concurrente y la gestión de procesos en sistemas operativos modernos. Se profundiza en la clase ProcessBuilder de Java para invocar subprocesos nativos, redirigir flujos estándar (stdin, stdout, stderr) y controlar códigos de retorno.

---

## 🧠 2. Conceptos Clave y Definiciones de Examen
| Concepto / Término | Definición Técnica | Importancia en Examen |
| :--- | :--- | :--- |
| **Proceso** | Programa en ejecución con su propio espacio de memoria aislado e independiente. | `Alta (Examen)` |
| **Hilo (Thread)** | Unidad básica de ejecución dentro de un proceso. Comparte memoria con otros hilos. | `Alta (Examen)` |
| **ProcessBuilder** | Clase de Java que permite crear y configurar procesos del sistema operativo. | `Práctica Clave` |

---

## 💻 3. Código Vivo y Ejemplos Prácticos
### Creación básica de un proceso en Java
*Lanza el comando 'ls -la' en Linux y espera su código de salida.*

```java
ProcessBuilder pb = new ProcessBuilder("ls", "-la");
Process p = pb.start();
int exitCode = p.waitFor();
System.out.println("Código de salida: " + exitCode);
```

---

## 🎯 4. Puntos a Recordar para el Examen
- Repasar las definiciones de la tabla antes de entrar a la prueba.
- Practicar la sintaxis del código ejecutándolo en tu IDE local.