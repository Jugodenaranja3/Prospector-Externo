# PROSPECTOR EXTERNO DATAX — INFORME FINAL DE EFECTIVIDAD

**Baseline auditado:** `b13-e2e`
**Matriz consolidada:** `audit-final-001`
**Fuentes logicas auditadas:** **52 / 52**
**Superficies fisicas unicas representadas:** **46**

## 1. Objetivo

La auditoria verifico de forma independiente si el Prospector Externo descubre y representa de manera adecuada los recursos publicos de las fuentes DATAX, diferenciando discovery, proyeccion legacy, fuentes API/JavaScript/custom, fuentes status-only y bloqueos externos.

La auditoria no intenta demostrar un crawling ilimitado o exhaustivo de Internet. Evalua el comportamiento del Prospector respecto de las superficies publicas verificables y de las paginas/origen efectivamente observadas en la corrida B13.

## 2. Alcance auditado

- Fuentes logicas: **52 / 52**.
- Superficies fisicas unicas: **46**.
- Estado previo DATAX_READY: **38**.
- Estado previo ACQUISITION_JOB_READY: **1**.
- Estado previo EXTERNAL_BLOCKER: **2**.
- Estado previo STATUS_ONLY: **11**.

## 3. Metodologia

La validacion utilizo un observador independiente del discovery productivo para evitar que el crawler y el auditor compartieran el mismo error. Se aplicaron metodos diferentes segun el tipo de fuente:

- **A6_SITEWIDE:** exploracion independiente amplia del portal.
- **A7_ORIGIN_RECHECK:** reapertura independiente de las paginas de origen realmente usadas por B13 y nueva extraccion de recursos.
- **A7_SPECIALIZED:** validacion especifica de APIs, JavaScript y flujos custom.
- **A8_STATUS_ONLY:** reevaluacion de fuentes excluidas del conjunto operacional.
- **A9_SPECIAL_CASE:** comprobaciones dedicadas para MHE, SIGMA y TRANSTATS.

Distribucion de metodos:

- `A6_SITEWIDE`: **6** fuentes logicas.
- `A7_ORIGIN_RECHECK`: **26** fuentes logicas.
- `A7_SPECIALIZED`: **6** fuentes logicas.
- `A8_STATUS_ONLY`: **11** fuentes logicas.
- `A9_SPECIAL_CASE`: **3** fuentes logicas.

## 4. Resultado cuantitativo comparable

En las fuentes fisicas donde fue metodologicamente valido realizar un origin-recheck comparable se observaron **7009** recursos del baseline sobre **9331** recursos rechecables, con un overlap agregado de **75.12%**.

> **Importante:** este porcentaje no es una calificacion global del Prospector. No incluye de la misma forma APIs, JavaScript, fuentes custom, status-only ni bloqueos externos. Ademas, las diferencias pueden reflejar cambios temporales del portal entre B13 y la auditoria.

### Caso piloto BCB

- Clasificacion: `ORIGIN_RECHECK_COMPLETED`.
- Cobertura comparable del origin-recheck: **99.31%**.
- Evidencia: origins_rechecked=22; origins_failed=0; raw_recheckable=580; matched=576.

El piloto BCB demostro que el crawler y el auditor debian tratar correctamente URLs relativas y separar recursos raw de recursos proyectados legacy. Esa calibracion se incorporo antes del barrido masivo.

## 5. Clasificaciones finales

- `ACCESS_REVIEW`: **4**.
- `ACCESS_REVIEW_WITH_EXTERNAL_EVIDENCE`: **1**.
- `ACQUISITION_JOB_VALIDATED`: **1**.
- `EXTERNAL_ROBOTS_5XX_CONFIRMED`: **1**.
- `EXTERNAL_TLS_BLOCKER_CONFIRMED`: **1**.
- `GENERIC_STRONG`: **6**.
- `HISTORICAL_STATUS_SUPPORTED`: **1**.
- `ORIGIN_RECHECK_COMPLETED`: **26**.
- `REVIEW_FOR_PROMOTION`: **1**.
- `STATUS_ONLY_SUPPORTED`: **3**.
- `STATUS_ONLY_SUPPORTED_AFTER_ADJUDICATION`: **1**.
- `VALIDATED`: **2**.
- `VALIDATED_WITH_TEMPORAL_DIFFERENCES`: **4**.

