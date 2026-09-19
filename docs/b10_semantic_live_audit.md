# B10B.1 — Auditoría semántica del crawl live

El run `b10-final` terminó con 41/41 fuentes en estado `SUCCEEDED`, pero el estado del proceso hijo no es evidencia suficiente por sí sola.

Durante SNIS se observó un `ParserRejectedMarkup` dentro del workflow HTML. El dispatcher registró el fallo y el proceso `crawler_batch` terminó con código 0, por lo que el runner externo lo marcó como `SUCCEEDED`.

Eso demuestra una condición de falso positivo:

```text
workflow interno falla
→ crawler_batch conserva exit code 0
→ checkpoint externo marca SUCCEEDED
```

B10B.1 audita el log completo del run ya ejecutado y cruza:

- estado del checkpoint;
- workflow esperado en `final_source_matrix.yaml`;
- workflow observado en consola;
- señales de error internas;
- conteo de recursos;
- presencia del segmento de log por fuente.

No hace red y no modifica el checkpoint.

## Clasificaciones bloqueantes

- `CHECKPOINT_NOT_SUCCEEDED`
- `PROCESS_OK_WITH_INTERNAL_ERROR`
- `WORKFLOW_MISMATCH`
- `WORKFLOW_NOT_OBSERVED`
- `ZERO_RESOURCES_REVIEW`
- `RESOURCE_COUNT_NOT_OBSERVED`
- `MISSING_LOG_SEGMENT`

El objetivo es determinar qué debe corregirse antes del cierre B10C, sin repetir las 41 fuentes a ciegas.
