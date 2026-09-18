# Mapeo masivo de fuentes — BATCH 6A

## Propósito

BATCH 6A crea una **fotografía conservadora del nivel de cobertura actual** del Prospector Externo sobre el inventario histórico de 52 fuentes lógicas.

No representa una validación 100% de cada fuente y no sustituye el trabajo posterior de B6/B7/B8/B9/B10.

## Inventario

`config/source_inventory.yaml` congela las 52 fuentes lógicas recuperadas del `benchmark_runner.py` histórico.

El inventario conserva nombres y entrypoints históricos, no corrige automáticamente URLs sospechosas, mantiene fuentes lógicas diferentes aunque compartan entrypoint y no asigna workflows definitivos.

`config/sources.yaml` continúa siendo la configuración operativa del crawler.

## Perfil de probe

`apps.source_mapping` usa el crawler real mediante `apps.crawler_batch.main`, pero genera configuraciones efímeras con un perfil deliberadamente barato:

- workflow baseline `html`;
- máximo 5 requests por entrypoint;
- máximo 20 s por entrypoint;
- profundidad 1;
- 40 URLs máximas;
- robots respetado;
- sin sitemap;
- sin API discovery;
- sin Playwright/browser;
- sin descarga binaria.

Una fuente accesible sin recursos queda como `REACHABLE_NO_RESOURCES`; B6/B7 decidirá después si necesita mejores seeds, API, JavaScript u otro workflow.

## Ejecución

```powershell
python -m apps.source_mapping.main `
  --inventory .\config\source_inventory.yaml `
  --report-dir .\.runtime\source_mapping
```

`.runtime/` está ignorada por Git y puede eliminarse en cualquier momento.

El comando genera solo:

```text
.runtime/source_mapping/
├── latest.json
├── latest.csv
└── latest.md
```

Los outputs intermedios por fuente se crean en el directorio temporal del sistema y se destruyen automáticamente.
