# B13 — Cierre final, tiempo y límites

La corrida `b13-e2e` es la ejecución final reproducible de las 41 fuentes
operacionales.

El cierre valida el paquete final, los 38 JSON DATAX, TRANSTATS como
`ACQUISITION_JOB_READY`, MHE/SIGMA como bloqueos externos y cero pendientes.

## El crawl no es ilimitado

El Prospector trabaja con discovery acotado. Las configuraciones pueden incluir
`max_urls`, `max_runtime_seconds`, `max_depth`, `max_query_variants` y
`max_calendar_variants`, además de deduplicación y protección contra spider
traps.

Por tanto, el informe final debe usar la formulación:

**crawleo final reproducible bajo límites operacionales configurados**

y no afirmar que se rastreó sin límites cada página de cada sitio.

## Tiempo

El auditor calcula desde los 41 reportes:

- suma de duración activa de los crawls;
- ventana primer inicio → último fin;
- ventana primer crawl → estado B13 READY;
- finalización offline posterior;
- diez fuentes más lentas;
- distribución de `stop_reason`.

La ventana completa puede incluir la pausa entre el canary y `--resume`.
