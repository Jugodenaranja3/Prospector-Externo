# B7C — Auditoría semántica de evidencia Browser

B7B demostró que JavaScript aporta evidencia nueva para varias fuentes, pero una respuesta JSON/XHR no es automáticamente un dataset.

B7C trabaja **offline** con:

- `config/browser_candidates.yaml`;
- `.runtime/browser_characterization/latest.json`;
- `network_events.json` por fuente.

## Qué distingue

Archivos:

- `STRUCTURED_FILE`
- `DOCUMENT_FILE`
- `ARCHIVE_FILE`
- `OTHER_FILE`

Tráfico browser:

- `DATA_ENDPOINT`
- `SAME_ORIGIN_JSON`
- `SAME_ORIGIN_XHR`
- `CMS_OR_GENERIC_BACKEND`
- `ANALYTICS_TELEMETRY`
- `THIRD_PARTY_JSON`
- `THIRD_PARTY_XHR`

La telemetría/analytics no se cuenta como evidencia de datasets.

## Resolución

- `READY_JAVASCRIPT_STRUCTURED_FILES`
- `READY_JAVASCRIPT_FILES`
- `READY_JAVASCRIPT_DATA_NETWORK`
- `JAVASCRIPT_NETWORK_REVIEW`
- `B8_CUSTOM_CANDIDATE`

Solo las evidencias suficientemente claras reciben `workflow_strategy: javascript`.

La salida versionada es:

`config/browser_resolution.yaml`
