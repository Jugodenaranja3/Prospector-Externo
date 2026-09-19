# B10A — Auditoría estructural final de 52 fuentes

B10A distingue ahora entre:

- `planned_workflow_strategy`: familia lógica resuelta durante B6–B8;
- `execution_workflow`: implementación concreta usada por `config/sources.yaml`.

Esta distinción es necesaria para configuraciones especializadas que siguen perteneciendo a la misma familia lógica.

## BBV

BBV estaba clasificada lógicamente como `html`, pero su configuración histórica preservada usa `commented_html`.

Eso no representa una contradicción: `commented_html` es una especialización del flujo HTML para contenido publicado dentro de comentarios HTML.

La compatibilidad queda explícita y versionada en:

`config/workflow_compatibility.yaml`

Actualmente solo se permite:

- `html → html`
- `html → commented_html`
- `javascript → javascript`
- `api → api`
- `custom → custom`

Cualquier otra diferencia lógica/física falla el audit.

## Invariantes finales

- 52 fuentes lógicas;
- 41 operacionales;
- 11 status-only;
- 35 configuraciones físicas;
- 41 mappings lógico → físico;
- 0 incompatibilidades de workflow;
- B8 cerrado;
- B9 cerrado;
- evidencia estática de dispatch para cada workflow físico.

La matriz definitiva queda en `config/final_source_matrix.yaml`.
