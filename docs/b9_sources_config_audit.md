# B9B.1 — Recuperación de integración y auditoría de `sources.yaml`

B9B implementó correctamente el runner checkpointed y pasó sus pruebas, pero la validación contra el archivo físico `config/sources.yaml` encontró solo 4 de las 41 fuentes operacionales.

Eso no invalida el checkpoint. Indica que `config/sources.yaml` todavía no contiene/promueve la matriz operacional construida durante B6–B8.

B9B.1 hace dos cosas:

1. conserva y versiona la integración checkpointed ya probada;
2. audita offline la forma real de `config/sources.yaml` y otros YAML de `config/`.

No hace crawl ni modifica `config/sources.yaml`.

El resultado se guarda en:

`.runtime/sources_config_audit/latest.json`

La siguiente fase debe construir/promover el config operativo usando la estructura real detectada, no inventar un schema nuevo.
