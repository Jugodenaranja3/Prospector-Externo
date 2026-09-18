# B6F — Reconciliación de endpoints históricos

El inventario DATAX contiene fuentes históricas. B6E demostró que varias URLs ya no representan la superficie actual de la misma institución.

B6F introduce `config/source_endpoint_reconciliation.yaml`.

La matriz NO borra historia y NO reemplaza silenciosamente una institución por su sucesora.

Estados típicos:

- `CURRENT_DOMAIN_CHANGED`
- `CURRENT_RECOVERED_HTTP`
- `CURRENT_TLS_CLIENT_INCOMPATIBILITY`
- `CURRENT_SERVICE_ENDPOINT_CANDIDATE`
- `RETIRED`
- `RETIRED_SUCCESSOR_IDENTIFIED`
- `ARCHIVE_ONLY`
- `HISTORICAL_DOMAIN_DEAD`

Acciones:

- `RETRY_CRAWL`
- `HOLD_ACCESS_REVIEW`
- `HOLD_TLS_REVIEW`
- `DEFER_B7_B8`
- `RETIRED_NO_SUBSTITUTION`
- `ARCHIVE_NO_LIVE_PROMOTION`
- `NEEDS_IDENTITY_REVIEW`

Solo `RETRY_CRAWL` entra al runner B6F.

El runner usa HTTP/HTML + sitemap + API discovery, sin browser y sin descargar binarios.

```powershell
python -m apps.source_reconciliation.main `
  --reconciliation .\config\source_endpoint_reconciliation.yaml `
  --output-dir .\.runtime\source_reconciliation
```
