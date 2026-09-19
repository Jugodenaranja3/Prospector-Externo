# B10C.1.1 — Follow-up del smoke selectivo

El primer intento de B10C.1 no modificó el repositorio: el aplicador buscaba
un bloque textual exacto de `JavascriptWorkflow` que ya había cambiado en B10C.

Esta revisión aplica el hardening mediante una transformación estructural
más pequeña y verificable.

## Statistics Denmark

El workflow JavaScript ya se ejecuta. El smoke falló porque Playwright está
instalado como paquete Python, pero el binario Chromium correspondiente no
está instalado en el entorno.

El runtime ahora captura esa excepción y la clasifica como:

`PLAYWRIGHT_BROWSER_MISSING`

en lugar de dejarla escapar como `UNHANDLED_WORKFLOW_EXCEPTION`.

La instalación del navegador sigue siendo explícita y externa al código:

`python -m playwright install chromium`

## ANAPO

El entrypoint configurado con `www` responde con redirect al dominio oficial
sin `www`. Se añade únicamente `anapobolivia.org` a `allowed_hosts`.

No se relaja globalmente la política de redirects.

## Rerun

Después de instalar Chromium se reintentan exclusivamente:

- `statistics_denmark`
- `anapo`

usando el estado B10C existente y `--resume`.
