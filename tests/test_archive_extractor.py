"""
Pruebas unitarias para el extractor de archivos comprimidos en memoria (ArchiveExtractor).
"""

import io
import zipfile
import tarfile
import pytest
from prospector_externo.infrastructure.archive_extractor import ArchiveExtractor, ArchiveEntry


def create_sample_zip_bytes() -> bytes:
    """Crea un buffer ZIP en memoria con archivos de prueba."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("reporte_financiero_01_2026.pdf", b"%PDF-1.4 Mock PDF Content")
        zf.writestr("notas_explicativas.txt", b"Texto de notas explicativas")
        zf.writestr("subfolder/resumen_2026.xlsx", b"Mock Excel Content")
    return buf.getvalue()


def create_sample_tar_bytes() -> bytes:
    """Crea un buffer TAR en memoria con archivos de prueba."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tf:
        data = b"Contenido CSV simulado"
        ti = tarfile.TarInfo(name="datos_2026.csv")
        ti.size = len(data)
        tf.addfile(ti, io.BytesIO(data))
    return buf.getvalue()


def test_is_archive():
    assert ArchiveExtractor.is_archive("https://example.org/download/archivo.zip") is True
    assert ArchiveExtractor.is_archive("archivo.tar") is True
    assert ArchiveExtractor.is_archive("paquete.tgz") is True
    assert ArchiveExtractor.is_archive("archivo.gz") is True
    assert ArchiveExtractor.is_archive("reporte.pdf") is False
    assert ArchiveExtractor.is_archive("datos.xlsx") is False


def test_inspect_zip_archive_bytes():
    zip_bytes = create_sample_zip_bytes()
    entries = ArchiveExtractor.inspect_archive_bytes(zip_bytes, filename_hint="reportes.zip")

    assert len(entries) == 3
    filenames = [e.filename for e in entries]
    assert "reporte_financiero_01_2026.pdf" in filenames
    assert "subfolder/resumen_2026.xlsx" in filenames
    assert "notas_explicativas.txt" in filenames

    pdf_entry = next(e for e in entries if e.filename == "reporte_financiero_01_2026.pdf")
    assert pdf_entry.extension == ".pdf"
    assert pdf_entry.size_bytes > 0
    assert pdf_entry.is_dir is False


def test_inspect_tar_archive_bytes():
    tar_bytes = create_sample_tar_bytes()
    entries = ArchiveExtractor.inspect_archive_bytes(tar_bytes, filename_hint="datos.tar")

    assert len(entries) == 1
    assert entries[0].filename == "datos_2026.csv"
    assert entries[0].extension == ".csv"
    assert entries[0].size_bytes > 0
