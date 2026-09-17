"""
Extractor e inspector de archivos comprimidos en memoria RAM.
Soporta .zip, .tar, .tar.gz, .tgz sin persistir archivos en disco.
"""

import io
import os
import zipfile
import tarfile
import logging
from typing import List, Dict, Any, Optional

logger = logging.getLogger("prospector.infrastructure.archive")


class ArchiveEntry:
    def __init__(self, filename: str, size_bytes: int, is_dir: bool = False):
        self.filename = filename
        self.size_bytes = size_bytes
        self.is_dir = is_dir
        self.extension = os.path.splitext(filename)[1].lower()


class ArchiveExtractor:
    """Inspecciona y cataloga archivos comprimidos en memoria RAM."""

    COMPRESSED_EXTENSIONS = {".zip", ".tar", ".gz", ".tgz", ".bz2"}

    @classmethod
    def is_archive(cls, url_or_filename: str) -> bool:
        """Determina si la extensión corresponde a un contenedor comprimido soportado."""
        lower = url_or_filename.lower()
        return any(lower.endswith(ext) for ext in cls.COMPRESSED_EXTENSIONS)

    @classmethod
    def inspect_archive_bytes(cls, archive_bytes: bytes, filename_hint: str = "") -> List[ArchiveEntry]:
        """
        Inspecciona el contenido binario de un archivo comprimido en memoria.
        Retorna la lista de archivos contenidos.
        """
        entries: List[ArchiveEntry] = []
        hint = filename_hint.lower()

        # Intentar como ZIP
        if hint.endswith(".zip") or zipfile.is_zipfile(io.BytesIO(archive_bytes)):
            try:
                with zipfile.ZipFile(io.BytesIO(archive_bytes)) as zf:
                    for info in zf.infolist():
                        if not info.is_dir():
                            # Sanitizar nombre de archivo (evitar path traversal)
                            clean_name = os.path.basename(info.filename)
                            if clean_name and not clean_name.startswith("."):
                                entries.append(ArchiveEntry(
                                    filename=info.filename,
                                    size_bytes=info.file_size,
                                    is_dir=False
                                ))
                return entries
            except Exception as e:
                logger.warning(f"Error inspeccionando archivo ZIP en memoria: {e}")

        # Intentar como TAR / TAR.GZ
        try:
            with tarfile.open(fileobj=io.BytesIO(archive_bytes)) as tf:
                for member in tf.getmembers():
                    if member.isfile():
                        clean_name = os.path.basename(member.name)
                        if clean_name and not clean_name.startswith("."):
                            entries.append(ArchiveEntry(
                                filename=member.name,
                                size_bytes=member.size,
                                is_dir=False
                            ))
            return entries
        except Exception as e:
            logger.warning(f"Error inspeccionando archivo TAR/GZ en memoria: {e}")

        return entries
