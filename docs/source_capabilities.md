# B6C — Matriz de capacidades y diagnóstico offline

B6C congela en configuración versionada la evidencia obtenida en B6B/B6B.1 y genera un diagnóstico offline de las fuentes todavía no resueltas.

## `config/source_capabilities.yaml`

No reemplaza `config/sources.yaml`.

Es una matriz de evidencia por fuente lógica. Contiene:

- entrypoint;
- host;
- fuente física reutilizada;
- baseline;
- caracterización;
- workflow hint;
- readiness;
- stage ganador;
- recursos máximos observados;
- evidencia API observada.

Los hints son evidencia, no una configuración final.

## Diagnóstico offline

```powershell
python -m apps.source_diagnostics.main `
  --capabilities .\config\source_capabilities.yaml `
  --characterization-report .\.runtime\source_characterization\latest.json `
  --output-dir .\.runtime\source_diagnostics
```

No realiza red.

Agrupa los 19 casos lógicos no resueltos por entrypoint físico y clasifica, cuando la evidencia existente lo permite:

- acceso restringido;
- DNS/resolución;
- SSL/TLS;
- timeout;
- connection refused;
- redirects;
- HTTP 5xx;
- no recursos después de HTML/API;
- error desconocido.

El siguiente batch usará este diagnóstico para decidir qué se corrige como URL/configuración, qué requiere B7 y qué requiere B8.
