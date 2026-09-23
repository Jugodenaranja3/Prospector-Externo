# Prospector Externo — Diseño de auditoría independiente por sitio (A2)

## Objetivo

Validar empíricamente, fuente por fuente, la efectividad del Prospector Externo sin reutilizar la lógica de discovery/proyección que se está auditando.

La auditoría distingue tres objetos diferentes:

1. **superficie web pública observable** mediante un probe independiente;
2. **evidencia raw B13** (`ResourceCandidate` del snapshot);
3. **salida legacy DATAX** entregada en el paquete B13.

Esto evita confundir un recurso no descubierto con un recurso descubierto pero descartado por la política de proyección.

## Universo real

- 52 fuentes lógicas DATAX.
- 46 superficies/hosts efectivos únicos.
- 41 fuentes lógicas operacionales en B13.
- 35 configuraciones físicas operacionales.
- 11 fuentes status-only/no operacionales.

Las 6 ejecuciones lógicas redundantes se explican por cinco grupos compartidos:

- `aps` + `aps_soat` -> `aps`;
- `asfi_valores` + `asfi` + `asfi_finrural` -> `asfi_valores`;
- `bcb` + `asfi_bcb` -> `bcb`;
- `icco` + `fdta_valles` -> `icco`;
- `mdryt_oap` + `mdryt` -> `mdryt_oap`.

La auditoría final debe producir 52 conclusiones lógicas, aunque solo requiera 46 probes web efectivos.

## Baseline B13

- 38 `DATAX_READY`.
- 1 `ACQUISITION_JOB_READY` (`transtats`).
- 2 `EXTERNAL_BLOCKER` (`mhe`, `sigma`).
- 11 status-only.
- 12.104 registros legacy lógicos.

## Hallazgo P1 de reproducibilidad

`apps/b13_pipeline/main.py` obtenía el roster operacional desde `.runtime/b11_output_audit/latest.json`, artefacto eliminado en B14. El roster puede derivarse íntegramente de `config/source_operational_plan.yaml`, que es durable y versionado.

La rama de auditoría corrige únicamente esta dependencia; no modifica el resultado histórico `b13-e2e`.

## Hallazgo de drift descriptivo de workflow

El `source_execution_map.yaml` conserva estrategia planificada/histórica para algunas fuentes mientras que el `sources.yaml` usado en B13 refleja remediaciones posteriores. La evidencia real de B13 confirma los workflows de runtime:

- `statistics_denmark`: plan `javascript`, runtime/config `api`;
- `data_gov`: plan `api`, runtime/config `html`;
- `bbv`: plan `html`, runtime/config `commented_html` (especialización compatible).

Para evaluar lo que realmente ocurrió, la auditoría debe priorizar la evidencia de ejecución B13 y la configuración efectiva, no inferir el runtime únicamente desde `workflow_strategy` histórico.

## Riesgo de cobertura prioritaria

Se deben revisar primero las superficies que terminaron por límites o tocaron topes de recursos:

- `REQUEST_BUDGET_REACHED`: 12 configuraciones físicas únicas;
- `MAX_CONSECUTIVE_ERRORS`: 6 configuraciones físicas únicas;
- `MAX_URLS`: 1;
- `BROWSER_PAGE_LIMIT`: 1;
- raw = 1000 (tope por defecto de recursos): `ada`, `asfi_valores`, `att`, `bm`, `senamhi`.

También merecen auditoría de proyección las fuentes con retención raw->legacy especialmente baja, por ejemplo `aps`, `snis`, `min_educacion`, `bm` y `vipfe`. `transtats` es un caso especial deliberado y no debe evaluarse con la misma métrica.

## Principio de independencia

`src/site_validation` no debe importar:

- `DiscoveryFrontier`;
- `UrlNormalizer`;
- workflows del Prospector;
- `ResourceProjectionPolicy`;
- `DataxProjectionService`.

El probe utiliza un crawler acotado independiente, GET-only, basado en HTTP/HTML/sitemap y respeto de `robots.txt`.

## Comparación en dos niveles

### Discovery

- `reference ∩ raw`: observado por ambos;
- `reference_only_raw`: candidato a miss del Prospector;
- `raw_only_reference`: candidato encontrado por Prospector pero no por el probe independiente.

### Downstream

- `reference ∩ legacy`;
- `reference_only_legacy`;
- `legacy_only_reference`;
- `raw_not_in_legacy`.

Ninguna diferencia automática se etiqueta todavía como falso positivo, falso negativo o recall final.

## Gold set / adjudicación

El reference probe no es un ground truth exhaustivo. A5 debe adjudicar las diferencias relevantes y producir un conjunto confirmado antes de calcular recall/precision finales.

## Salida mínima por fuente

`output/site-validation/<run-id>/<source_id>/`

- `reference_candidates.json`;
- `comparison.json`;
- `summary.json`;
- `REPORT.md`.

## Piloto

BCB es el piloto A4 porque tiene:

- 580 recursos raw B13;
- 527 registros legacy;
- 53 recursos raw no seleccionados por la proyección;
- grouping contract;
- históricos y series periódicas;
- `QUEUE_EXHAUSTED`, por lo que permite validar el comparador sin comenzar por una fuente terminada por budget.

El piloto no debe declarar recall final hasta A5.
