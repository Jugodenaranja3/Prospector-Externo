# B10B.3 — Reconciliación de evidencia semántica

B10B.2.1 recuperó correctamente los 41 segmentos del log, pero trataba ausencia de una línea de observabilidad como si fuera evidencia de fallo.

Eso producía falsos bloqueos para workflows que no registran `Iniciando XWorkflow` y para fuentes cuyo log no imprime un conteo explícito de recursos.

B10B.3 aplica una regla más estricta epistemológicamente:

> ausencia de evidencia en consola no equivale a evidencia de ejecución incorrecta.

## Bloqueos reales

Siguen bloqueando:

- checkpoint no exitoso;
- error interno observado;
- workflow distinto observado;
- cero recursos observado;
- segmento completamente ausente.

## Warnings

No bloquean por sí solos:

- workflow no observado en consola;
- conteo de recursos no observado en consola.

Esto no elimina problemas reales. FIFA/TranStats siguen bloqueando porque se observó `html` cuando se esperaba `custom`. SNIS sigue bloqueando por error interno. Los casos de cero recursos siguen requiriendo revisión.