### 5.1 Fuentes operacionales

Las fuentes operacionales quedaron contabilizadas mediante site-wide discovery, origin-recheck o validacion especializada. Las diferencias temporales se conservaron como evidencia y no se reinterpretaron automaticamente como fallos del crawler.

### 5.2 STATUS_ONLY

Fuentes cuya exclusion se mantiene soportada por la auditoria/adjudicacion: **atc, fam, fegasacruz, ibch, otros_historicos**.

Fuentes que permanecen en `ACCESS_REVIEW`: **bolcereales, ceprobol, fundempresa, sabsa**.

Fuentes con evidencia externa adicional de acceso: **mefp**.

Fuentes recomendadas para revisar promocion operacional: **fmi**.

La recomendacion de promocion no modifica automaticamente el inventario productivo; identifica una decision de catalogo que debe revisarse por separado.

### 5.3 Casos especiales

- **MHE:** `EXTERNAL_TLS_BLOCKER_CONFIRMED`.
- **SIGMA:** `EXTERNAL_ROBOTS_5XX_CONFIRMED`.
- **TRANSTATS:** `ACQUISITION_JOB_VALIDATED`.

MHE y SIGMA conservaron sus bloqueos externos sin degradar TLS ni ignorar robots. TRANSTATS valido su contrato de acquisition job manteniendo GET/HEAD y sin POST automatizado.

## 6. Hallazgos principales

1. El inventario completo de **52 fuentes logicas** quedo auditado y trazado a **46 superficies fisicas unicas**.
2. La ejecucion A6 proceso todas las superficies operacionales genericas previstas sin errores de orquestacion.
3. Los origin-rechecks mostraron que una comparacion site-wide simple produce falsos negativos y falsos positivos si no se consideran URLs relativas, APIs, historicos y cambios temporales.
4. Las fuentes API/JavaScript/custom requirieron validacion especializada; no es metodologicamente correcto evaluarlas solo por coincidencia de enlaces HTML.
5. Las fuentes STATUS_ONLY deben tratarse como decisiones de catalogo y alcance. La auditoria encontro al menos una fuente que merece revisar promocion operacional.
6. Los bloqueos externos MHE y SIGMA se reprodujeron respetando las politicas de seguridad.
7. TRANSTATS continuo siendo coherente como acquisition job metadata-only.

## 7. Limitaciones

- Los portales publicos pueden cambiar entre la corrida B13 y la fecha de auditoria.
- Un recurso que ya no aparece en HTML actual no demuestra por si solo un falso positivo historico.
- Un recurso nuevo encontrado hoy no demuestra por si solo que B13 lo omitio; pudo publicarse despues.
- El auditor no intenta evadir autenticacion, CAPTCHA, WAF, TLS ni robots.
- El overlap agregado de origin-recheck no debe extrapolarse como precision/recall universal de las 52 fuentes.
- La auditoria valida efectividad de discovery y trazabilidad; no sustituye una revision semantica humana exhaustiva de cada archivo publicado por cada institucion.

## 8. Conclusion

La auditoria 52/52 confirma que el Prospector Externo dispone de una arquitectura funcional y auditable para discovery controlado, proyeccion DATAX y tratamiento de fuentes heterogeneas. La evidencia obtenida permite distinguir correctamente entre recursos redescubiertos, diferencias temporales, fuentes especializadas, decisiones status-only y bloqueos externos.

El resultado no debe expresarse como '100 % de Internet rastreado' ni como una garantia de exhaustividad ilimitada. La conclusion defendible es que el sistema fue validado fuente por fuente bajo criterios reproducibles y que sus resultados finales pueden ser auditados contra evidencia independiente.

### Estado de cierre A11

- [x] Matriz 52/52 disponible.
- [x] Metodologias diferenciadas por tipo de fuente.
- [x] Diferencias temporales preservadas.
- [x] STATUS_ONLY reevaluadas.
- [x] MHE / SIGMA / TRANSTATS validados.
- [x] Limitaciones documentadas.
- [x] Informe final generado.

El cierre tecnico definitivo del repositorio corresponde a A12: suite completa, limpieza de artefactos auxiliares, commit, push y tag de auditoria.
