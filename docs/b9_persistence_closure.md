# B9C — Cierre de persistencia y reanudación

B9 quedó validado con una corrida live checkpointed y una reanudación live.

## Evidencia live

Primera ejecución del run `b9b-smoke`:

- `ANAPO`
- exit code del crawler: 0
- checkpoint final: `SUCCEEDED`

Segunda ejecución con `--resume` sobre el mismo `run_id`:

- ejecutó `APS`;
- no repitió ANAPO;
- APS terminó correctamente;
- checkpoint acumulado: 2 `SUCCEEDED`, 39 `PENDING`, 11 `SKIPPED_STATUS`.

Esto demuestra el comportamiento requerido: una fuente exitosa no se vuelve a ejecutar al reanudar.

## Mongo

El adapter Mongo ya existía desde B9A. B9C añade pruebas de contrato sin requerir un servidor externo:

- `run_id` se usa como `_id`;
- `save` hace `replace_one(..., upsert=True)`;
- `load(run_id)` recupera el run correcto;
- `load()` recupera el checkpoint más reciente;
- `_id` no contamina el modelo de dominio;
- el cliente se cierra.

Un smoke contra un servidor Mongo real queda como validación de despliegue porque requiere un URI/database concretos. La lógica del adapter sí forma parte de B9 y queda testeada.

## Estado final B9

- checkpoint/resume: funcional;
- ejecución checkpointed: funcional;
- logical→physical map: 41/41;
- JSON store: live probado;
- Mongo store: implementado y contract-tested;
- B9: cerrado.
