# B6G — Cierre de la fase HTTP/API

B6G consolida la evidencia acumulada desde B6A hasta B6F en una matriz única:

`config/source_resolution.yaml`

La matriz contiene las 52 fuentes lógicas y registra:

- entrypoint histórico;
- entrypoint efectivo cuando fue reconciliado;
- lifecycle;
- estado B6;
- ruta siguiente;
- workflow estratégico;
- si API discovery sigue siendo auxiliar;
- si debe revisarse con browser;
- si requiere custom review;
- evidencia de recursos y calidad API.

## Principios

- Una API CMS no se promociona automáticamente a workflow API.
- Una fuente retirada no se sustituye silenciosamente por su sucesor.
- Un 403 no se intenta evadir.
- Un endpoint accesible sin recursos después de HTML+sitemap+API pasa a B7/B8.
- La matriz no reemplaza todavía `config/sources.yaml`; es la decisión de cierre de B6.

## Ejecución

```powershell
python -m apps.source_resolution.main `
  --capabilities .\config\source_capabilities.yaml `
  --audit-report .\.runtime\source_evidence_audit\latest.json `
  --reconciliation .\config\source_endpoint_reconciliation.yaml `
  --reconciliation-run .\.runtime\source_reconciliation\latest.json `
  --output-yaml .\config\source_resolution.yaml `
  --report-dir .\.runtime\source_resolution
```

Después de B6G, la siguiente fase es B7: caracterización JavaScript/browser únicamente para las fuentes marcadas `B7_BROWSER_CHARACTERIZATION`.
