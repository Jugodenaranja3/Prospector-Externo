# B11B — Proyección DATAX bulk offline

## Motivo

B11A confirmó que 39 fuentes operacionales contienen recursos raw reales, pero
ninguna evidencia B10 contiene todavía una proyección legacy `ESTADISTICAS`.

B11B reutiliza el componente existente `apps.datax_projection.main`; no crea
otro exporter ni duplica la política de proyección.

## Estrategia

Para cada fuente clasificada por B11A como `RAW_READY_NO_PROJECTION`:

1. toma su `evidence_root` real;
2. resuelve el `source_id` físico persistido;
3. busca un contrato `config/grouping/<logical>.yaml`;
4. si no existe, intenta el contrato del `source_id` físico;
5. ejecuta `apps.datax_projection.main` sobre el snapshot ya persistido;
6. no hace crawl ni HTTP;
7. registra stdout/stderr por fuente;
8. vuelve a buscar JSON con raíz `ESTADISTICAS` y cuenta registros válidos.

Estados de B11B:

- `PROJECTED`: comando exitoso y al menos un registro legacy completo;
- `EMPTY_PROJECTION`: comando exitoso, pero cero registros legacy;
- `COMMAND_FAILED`: la proyección existente falló para esa evidencia;
- `DRY_RUN`: selección y comandos resueltos sin ejecutarlos.

## Archivos runtime

- `.runtime/b11_bulk_projection/latest.json`
- `.runtime/b11_bulk_projection/logs/<logical_source_id>.log`

Los outputs legacy son artefactos de ejecución y permanecen bajo el
`evidence_root` de cada fuente. No se versionan mediante este batch.

Después de B11B debe ejecutarse nuevamente `apps.b11_output_audit.main` para
medir cuántas fuentes pasan de `RAW_READY_NO_PROJECTION` a `DATAX_READY`.
