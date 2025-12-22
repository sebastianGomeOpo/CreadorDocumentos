"""
Storage Package
---------------
Drivers y adaptadores para la persistencia de datos en el sistema de archivos.
Soporta:
- Bundles (JSON)
- Vault (Markdown + WAL)
- Grafos (NetworkX/JSON/GML)
- Vectores (Chroma/JSON)
- Tablas grandes (Parquet)
"""

from core.storage.bundles_fs import BundleStore
from core.storage.graph_store import GraphStore
from core.storage.parquet_store import ChunkStore, MetricsStore, ProcessingHistoryStore
from core.storage.vault_io import VaultWriter
from core.storage.wal import WriteAheadLog
from core.storage.vector_store_a import EvidenceVectorStorage
from core.storage.vector_store_b import ConceptVectorStorage

__all__ = [
    "BundleStore",
    "GraphStore",
    "ChunkStore",
    "MetricsStore",
    "ProcessingHistoryStore",
    "VaultWriter",
    "WriteAheadLog",
    "EvidenceVectorStorage",
    "ConceptVectorStorage",
]