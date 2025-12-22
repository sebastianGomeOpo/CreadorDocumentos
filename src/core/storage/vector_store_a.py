"""
vector_store_a.py — Storage Driver para VectorDB-A (Evidence/Chunks)

Este módulo gestiona la configuración física y el acceso a la base de datos
vectorial de 'Chunks' (Evidencia).

RESPONSABILIDAD:
- Definir la ruta física: data/index/vector_chunks
- Proveer configuración específica para embeddings de fragmentos
- Gestión de backups específicos para el índice de chunks

CONEXIONES:
- Usado por: vector_indexer.py (ChunkIndex)
"""

from __future__ import annotations

import shutil
from pathlib import Path
from datetime import datetime

# Rutas estándar
DEFAULT_CHUNK_INDEX_PATH = Path("data/index/vector_chunks")
COLLECTION_NAME = "chunks"

class EvidenceVectorStorage:
    """
    Gestor de almacenamiento físico para VectorDB-A (Chunks).
    """
    
    def __init__(self, base_path: Path | str | None = None):
        if base_path:
            self.store_path = Path(base_path) / "index" / "vector_chunks"
        else:
            self.store_path = DEFAULT_CHUNK_INDEX_PATH
            
        self.store_path.mkdir(parents=True, exist_ok=True)
        
    def get_store_path(self) -> Path:
        """Retorna la ruta absoluta al directorio del store."""
        return self.store_path.absolute()
        
    def get_collection_name(self) -> str:
        """Retorna el nombre canónico de la colección."""
        return COLLECTION_NAME
        
    def exists(self) -> bool:
        """Verifica si el índice ya ha sido inicializado."""
        # En ChromaDB/JSON, suele haber archivos dentro
        return any(self.store_path.iterdir())
        
    def create_snapshot(self) -> Path:
        """
        Crea una copia de seguridad del índice de chunks.
        Útil antes de re-indexaciones masivas.
        """
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_dir = self.store_path.parent / "backups" / f"vector_chunks_{timestamp}"
        backup_dir.parent.mkdir(exist_ok=True)
        
        shutil.copytree(self.store_path, backup_dir)
        return backup_dir
        
    def clear(self) -> None:
        """Elimina físicamente todos los datos del índice."""
        if self.store_path.exists():
            shutil.rmtree(self.store_path)
            self.store_path.mkdir()

def get_chunk_storage_config(base_path: Path | str) -> dict:
    """Helper para obtener configuración rápida."""
    storage = EvidenceVectorStorage(base_path)
    return {
        "path": str(storage.get_store_path()),
        "collection": storage.get_collection_name()
    }