# B11A — Auditoría offline de outputs consumibles

## Objetivo

B10 cerró la ejecución live de las 41 fuentes operacionales. B11 cambia el
criterio: ya no pregunta únicamente si el workflow terminó, sino si la
evidencia producida es realmente consumible por DATAX.

Este batch no hace crawl, no usa Internet y no modifica snapshots ni outputs
de B10.

## Evidencia utilizada

Para cada fuente lógica operacional:

1. si la fuente fue remediada en B10C, se prioriza
   `output/b10-remediation/<logical_source_id>`;
2. en caso contrario se usa
   `output/b10-final/b10-final/<logical_source_id>`;
3. se toma el reporte más reciente;
4. se asocia el snapshot del mismo `run_id` cuando existe;
5. se contabilizan mapas `mapa_*.json`;
6. se detecta cualquier JSON legacy cuyo objeto raíz contenga `ESTADISTICAS`.

La fuente lógica se mantiene separada del `source_id` físico que pueda aparecer
dentro del reporte/snapshot. Esto es necesario para las configuraciones
compartidas.

## Clasificaciones

- `DATAX_READY`: ejecución exitosa, recursos raw y proyección legacy detectada.
- `RAW_READY_NO_PROJECTION`: ejecución exitosa y recursos raw, pero falta salida
  legacy consumible en la evidencia auditada.
- `SUCCESS_EMPTY`: ejecución exitosa con cero recursos.
- `EXTERNAL_BLOCKER`: MHE/SIGMA conservan el bloqueo externo aceptado en B10.
- `EXECUTION_NOT_SUCCESS`: fallo no cubierto por la política de cierre externo.
- `EVIDENCE_MISSING`: no hay reporte persistido para la fuente lógica.

## Contrato legacy auditado

Cuando se detecta `ESTADISTICAS`, cada registro candidato se valida contra los
seis campos de compatibilidad DATAX:

- `descripcion`
- `url_descarga`
- `fecha_actualizacion`
- `tipo_archivo`
- `url_origen`
- `metodo_deteccion`

## Salidas

- `.runtime/b11_output_audit/latest.json`
- `.runtime/b11_output_audit/latest.md`

El JSON conserva detalle por fuente, conteos de recursos, tipos, extensiones,
formatos API, artefactos, proyecciones y problemas estructurales.

B11A es deliberadamente descriptivo: un `SUCCESS_EMPTY` o una fuente sin
proyección no hace fallar el auditor. Esos hallazgos determinan los siguientes
batches de implementación.
