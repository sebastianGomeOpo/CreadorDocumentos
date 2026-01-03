"""
Storage Package
---------------
Drivers y adaptadores para la persistencia de datos en el sistema de archivos.
Soporta:
- Bundles (JSON)
- Vault (Markdown + WAL)
"""

from core.storage.bundles_fs import BundleStore
from core.storage.vault_io import VaultWriter
from core.storage.wal import WriteAheadLog

__all__ = [
    "BundleStore",
    "VaultWriter",
    "WriteAheadLog",
]
