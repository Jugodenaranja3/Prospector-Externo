# B8B — Custom strategy live probes

B8B ejecuta probes por **estrategia**, no por adaptador específico de fuente.

Estrategias soportadas:

- `API_SEMANTIC_PROBE`
- `NETWORK_ENDPOINT_PROBE`
- `SAFE_BROWSER_INTERACTION`
- `DATA_LINK_TRAVERSAL`
- `CUSTOM_SITE_REVIEW`
- `IDENTITY_RESEARCH`

## Seguridad operacional

Los probes HTTP custom usan GET.

En browser:

- JavaScript está habilitado;
- image/media/font se bloquean;
- POST/PUT/PATCH/DELETE se bloquean;
- formularios GET pueden evaluarse usando valores ya presentes en la interfaz;
- formularios no-GET se registran pero no se envían;
- no hay login;
- no hay CAPTCHA/WAF bypass;
- TLS no se desactiva.

## Identidad

`config/custom_identity_resolution.yaml` conserva dos decisiones verificadas:

- IBCH: mismo instituto con dominio actual `ibch.com`, candidato a probe.
- BOLCEREALES: no existe sustitución segura; `bolsadecereales.com` no debe usarse como reemplazo automático del dominio histórico boliviano.

## Ejecución

```powershell
python -m apps.custom_probe.main `
  --strategies .\config\custom_strategy_candidates.yaml `
  --identity-resolution .\config\custom_identity_resolution.yaml `
  --output-dir .\.runtime\custom_probe
```

B8C utilizará los resultados para cerrar los custom workflows y promover únicamente evidencia suficiente.
