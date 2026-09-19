# B13A — Pipeline reproducible end-to-end

## Objetivo

B12 dejó una entrega empresarial válida a partir de evidencia ya recolectada.
B13 convierte el flujo completo en una operación reproducible:

```text
crawl checkpointed
→ inspección de resultados
→ proyección DATAX
→ especialización TRANSTATS
→ paquete empresarial
→ checksums + ZIP
```

## Reutilización

B13 no reemplaza el runner existente de B9. Usa:

`apps.checkpointed_batch.main`

con:

- `config/source_operational_plan.yaml`
- `config/sources.yaml`
- `config/source_execution_map.yaml`

Así se conservan checkpoint, resume, fuentes lógicas compartidas y status-only.

## Ejecución lógica

El pipeline trabaja sobre las 41 fuentes lógicas operacionales. El runner
checkpointed crea un output independiente por `logical_source_id`, aunque
varias fuentes lógicas apunten al mismo `config_source_id`.

Después del crawl, B13 resuelve el `source_id` físico desde `state/sources.json`
y ejecuta la proyección sobre ese output específico.

## TRANSTATS

TRANSTATS continúa usando `ACQUISITION_JOB_READY`. No se convierte el formulario
HTML en un archivo ficticio.

## Bloqueos externos

MHE y SIGMA solo se aceptan como `EXTERNAL_BLOCKER` cuando siguen fallando con
`ROBOTS_UNREACHABLE`. Si aparece un fallo distinto, B13 lo considera unresolved.

## Parcial y resume

Canary:

```powershell
python -m apps.b13_pipeline.main run --run-id b13-e2e --max-sources 3
```

Reanudación:

```powershell
python -m apps.b13_pipeline.main run --run-id b13-e2e --resume
```

El paquete final solo se construye cuando las 41 fuentes operacionales tienen
un estado terminal downstream válido.

## Salidas

Estado:

`.runtime/b13_pipeline/<run-id>/state.json`

Último estado:

`.runtime/b13_pipeline/latest.json`

Outputs crawl:

`output/checkpointed/<run-id>/<logical_source_id>`

Paquete final:

`output/b13-pipeline/<run-id>/datax-package/latest`

ZIP:

`output/b13-pipeline/<run-id>/datax-package/datax_package.zip`
