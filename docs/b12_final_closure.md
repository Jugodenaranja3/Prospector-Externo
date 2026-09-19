# B12 — Cierre final de entrega empresarial DATAX

## Estado final

B12 queda cerrado cuando el paquete empresarial pasa una auditoría offline
fresca y conserva los resultados obtenidos en B12B:

- `ENTERPRISE_PACKAGE_READY`;
- 41/41 fuentes operacionales indexadas;
- 38 JSON legacy;
- 5407 registros legacy;
- 1 manifest de adquisición TRANSTATS;
- 2 bloqueos externos;
- `manifest.json` como entry point;
- 5 grupos de payload exactamente duplicado;
- los 5 corresponden a fuentes lógicas que comparten fuente física;
- 0 duplicados exactos entre fuentes físicas distintas;
- checksums sin mismatch;
- ZIP byte-equivalente al directorio de entrega.

## Checksums versus entradas ZIP

El auditor B12B reporta 44 archivos cubiertos por SHA-256 y 45 entradas en el
ZIP.

Esto es correcto: `checksums.sha256` contiene el digest de todos los demás
archivos, pero no puede incluir de manera estable el hash de sí mismo. Por eso:

`ZIP entries = checksum-covered files + 1`

Esta relación se valida explícitamente en el cierre B12.

## Duplicados

No se eliminan los cinco payloads exactos duplicados. Todos se explican por
fuentes lógicas que comparten una misma configuración/fuente física. Mantener
los artefactos por `logical_source_id` preserva la interfaz empresarial y la
trazabilidad.

Un duplicado exacto entre fuentes físicas distintas sí reabriría B12 para
revisión.

## Contrato de consumo

El consumidor debe:

1. abrir `manifest.json`;
2. seleccionar por `logical_source_id`;
3. leer `classification`;
4. abrir el `artifact` declarado.

Para `DATAX_READY`, el artefacto es:

`sources/<logical_source_id>.json`

y conserva `ESTADISTICAS` como raíz.

## Auditor

`apps.b12_closure_audit.main`

Resultado runtime:

`.runtime/b12_closure/latest.json`
