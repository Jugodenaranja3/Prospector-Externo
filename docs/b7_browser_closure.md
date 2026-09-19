# B7D — Cierre de Browser y Plan Operacional

B7D integra las decisiones de:

- `config/source_resolution.yaml` — cierre B6 HTTP/API;
- `config/browser_resolution.yaml` — cierre semántico B7.

Genera:

- `config/source_operational_plan.yaml`
- `config/custom_candidates.yaml`

## Regla de promoción JavaScript

Solo una fuente B7 con:

`route: PROMOTE_JAVASCRIPT_WORKFLOW`

se convierte en:

`workflow_strategy: javascript`

La fuente con `JAVASCRIPT_NETWORK_REVIEW` no se promociona por incertidumbre; pasa a B8.

## B8

`config/custom_candidates.yaml` contiene únicamente las fuentes que requieren lógica especial, identidad histórica o semántica que no pudo resolverse con los workflows genéricos HTTP/API/JavaScript.

## No reemplaza todavía `config/sources.yaml`

El plan operacional es la fuente intermedia de decisión. B8 cerrará los casos especiales y después se construirá la configuración permanente completa.
