# B10C — Core runtime remediation and targeted rerun

B10C parte de los reportes hijos autoritativos del crawl `b10-final`, no del exit code histórico.

Baseline observado antes de este batch:

- 41 reportes lógicos disponibles.
- 27 ejecuciones `SUCCESS`.
- 14 ejecuciones `FAILED`.
- 9 ejecuciones `SUCCESS` con `EMPTY_RESULT`.
- 23 fuentes requieren rerun selectivo.

## Correcciones de runtime

1. `crawler_batch` propaga `sources_failed` al código de salida.
2. Se elimina el gate histórico `WORKFLOW_DEFERRED` para JavaScript.
3. `JavascriptWorkflow` usa el runtime HTTP como preflight y luego Playwright.
4. `DiscoveryFrontier` separa presupuesto de navegación y presupuesto de recursos.
5. `HtmlWorkflow` ya no abandona el crawl apenas `max_urls` fue alcanzado por sitemap.
6. Respuestas binarias/no-markup se controlan y pueden catalogarse como recurso directo.
7. `ParserRejectedMarkup` deja de escalar como excepción no controlada.
8. `CustomWorkflow` deja de ser un alias silencioso de HTML:
   - `data_endpoint` delega en `ApiWorkflow`.
   - `download_form_acquisition_job` valida por GET y modela el formulario como recurso sin POST.
9. Los `operational_config` custom de B8 se promueven a `config/sources.yaml` bajo `custom_config`.

## Rerun selectivo

`apps.b10_targeted_remediation.main` toma como baseline los reportes de
`output/b10-final/b10-final` y selecciona únicamente fuentes FAILED o EMPTY.

El estado se guarda de forma atómica en:

`.runtime/b10_remediation/state.json`

Los resultados nuevos quedan en:

`output/b10-remediation/<logical_source_id>`

El runner soporta `--source-ids`, `--resume`, `--max-sources` y `--rerun-empty`.

El aplicador B10C no hace red. El crawl live se ejecuta después y de forma selectiva.
