# B6D — Auditoría offline de evidencia

B6D usa exclusivamente la evidencia ya producida por B6B/B6B.1.

No hace red.

Tiene dos objetivos:

1. Auditar las 33 fuentes `READY_CANDIDATE` antes de promoverlas a configuración operacional.
2. Extraer más evidencia forense de los entrypoints todavía no resueltos.

## Auditoría de candidatos

Se deduplican recursos por URL y se revisa:

- extensiones;
- recursos API;
- APIs CMS/WordPress;
- APIs de documentación;
- posibles APIs de datos;
- evidencia de archivos estructurados;
- evidencia documental.

Esto evita promover automáticamente a `workflow: api` un portal cuyo único API observado sea el API genérico del CMS.

## Forense offline

Se inspeccionan logs, run reports, snapshots y demás JSON/textos existentes.

Se intenta distinguir:

- DNS;
- SSL/TLS;
- connection refused;
- timeouts;
- redirects;
- HTTP 5xx;
- fallo de robots;
- ausencia total de traza HTTP;
- fuente accesible sin recursos tras HTML/API.

## Ejecución

```powershell
python -m apps.source_evidence_audit.main `
  --characterization-report .\.runtime\source_characterization\latest.json `
  --capabilities .\config\source_capabilities.yaml `
  --output-dir .\.runtime\source_evidence_audit
```

Los resultados sirven de entrada para el siguiente batch de promoción/recovery.
