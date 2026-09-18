# B7A — Preparación y auditoría del runtime Browser

B6 cerró la fase HTTP/API y dejó un conjunto explícito de fuentes con ruta `B7_BROWSER_CHARACTERIZATION`.

B7A no instala todavía un navegador ni elige una librería por adivinanza. Primero congela la lista de candidatas y audita el runtime real existente.

## Archivos

- `config/browser_candidates.yaml`
- `apps/browser_runtime_audit/`
- `tests/test_browser_runtime_audit_batch7a.py`

## Qué inspecciona

Sin Internet:

- paquetes `playwright`, `selenium` y `pyppeteer`;
- capacidad de iniciar el driver Playwright;
- presencia local de Chromium/Firefox/WebKit de Playwright;
- Chrome/Edge/Firefox instalados;
- referencias browser ya existentes en `src/`, `apps/`, `config/`, `tests/` y `pyproject.toml`.

## Ejecución

```powershell
python -m apps.browser_runtime_audit.main `
  --candidates .\config\browser_candidates.yaml `
  --output-dir .\.runtime\browser_runtime_audit
```

El resultado define B7B. Si Playwright ya forma parte real del proyecto, B7B lo reutilizará. Si no existe runtime browser, la dependencia se incorporará de forma explícita en B7B, sin mezclar esa decisión con la auditoría.
