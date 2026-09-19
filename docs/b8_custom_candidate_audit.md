# B8A — Auditoría de candidatos Custom

B8 no empieza creando ocho adaptadores específicos.

Primero clasifica las candidatas que quedaron abiertas después de B6+B7 y reutiliza toda la evidencia existente.

La entrada es:

- `config/custom_candidates.yaml`
- `config/browser_resolution.yaml`
- `.runtime/browser_characterization/`
- `.runtime/source_evidence_audit/latest.json`

La salida versionada es:

- `config/custom_strategy_candidates.yaml`

## Clases

- `IDENTITY_RESEARCH_REQUIRED`
- `API_SEMANTIC_REVIEW`
- `BROWSER_NETWORK_SEMANTIC_REVIEW`
- `BROWSER_INTERACTION_REVIEW`
- `CUSTOM_GENERAL_REVIEW`

## Estrategias

- `IDENTITY_RESEARCH`
- `API_SEMANTIC_PROBE`
- `NETWORK_ENDPOINT_PROBE`
- `SAFE_BROWSER_INTERACTION`
- `DATA_LINK_TRAVERSAL`
- `CUSTOM_SITE_REVIEW`

La auditoría es offline. B8B usará estas estrategias para ejecutar únicamente los probes live realmente necesarios.
