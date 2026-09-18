# B6E — Targeted HTTP Recovery

B6E ataca únicamente las fuentes físicas que B6D dejó en categorías técnicas todavía no explicadas.

No procesa:

- los 33 candidatos operacionales;
- el caso ya identificado como acceso restringido;
- los 4 casos que ya llegaron correctamente a HTML/API pero no encontraron recursos.

## Qué prueba

Por cada entrypoint objetivo:

- resolución DNS;
- HTTPS original;
- variante con/sin `www`;
- HTTP como diagnóstico de redirección;
- raíz;
- `robots.txt`;
- redirecciones limitadas.

No usa browser.

No desactiva verificación TLS.

No descarga binarios.

Solo conserva una muestra pequeña del cuerpo HTTP.

## Ejecución

```powershell
python -m apps.source_recovery.main `
  --audit-report .\.runtime\source_evidence_audit\latest.json `
  --output-dir .\.runtime\source_recovery
```

La salida permite separar dominios que todavía responden, URLs históricas/incorrectas, errores DNS, TLS, restricciones y fallos del servidor.
