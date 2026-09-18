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


## Hallazgo del primer smoke real (2026-09-18)

La página `?q=reporte-estadistico` expuso 39 recursos en el catálogo bruto con solo
2 requests HTTP (robots + HTML). De ellos, 27 coincidieron explícitamente con la
serie objetivo `OPERACIONES DEL SISTEMA DE PAGOS NACIONAL`: 9 periodos, cada uno
con PDF/XLSX/ODS. Los 12 restantes provinieron de navegación/globales del sitio
(manuales, normativa, publicaciones, reservas, formularios, etc.).

Por ello el contrato BCB usa `unmatched_policy: exclude`: los 39 recursos se
conservan como evidencia raw, pero solo los que coinciden con una regla BCB
caracterizada pueden entrar a la proyección DATAX. Esto evita que un XLSX global
como Reservas Internacionales sea seleccionado solo por ser un formato
estructurado cuando el crawl se originó en otra familia estadística.


## Hallazgo histórico completo y hardening BATCH 5C (2026-09-18)

La caracterización controlada de las 17 páginas de `?q=reporte-estadistico`
(`page=0..16`) produjo 404 recursos raw con 18 requests HTTP (robots + 17 HTML),
sin errores, timeouts ni bloqueos de robots. El rango mensual del reporte es
continuo desde 2013-01 hasta 2026-07: 163 periodos sin huecos.

La disponibilidad histórica cambia por era y debe conservarse como evidencia,
no rellenarse artificialmente:

- 2013-01 a 2015-02: solo PDF (26 periodos).
- 2015-03 a 2018-12: PDF + XLSX (46 periodos).
- 2019-01 a 2026-07: PDF + XLSX + ODS (91 periodos).

Eso equivale a 391 representaciones estadísticas reales: 163 PDF, 137 XLSX y
91 ODS. El crawl raw contiene además 13 recursos fuera de este slice.

Durante la auditoría apareció un falso positivo: `operactiponumero.pdf`, un
recurso general de Sistema de Pagos sin periodo mensual propio, heredaba el
contexto visual de Enero 2013 y entraba erróneamente en la familia mensual. La
regla `bcb_sistema_pagos_reporte_estadistico` se endureció para exigir también
una ruta `webdocs/sistema_pagos` con evidencia nominal de reporte/estadística o
mes. El raw permanece intacto; el falso positivo solo queda fuera de downstream.

Resultado downstream esperado del histórico completo: `raw=404`,
`selected=391`, `families=1`, `periods=163`, `legacy=391`.

## Smoke Boletín Mensual actual y hardening BATCH 5D (2026-09-18)

El smoke controlado de `?q=pub_boletin-mensual` usó únicamente robots + una
página HTML y produjo 70 recursos raw. De ellos, 61 son cuadros XLSX del Boletín
Mensual N° 373 (Enero 2026) y 9 pertenecen a navegación/publicaciones globales.
La numeración visible termina en 60, pero existen dos cuadros independientes
`3A` y `3B`; por ello el número real de series/cuadros XLSX es 61, no 60.

El contrato `bcb_boletin_mensual_cuadro` ya separa correctamente cada código y
título en su propia familia (`3A` y `3B` no colapsan). Sin embargo, el cuadro 43,
`Índice de Precios al Consumidor - IPC (Base 2016 = 100)`, reveló un defecto
genérico de extracción temporal: al concatenar título, contexto y URL antes de
extraer el periodo, `enero` del contexto podía combinarse con `2016` del título,
produciendo el periodo híbrido incorrecto `2016-01` en vez de `2026-01`.

BATCH 5D resuelve cada campo temporal por separado y escoge la evidencia más
precisa: día > mes/trimestre > año; en empate se conserva el orden título >
contexto > URL. Así un periodo explícito en el título sigue teniendo prioridad,
pero un simple año-base del título no puede contaminar un mes/año explícito del
contexto. El cambio es genérico y no introduce lógica BCB dentro del modelo de
dominio ni del grouping contract.

Después de re-crawlear este smoke, el resultado esperado es: 70 raw, 61 cuadros
caracterizados, 61 familias y los 61 con periodo `2026-01`. Los 9 recursos
globales permanecen raw y fuera de downstream por `unmatched_policy: exclude`.

## Muestreo histórico del Boletín Mensual y hardening BATCH 5E (2026-09-18)

Se ejecutó un muestreo real, acotado y sin crawl lateral sobre siete páginas del
Boletín Mensual (`page=0,36,72,108,144,180,216`). La corrida utilizó robots +
siete HTML, terminó sin errores y produjo 76 recursos raw. La página actual
aporta 61 cuadros XLSX del Boletín Mensual N° 373 (enero 2026); las seis páginas
históricas muestreadas aportan una publicación PDF completa cada una.

El muestreo confirmó dos modelos de publicación que no deben colapsarse:

- modelo moderno: múltiples cuadros XLSX, cada código/título constituye una
  familia estadística independiente para un periodo común;
- modelo histórico: un PDF de publicación completa representa el boletín del
  mes y debe conservarse como la familia
  `boletin-mensual-publicacion-completa`, sin inventar 61 cuadros internos que
  no fueron publicados como recursos separados.

También se observó que en páginas antiguas el enlace puede llamarse solamente
`Ver archivo Pdf` y la URL puede no contener el año o el mes (`mensualenero14.pdf`,
`mensualeneroxxx.pdf`). BATCH 5E amplía la recuperación de contexto cercano para
reconocer etiquetas fuertes del tipo `Boletín Mensual <número> - <mes> <año>`.
Así el periodo se obtiene del encabezado humano del registro y no de la fecha de
publicación ni de una heurística de URL.

Con el recrawl del mismo muestreo, los seis PDF históricos esperados deben quedar
con periodos `2023-01`, `2020-01`, `2017-01`, `2014-01`, `2011-01` y `2008-01`.
El contrato BCB proyecta esos PDF como una sola familia histórica con seis
periodos, mientras mantiene las 61 familias XLSX modernas separadas. Los recursos
globales/nav continúan en raw y fuera de downstream.

