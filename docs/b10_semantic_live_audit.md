# B10B.2.1 — Parser robusto del log final

B10B.2 añadió autodetección de encoding, pero una prueba UTF-8 falló en Windows.

El ajuste B10B.2.1 elimina dos supuestos frágiles:

1. ya no se elige un encoding alternativo si un UTF-8 estricto contiene headers válidos;
2. los headers `[01/41] SOURCE (source_id)` ya no tienen que ocupar exactamente toda la línea.

Esto último es importante porque PowerShell puede prefijar registros de procesos nativos.

## Estrategia

- BOM UTF-16 / UTF-8: autoritativo;
- UTF-8 estricto + headers: se acepta inmediatamente;
- si no hay evidencia estructural: UTF-16LE, UTF-16BE y CP1252;
- el número de headers domina el ranking de candidatos;
- parser de headers tolera prefijos de PowerShell;
- no se repite el crawl.

La auditoría conserva las clasificaciones semánticas de B10B.1.
