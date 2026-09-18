# B7B — Playwright Browser Characterization

Playwright es la capacidad browser elegida para B7.

El Chromium empaquetado por Playwright es **opcional**. Si su descarga falla, el runner puede reutilizar Chrome o Microsoft Edge ya instalados en Windows mediante Playwright.

Orden de selección del runtime:

1. Chromium de Playwright, si ya existe localmente.
2. Canal `chrome`.
3. Canal `msedge`.
4. Rutas locales conocidas de Chrome/Edge.

No se desactiva TLS y no se implementa bypass de captcha/WAF.

## Instalación Python

```powershell
python -m pip install -r .\requirements-browser.txt
```

No es necesario ejecutar `python -m playwright install chromium` si Chrome/Edge del sistema funciona.

## Runtime check

```powershell
python -m apps.browser_characterization.main --runtime-check
```

## Caracterización real

```powershell
python -m apps.browser_characterization.main `
  --candidates .\config\browser_candidates.yaml `
  --output-dir .\.runtime\browser_characterization
```

Por fuente se guardan HTML renderizado, enlaces visibles, XHR/fetch y señales de datos bajo `.runtime/browser_characterization/sources/<source_id>/`.
