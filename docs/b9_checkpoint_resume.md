# B9A — Checkpoint y reanudación

B9A introduce persistencia de estado de ejecución sin acoplarla todavía al crawler batch.

## Modelo

Cada run conserva 52 fuentes.

Al iniciar desde el plan operacional posterior a B8:

- 41 quedan `PENDING`.
- 11 quedan `SKIPPED_STATUS`, porque su estado ya está explícitamente cerrado para B10.

Estados ejecutables:

- `PENDING`
- `RUNNING`
- `SUCCEEDED`
- `FAILED`

Estado terminal no ejecutable:

- `SKIPPED_STATUS`

Una fuente `FAILED` puede volver a `RUNNING`. Una fuente `SUCCEEDED` no se reabre dentro del mismo run.

## Reanudación

Antes de reanudar:

1. Se valida el fingerprint del plan.
2. Un `RUNNING` dejado por una interrupción se convierte en `FAILED` con `interrupted_previous_run`.
3. Se seleccionan únicamente `PENDING` + `FAILED`.
4. `SUCCEEDED` y `SKIPPED_STATUS` no vuelven a ejecutarse.

## Backends

El backend local JSON es el default de desarrollo y usa escritura atómica `temp + os.replace`.

El backend Mongo está implementado de forma lazy y requiere:

```powershell
python -m pip install -r .\requirements-persistence.txt
```

Variables de producción:

```text
PROSPECTOR_CHECKPOINT_BACKEND=mongo
PROSPECTOR_MONGO_URI=...
PROSPECTOR_MONGO_DATABASE=...
```

B9B conectará este store con el batch crawler y hará un smoke real de resume.
