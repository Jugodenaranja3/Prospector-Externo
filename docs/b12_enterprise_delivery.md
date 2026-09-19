# B12B — Validación empresarial del paquete DATAX

## Entry point de consumo

El archivo de entrada del paquete es:

`manifest.json`

DATAX no debe adivinar el archivo a partir del `physical_source_id`.

El procedimiento de consumo es:

1. abrir `manifest.json`;
2. localizar `logical_source_id`;
3. leer el campo `classification`;
4. cuando sea `DATAX_READY`, abrir la ruta `artifact`;
5. ese archivo conserva `ESTADISTICAS` como raíz;
6. cuando sea `ACQUISITION_JOB_READY`, consumir el manifest especializado;
7. cuando sea `EXTERNAL_BLOCKER`, no existe artefacto de datos.

Por tanto, el archivo concreto de datos para una fuente legacy es:

`sources/<logical_source_id>.json`

pero el **entry point empresarial del paquete completo** es `manifest.json`.

## Por qué no existe un JSON gigante único

Hay fuentes lógicas que pueden compartir una configuración física. Un merge
global de sus árboles `ESTADISTICAS` podría introducir colisiones y perder la
provenance lógica.

B12B conserva la separación por fuente y audita hashes para descubrir payloads
idénticos. Los duplicados exactos se documentan en
`consumer_contract.json`; no se eliminan automáticamente.

## Hardening de integridad

B12B valida:

- las 41 fuentes del manifest;
- `sources.csv` contra `manifest.json`;
- los 38 JSON legacy;
- TRANSTATS como acquisition job;
- MHE y SIGMA como bloqueos externos;
- cobertura completa de SHA-256;
- contenido exacto del ZIP contra el directorio;
- ausencia de paths inseguros en el ZIP;
- grupos de payloads JSON exactamente idénticos.

## Salidas

Dentro del paquete:

- `manifest.json`
- `consumer_contract.json`
- `README.md`
- `sources.csv`
- `external_blockers.json`
- `checksums.sha256`
- `sources/*.json`
- `special/transtats/acquisition_job.json`

Auditoría runtime:

`.runtime/b12_enterprise_audit/latest.json`

Entrega comprimida:

`output/datax-package/datax_package_latest.zip`
