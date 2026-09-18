# BCB — caracterización BATCH 5A

Fecha de caracterización: 2026-09-17.

## Objetivo

Preparar el vertical slice de Banco Central de Bolivia sin migrar un adapter histórico gigante y sin ejecutar todavía un crawl agresivo. La fuente se modela con `workflow: html`, discovery genérico y un contrato declarativo de agrupamiento downstream.

## Superficies estadísticas verificadas

- Información Estadística Semanal: `https://www.bcb.gob.bo/?q=estad-sticas-semanales`.
- Boletín Mensual: `https://www.bcb.gob.bo/?q=pub_boletin-mensual`.
- Boletín Estadístico: `https://www.bcb.gob.bo/?q=pub_boletin-estadistico`.
- Boletín del Sector Externo: `https://www.bcb.gob.bo/?q=pub_boletin-sector-externo`.
- Reporte Estadístico de Operaciones del Sistema de Pagos: `https://www.bcb.gob.bo/?q=reporte-estadistico`.

## Patrones observados

### Reporte Estadístico de Sistema de Pagos

Un mismo periodo publica varias representaciones equivalentes, por ejemplo PDF/XLSX/ODS. La familia lógica debe ser única y las representaciones deben mantenerse separadas dentro del mismo periodo. Sufijos de nombre como `(1)` no crean una familia nueva.

### Boletín Mensual

Los cuadros XLSX usan rutas de publicación como `webdocs/publicacionesbcb/YYYY/MM/DD/01.xlsx`. La fecha de la carpeta es fecha de publicación, no necesariamente el periodo estadístico. El periodo debe salir del contexto de sección del HTML (por ejemplo `Índice · Estadísticas (Diciembre 2025)`) y la serie debe identificarse por código/título del cuadro (`1. Base Monetaria`, `25. Exportaciones`, etc.).

### Información Estadística Semanal

La evidencia humana publica fechas como `AL 11 DE SEPTIEMBRE DE 2026`. Para no colapsar semanas distintas, el periodo normalizado admite granularidad diaria `YYYY-MM-DD`.

## Clasificación de responsabilidades

| Responsabilidad histórica | Clasificación nueva |
|---|---|
| recorrer HTML/paginación | generalizable — `HtmlWorkflow` + frontier |
| respetar robots/rate limit | generalizable — runtime HTTP |
| detectar XLSX/ODS/PDF | generalizable — `ResourceDetector` |
| extraer periodo desde contexto HTML | generalizable — contexto de sección + normalizador |
| agrupar cuadros/series BCB | projection — `config/grouping/bcb.yaml` |
| preferir XLSX sobre ODS/PDF | generalizable — `RepresentationPreference` |
| reproducir rutas JSON históricas | projection/export — `LegacyExportPlan` posterior |
| adapter monolítico BCB | obsoleto — no migrar |

## Política de primera corrida

La configuración principal queda conservadora (`depth=1`, 120 requests/URLs, 1.5 s por host). Antes de utilizar esos máximos se hará un smoke más pequeño (aprox. 20–30 requests) con config temporal, igual que se hizo con FINRURAL.
