# B10C.2 — Fallback a Chrome del sistema

## Contexto

El smoke de `statistics_denmark` alcanzó correctamente el
`JavascriptWorkflow`, pero el entorno no pudo descargar la revisión de
Chromium administrada por Playwright debido a timeouts repetidos.

Una prueba directa confirmó que Playwright sí puede lanzar el Chrome ya
instalado mediante `channel="chrome"`.

## Política implementada

`PlaywrightDriver` ahora:

1. intenta primero Chromium administrado por Playwright;
2. solo si el error indica que ese ejecutable no está instalado,
   intenta `channel="chrome"`;
3. no realiza descargas ni instalaciones automáticas;
4. no usa el fallback para errores ajenos a un browser ausente;
5. mantiene el reciclaje del navegador y el cierre defensivo de recursos.

Esto conserva una ejecución determinista cuando existe Chromium de
Playwright, pero permite operar en estaciones Windows donde el browser
administrado no puede descargarse y Chrome ya está instalado.

## Validación

El batch B10C.2 ejecuta tests focalizados y toda la suite offline.
Después se debe reintentar únicamente `statistics_denmark`.
