# B9B — Ejecución batch con checkpoint/resume

B9B integra la máquina de estados de B9A con el ejecutor existente:

```text
python -m apps.crawler_batch.main --config ... --output-dir ...
```

La integración es deliberadamente externa al crawler: cada fuente operacional se materializa como una configuración temporal de una sola fuente y se ejecuta mediante el CLI existente.

## Flujo por fuente

```text
PENDING / FAILED
      ↓
   RUNNING
      ↓
crawler_batch(single source)
   ↙           ↘
exit 0       exit != 0
  ↓              ↓
SUCCEEDED       FAILED
```

El checkpoint se guarda:

1. inmediatamente después de marcar `RUNNING`;
2. inmediatamente después de `SUCCEEDED` o `FAILED`.

Una interrupción entre esos puntos será recuperada como `FAILED: interrupted_previous_run` por el mecanismo B9A.

## Validación offline

Antes de cualquier crawl real:

```powershell
python -m apps.checkpointed_batch.main `
  --plan .\config\source_operational_plan.yaml `
  --sources-config .\config\sources.yaml `
  validate
```

Las 41 fuentes operacionales deben mapear contra la configuración física existente.

## Smoke real limitado

Después de validar el mapeo:

```powershell
python -m apps.checkpointed_batch.main `
  --plan .\config\source_operational_plan.yaml `
  --sources-config .\config\sources.yaml `
  --backend json `
  run `
  --run-id b9b-smoke `
  --max-sources 1
```

Y para continuar el mismo run:

```powershell
python -m apps.checkpointed_batch.main `
  --plan .\config\source_operational_plan.yaml `
  --sources-config .\config\sources.yaml `
  --backend json `
  run `
  --run-id b9b-smoke `
  --resume `
  --max-sources 1
```

La segunda ejecución no repite una fuente `SUCCEEDED`.

B9B todavía no altera `config/sources.yaml`; solo añade scheduling/restart seguro. La promoción final de decisiones operacionales al config canónico debe ocurrir antes del audit B10.
