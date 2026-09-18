# B6B — Caracterización masiva HTTP/API

## Objetivo

B6A respondió qué puede hacer el baseline HTTP/HTML mínimo sobre las 52 fuentes.

B6B avanza una capa y caracteriza los 46 entrypoints físicos con una estrategia escalonada:

```text
mapping previo
   ↓
recovery_http       (solo cuando B6A terminó en EXECUTION_ERROR)
   ↓
expanded_html       (HTML + sitemap, budget mayor)
   ↓
api_enriched        (API discovery + documentación + paginación limitada)
   ↓
workflow hint / siguiente fase
```

B6B sigue sin usar navegador. Una fuente que continúa accesible pero sin recursos NO se marca automáticamente como JavaScript. Queda como `REACHABLE_NEEDS_DEEPER_REVIEW` y pasa a revisión de B7/B8.

## Evidencia

La salida se conserva en:

```text
.runtime/source_characterization/
├── latest.json
├── latest.csv
├── latest.md
├── candidate_workflows.yaml
└── physical_sources/
    └── <source_id>/
        ├── recovery_http/
        ├── expanded_html/
        └── api_enriched/
```

Cada stage conserva:

- `probe.yaml`;
- `crawl.log`;
- `crawl/`;
- `stage_result.json`.

El runtime continúa fuera de Git.

## Resume

Por defecto, si existe `stage_result.json`, el stage se reutiliza. Esto permite reanudar una corrida interrumpida sin volver a golpear todas las fuentes.

Para repetir todo:

```powershell
python -m apps.source_characterization.main `
  --inventory .\config\source_inventory.yaml `
  --mapping-report .\.runtime\source_mapping\latest.json `
  --report-dir .\.runtime\source_characterization `
  --force
```

Para ejecución normal/reanudable:

```powershell
python -m apps.source_characterization.main `
  --inventory .\config\source_inventory.yaml `
  --mapping-report .\.runtime\source_mapping\latest.json `
  --report-dir .\.runtime\source_characterization
```

## Interpretación

Estados esperados:

- `HTTP_HTML_CANDIDATE`
- `HTML_SITEMAP_CANDIDATE`
- `API_CANDIDATE`
- `REACHABLE_NEEDS_DEEPER_REVIEW`
- `ACCESS_RESTRICTED`
- `ROBOTS_REVIEW`
- `UNAVAILABLE`
- `EXECUTION_ERROR`

`candidate_workflows.yaml` es solo evidencia/entrada para el siguiente batch. No sustituye `config/sources.yaml`.

El siguiente paso tras revisar B6B será convertir los candidatos válidos en configuración operacional permanente y separar con evidencia las fuentes que deben ir a JavaScript o Custom.

## B6B.1 — endurecimiento de evidencia

B6B.1 corrige dos sesgos detectados tras la primera caracterización 52/52:

1. Los recursos aparecen en varios artefactos del crawler (mapa, compact, tree y snapshots). El contador `api_resources` ahora se deduplica por URL única.
2. Una cadena de redirects que termina en HTTP 403 con ejecución fallida ya no se oculta como `EXECUTION_ERROR`; se conserva como `ACCESS_RESTRICTED`.

Los resultados existentes pueden reindexarse **sin red** usando `--offline-reindex`.

```powershell
python -m apps.source_characterization.main `
  --inventory .\config\source_inventory.yaml `
  --mapping-report .\.runtime\source_mapping\latest.json `
  --report-dir .\.runtime\source_characterization `
  --offline-reindex
```
