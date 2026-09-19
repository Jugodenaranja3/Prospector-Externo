# Entrega final del Prospector Externo DATAX

## Estado final

La ejecución reproducible `b13-e2e` quedó cerrada correctamente.

- Inventario: **52 fuentes lógicas**
- Operacionales: **41**
- Status-only: **11**
- `DATAX_READY`: **38**
- `ACQUISITION_JOB_READY`: **1** (TRANSTATS)
- `EXTERNAL_BLOCKER`: **2** (MHE y SIGMA)
- Pendientes: **0**
- Unresolved: **0**
- Registros legacy producidos: **12104**
- JSON DATAX finales: **38**
- Duplicados cross-physical: **0**
- ZIP SHA-256: `f52bf8d1f62d887420517f3d60dd8911f9d3ca5ebf258d5cf58f8369f7326e70`

## Tiempo de la corrida final

- Tiempo activo acumulado de crawl: **1 h 51 min 21 s**
- Ventana desde el primer inicio hasta el último fin de crawl: **1 h 52 min 13 s**
- Ventana desde el primer crawl hasta el estado B13 `READY`: **1 h 52 min 59 s**
- Proyección/empaquetado final posterior al último crawl: **45 s**

Para el informe, la métrica más representativa del tiempo de corrida completa
es **1 h 52 min 59 s**, mientras que la suma del
tiempo efectivo dedicado por las fuentes al crawling fue
**1 h 51 min 21 s**.

## Alcance real del crawling

La ejecución final **no fue un rastreo ilimitado de todas las páginas de todos
los sitios**. El Prospector utiliza límites operacionales para evitar spider
traps, calendarios infinitos, explosión de parámetros y consumo de red sin
control.

Fuentes que terminaron por un `MAX_*`: **8**.

Stop reasons observados:

`{"API_SEEDS_EXHAUSTED": 2, "BROWSER_PAGE_LIMIT": 1, "CUSTOM_FORM_CONFIRMED": 1, "MAX_CONSECUTIVE_ERRORS": 7, "MAX_URLS": 1, "QUEUE_EXHAUSTED": 14, "REQUEST_BUDGET_REACHED": 15}`

La redacción recomendada para el informe es:

> Se ejecutó un crawleo final reproducible sobre las 41 fuentes operacionales,
> aplicando las políticas de cobertura, seguridad y límites de exploración
> configuradas en el Prospector.

## Fuentes con mayor duración

- `dgac`: 7 min 29 s (`REQUEST_BUDGET_REACHED`)
- `cadexco`: 6 min 11 s (`MAX_CONSECUTIVE_ERRORS`)
- `asofin`: 5 min 45 s (`REQUEST_BUDGET_REACHED`)
- `ine`: 5 min 40 s (`REQUEST_BUDGET_REACHED`)
- `snis`: 5 min 28 s (`QUEUE_EXHAUSTED`)
- `ada`: 4 min 20 s (`REQUEST_BUDGET_REACHED`)
- `mdryt`: 4 min 17 s (`REQUEST_BUDGET_REACHED`)
- `data_gov`: 4 min 16 s (`REQUEST_BUDGET_REACHED`)
- `mdryt_oap`: 4 min 14 s (`REQUEST_BUDGET_REACHED`)
- `cndc`: 4 min 12 s (`REQUEST_BUDGET_REACHED`)

## Artefactos definitivos

Resultados DATAX:

`output/b13-pipeline/b13-e2e/datax-package/latest/`

Paquete comprimido:

`output/b13-pipeline/b13-e2e/datax-package/datax_package.zip`

Evidencia raw completa:

`output/checkpointed/b13-e2e/`

Carpeta simplificada de entrega:

`output/final-delivery/`

El entry point empresarial es `manifest.json`; para una fuente
`DATAX_READY`, el artefacto legacy está en
`sources/<logical_source_id>.json`.
