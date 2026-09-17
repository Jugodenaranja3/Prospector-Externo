"""Pruebas unitarias para el normalizador canónico de URLs."""

from prospector_externo.domain.normalizer import UrlNormalizer


def test_url_normalization():
    # Remover fragmentos, ordenar query params y remover tracking
    raw = "HTTPS://WWW.ASFI.GOB.BO:443/estadisticas/?utm_source=twitter&b=2&a=1#seccion-1"
    normalized = UrlNormalizer.normalize(raw)
    assert normalized == "https://www.asfi.gob.bo/estadisticas/?a=1&b=2"

    # Resolución relativa
    base = "https://www.bbv.com.bo/documentos/"
    rel = "../memorias/2024.pdf"
    assert UrlNormalizer.normalize(rel, base_url=base) == "https://www.bbv.com.bo/memorias/2024.pdf"


def test_url_hash_deterministic():
    u1 = "https://www.finrural.org.bo/reportes/2024.pdf"
    h1 = UrlNormalizer.compute_url_hash(u1)
    h2 = UrlNormalizer.compute_url_hash(u1)
    assert h1 == h2
    assert len(h1) == 64
