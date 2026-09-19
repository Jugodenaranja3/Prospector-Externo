# B10 — Cierre final de remediación live

## Resultado

B10 se cierra como `CLOSED_WITH_EXTERNAL_BLOCKERS`.

La corrida autoritativa original contenía 41 fuentes operacionales. De ellas:

- 18 ya estaban limpias y no requerían remediación.
- 23 fueron identificadas como afectadas por fallo o resultado vacío.
- 21 de esas 23 fueron remediadas y terminaron en `SUCCESS`.
- 2 conservan fallos externos reproducibles que no deben ocultarse con bypasses.

Reconciliación operacional:

- 39 fuentes operacionales con ejecución satisfactoria.
- 2 fuentes operacionales bloqueadas por condiciones externas.
- 41/41 fuentes operacionales contabilizadas.
- 11 fuentes adicionales permanecen `status-only` según la resolución previa.
- Inventario total: 52 fuentes.

## Bloqueos externos aceptados

### MHE

Estado de remediación: `FAILED / ROBOTS_UNREACHABLE`.

Diagnóstico live desde la estación DATAX:

- `https://www.mhe.gob.bo/` y su `robots.txt` fallaron por validación de
  certificado TLS.
- `https://www.hidrocarburos.gob.bo/` presentó el mismo fallo TLS.
- el host alternativo sin `www` de hidrocarburos agotó timeout durante el
  diagnóstico.

Decisión: no usar `verify=False`, no desactivar TLS y no convertir el fallo en
éxito artificial.

### SIGMA

Estado de remediación: `FAILED / ROBOTS_UNREACHABLE`.

Diagnóstico live:

- la portada respondió HTTP 200;
- `/robots.txt` respondió HTTP 500 tanto con `www` como sin `www`.

Decisión: no habilitar `ignore_robots_txt` para transformar un fallo externo de
robots en un éxito artificial.

## Remediaciones principales consolidadas

Durante B10 se corrigieron, entre otros:

- separación entre presupuesto de navegación y recursos;
- propagación del exit code de fallos;
- ejecución real del workflow JavaScript;
- fallback controlado desde Chromium administrado por Playwright hacia Chrome
  del sistema cuando el binario administrado no existe;
- guardas HTML para contenido binario/no-markup;
- promoción declarativa de fuentes custom;
- correcciones de hosts canónicos y redirects oficiales;
- Statistics Denmark hacia la API pública StatBank;
- Data.gov hacia la superficie pública `catalog.data.gov`.

## Política de cierre

El auditor `apps.b10_closure_audit.main` no cambia estados ni falsea resultados.
Solo acepta un cierre cuando:

1. las 23 fuentes afectadas están presentes en el state de remediación;
2. toda fuente no bloqueada está en `SUCCESS`;
3. los únicos fallos tolerados son los bloqueos externos explícitamente
   reconocidos para MHE y SIGMA con `ROBOTS_UNREACHABLE`;
4. no existen `EMPTY_REVIEW` ni fallos adicionales sin resolver.

Si cualquiera de esas condiciones cambia, el auditor vuelve a estado `OPEN`.
