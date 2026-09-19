# B12A — Paquete consolidado de entrega DATAX

## Objetivo

B11 cerró la readiness downstream de las 41 fuentes operacionales:

- 38 `DATAX_READY`;
- 1 `ACQUISITION_JOB_READY` (TRANSTATS);
- 2 bloqueos externos (MHE y SIGMA).

B12A transforma esa evidencia distribuida en una entrega única y transportable
sin cambiar el contrato legacy de cada fuente.

## Decisión de empaquetado

No se fusionan a ciegas los árboles `ESTADISTICAS` de las 38 fuentes. Algunas
fuentes lógicas comparten una configuración física y una fusión destructiva
podría provocar colisiones de claves o borrar provenance.

La entrega se consolida como un paquete:

```text
output/datax-package/latest/
├── manifest.json
├── README.md
├── sources.csv
├── checksums.sha256
├── external_blockers.json
├── sources/
│   ├── <logical_source_id>.json
│   └── ...
└── special/
    └── transtats/
        └── acquisition_job.json
```

Cada `sources/<id>.json` conserva exactamente el contrato legacy con raíz
`ESTADISTICAS`. La consolidación ocurre en el nivel de paquete e índice, no
reescribiendo la semántica interna de cada fuente.

Además se genera:

`output/datax-package/datax_package_latest.zip`

## Validaciones

El builder exige:

1. 41 fuentes operacionales en el audit B11;
2. exactamente 38 `DATAX_READY`;
3. exactamente TRANSTATS como `ACQUISITION_JOB_READY`;
4. exactamente MHE y SIGMA como `EXTERNAL_BLOCKER`;
5. cero estados unresolved;
6. un único `legacy_estadisticas.json` canónico válido por fuente DATAX;
7. los seis campos mínimos en todos los registros;
8. 5407 registros legacy totales, igual al cierre B11;
9. checksums SHA-256 de los artefactos;
10. ZIP autocontenido.

No se hace HTTP, no se hace crawl y no se descargan binarios remotos.
