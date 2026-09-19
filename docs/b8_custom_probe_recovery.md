# B8B.2 — Recuperación del cierre de custom probes

La corrida live B8B alcanzó las ocho fuentes y escribió un `result.json` por fuente, pero falló al finalizar Playwright por un error de lifecycle:

```text
AttributeError: 'PlaywrightContextManager' object has no attribute 'stop'
```

La corrección usa `pw.stop()` sobre la instancia retornada por `sync_playwright().start()`.

No es necesario repetir los ocho probes para recuperar el resumen. El agregador:

```powershell
python -m apps.custom_probe.aggregate `
  --strategies .\config\custom_strategy_candidates.yaml `
  --output-dir .\.runtime\custom_probe
```

lee exclusivamente los `result.json` ya persistidos y reconstruye:

- `latest.json`
- `latest.csv`
- `latest.md`

sin acceso a Internet.
