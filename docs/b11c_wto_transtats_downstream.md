# B11C — OMC útil + semántica downstream de TRANSTATS

## Hallazgo B11B

La proyección bulk produjo 37 fuentes `DATAX_READY`. Solo OMC y TRANSTATS
quedaron sin selección legacy.

### OMC

La evidencia actual de OMC contenía tres PDFs: un mensaje de la Dirección
General, su variante HTTP y un manual de notificaciones. El filtro de
`ResourceProjectionPolicy` hizo bien en no promoverlos a `ESTADISTICAS`: son
documentos informativos y no datasets estadísticos.

La superficie oficial vigente de datos de la OMC es `data.wto.org`. Su página
de bulk download publica datasets de comercio y recursos descargables, por lo
que el config físico `omc` se focaliza en:

`https://data.wto.org/dataset/bulkdownload`

El workflow sigue siendo `html`. No se introduce credencial ni bypass.

La nueva evidencia se escribirá en `output/b11-refresh/omc`, separada del
baseline autoritativo B10. El auditor B11 prioriza este refresh cuando existe.

### TRANSTATS

La evidencia actual no representa un archivo materializado. Es un formulario
público de adquisición:

`DL_SelectFields.aspx`

El recurso tiene `discovery_method=custom_form_acquisition_job`. La política
preexistente es `metadata_only_no_post`, por lo que convertir ese HTML en un
CSV ficticio sería incorrecto.

B11C conserva esta semántica y genera:

`<evidence>/<source>/downstream/acquisition_job.json`

El auditor lo clasifica como `ACQUISITION_JOB_READY`, separado de
`DATAX_READY`. Una capacidad posterior podrá ejecutar de forma controlada la
adquisición y recién entonces proyectar el dataset materializado.

## Criterio esperado de cierre B11 después del refresh OMC

- 38 `DATAX_READY`
- 1 `ACQUISITION_JOB_READY` (TRANSTATS)
- 0 `RAW_READY_NO_PROJECTION`
- 2 `EXTERNAL_BLOCKER` (MHE, SIGMA)

Este criterio evita forzar documentos irrelevantes o formularios HTML dentro
del contrato legacy.
