"""
hierarchical_index.py — Índice Jerárquico con ChromaDB

Almacena chunks y bloques con sus relaciones en ChromaDB.

Dos colecciones separadas:
- chunks: Para búsqueda precisa
- blocks: Para búsqueda de contexto amplio

Metadata incluye:
- Jerarquía (block_id, position, neighbors)
- Tipo de bloque
- Heading (si existe)

Permite:
- Búsqueda a nivel chunk
- Búsqueda a nivel bloque
- Recuperación de contexto estructural
- Expansión a vecinos/padre
"""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from dotenv import load_dotenv

load_dotenv()


# =============================================================================
# ESTRUCTURAS DE DATOS
# =============================================================================

@dataclass
class IndexedChunk:
    """Chunk almacenado en el índice."""
    chunk_id: str
    content: str
    block_id: str
    position_in_block: int
    total_in_block: int
    prev_chunk_id: Optional[str]
    next_chunk_id: Optional[str]
    source_id: str
    
    def to_metadata(self) -> dict:
        return {
            "chunk_id": self.chunk_id,
            "block_id": self.block_id,
            "position_in_block": self.position_in_block,
            "total_in_block": self.total_in_block,
            "prev_chunk_id": self.prev_chunk_id or "",
            "next_chunk_id": self.next_chunk_id or "",
            "source_id": self.source_id,
        }


@dataclass
class IndexedBlock:
    """Bloque almacenado en el índice."""
    block_id: str
    content: str
    heading: Optional[str]
    block_type: str
    chunk_ids: list[str]
    position_in_doc: int
    prev_block_id: Optional[str]
    next_block_id: Optional[str]
    source_id: str
    
    def to_metadata(self) -> dict:
        return {
            "block_id": self.block_id,
            "heading": self.heading or "",
            "block_type": self.block_type,
            "chunk_ids_json": json.dumps(self.chunk_ids),
            "position_in_doc": self.position_in_doc,
            "prev_block_id": self.prev_block_id or "",
            "next_block_id": self.next_block_id or "",
            "source_id": self.source_id,
        }


@dataclass
class SearchResult:
    """Resultado de búsqueda con metadata."""
    id: str
    content: str
    score: float
    metadata: dict
    granularity: str  # "chunk" | "block"


# =============================================================================
# CONFIGURACIÓN
# =============================================================================

DEFAULT_INDEX_DIR = Path("data/temp/hierarchical_index")
CHUNKS_COLLECTION = "chunks"
BLOCKS_COLLECTION = "blocks"


# =============================================================================
# ÍNDICE JERÁRQUICO
# =============================================================================

class HierarchicalIndex:
    """
    Índice jerárquico con dos niveles (chunks y blocks).
    
    Usa ChromaDB para almacenamiento vectorial con metadata
    que preserva la estructura del documento.
    """
    
    def __init__(
        self,
        base_path: Path | str = DEFAULT_INDEX_DIR,
        source_id: Optional[str] = None,
    ):
        self.base_path = Path(base_path)
        self.source_id = source_id
        
        # Path específico para esta fuente
        if source_id:
            self.index_path = self.base_path / source_id
        else:
            self.index_path = self.base_path
        
        self._client = None
        self._chunks_collection = None
        self._blocks_collection = None
        self._embeddings = None
    
    @property
    def client(self):
        """Lazy loading del cliente ChromaDB."""
        if self._client is None:
            try:
                import chromadb
                self.index_path.mkdir(parents=True, exist_ok=True)
                self._client = chromadb.PersistentClient(
                    path=str(self.index_path)
                )
            except ImportError:
                raise ImportError("chromadb no instalado. Ejecuta: pip install chromadb")
        return self._client
    
    @property
    def embeddings(self):
        """Lazy loading del embedder."""
        if self._embeddings is None:
            from langchain_openai import OpenAIEmbeddings
            self._embeddings = OpenAIEmbeddings(
                model="text-embedding-3-small",
                api_key=os.getenv("OPENAI_API_KEY"),
            )
        return self._embeddings
    
    @property
    def chunks_collection(self):
        """Colección de chunks."""
        if self._chunks_collection is None:
            self._chunks_collection = self.client.get_or_create_collection(
                name=CHUNKS_COLLECTION,
                metadata={"hnsw:space": "cosine"}
            )
        return self._chunks_collection
    
    @property
    def blocks_collection(self):
        """Colección de bloques."""
        if self._blocks_collection is None:
            self._blocks_collection = self.client.get_or_create_collection(
                name=BLOCKS_COLLECTION,
                metadata={"hnsw:space": "cosine"}
            )
        return self._blocks_collection
    
    def index_document(
        self,
        hierarchical_doc,  # HierarchicalDocument
        doc_embeddings,    # DocumentEmbeddings
    ) -> dict[str, int]:
        """
        Indexa un documento jerárquico completo.
        
        Args:
            hierarchical_doc: Documento con estructura
            doc_embeddings: Embeddings pre-calculados
            
        Returns:
            Estadísticas de indexación
        """
        # 1. Indexar chunks
        chunk_ids = []
        chunk_embeddings = []
        chunk_documents = []
        chunk_metadatas = []
        
        for chunk in hierarchical_doc.chunks:
            chunk_emb = doc_embeddings.chunk_embeddings.get(chunk.chunk_id)
            if not chunk_emb:
                continue
            
            indexed = IndexedChunk(
                chunk_id=chunk.chunk_id,
                content=chunk.content,
                block_id=chunk.block_id,
                position_in_block=chunk.position_in_block,
                total_in_block=chunk.total_in_block,
                prev_chunk_id=chunk.prev_chunk_id,
                next_chunk_id=chunk.next_chunk_id,
                source_id=hierarchical_doc.source_id,
            )
            
            chunk_ids.append(chunk.chunk_id)
            chunk_embeddings.append(chunk_emb.chunk_embedding)
            chunk_documents.append(chunk.content)
            chunk_metadatas.append(indexed.to_metadata())
        
        if chunk_ids:
            self.chunks_collection.add(
                ids=chunk_ids,
                embeddings=chunk_embeddings,
                documents=chunk_documents,
                metadatas=chunk_metadatas,
            )
        
        # 2. Indexar bloques
        block_ids = []
        block_embeddings = []
        block_documents = []
        block_metadatas = []
        
        for block in hierarchical_doc.blocks:
            block_emb = doc_embeddings.block_embeddings.get(block.block_id)
            if not block_emb:
                continue
            
            indexed = IndexedBlock(
                block_id=block.block_id,
                content=block.content,
                heading=block.heading,
                block_type=block.block_type.value,
                chunk_ids=block.chunk_ids,
                position_in_doc=block.position_in_doc,
                prev_block_id=block.prev_block_id,
                next_block_id=block.next_block_id,
                source_id=hierarchical_doc.source_id,
            )
            
            block_ids.append(block.block_id)
            block_embeddings.append(block_emb)
            block_documents.append(block.summary)
            block_metadatas.append(indexed.to_metadata())
        
        if block_ids:
            self.blocks_collection.add(
                ids=block_ids,
                embeddings=block_embeddings,
                documents=block_documents,
                metadatas=block_metadatas,
            )
        
        return {
            "chunks_indexed": len(chunk_ids),
            "blocks_indexed": len(block_ids),
            "source_id": hierarchical_doc.source_id,
        }
    
    def search_chunks(
        self,
        query_embedding: list[float],
        k: int = 10,
        filter_source: Optional[str] = None,
    ) -> list[SearchResult]:
        """
        Busca en la colección de chunks.
        
        Args:
            query_embedding: Vector de búsqueda
            k: Número de resultados
            filter_source: Filtrar por source_id
            
        Returns:
            Lista de SearchResult
        """
        where_filter = None
        if filter_source:
            where_filter = {"source_id": filter_source}
        
        results = self.chunks_collection.query(
            query_embeddings=[query_embedding],
            n_results=k,
            where=where_filter,
            include=["documents", "metadatas", "distances"],
        )
        
        return self._parse_results(results, "chunk")
    
    def search_blocks(
        self,
        query_embedding: list[float],
        k: int = 5,
        filter_source: Optional[str] = None,
    ) -> list[SearchResult]:
        """
        Busca en la colección de bloques.
        
        Args:
            query_embedding: Vector de búsqueda
            k: Número de resultados
            filter_source: Filtrar por source_id
            
        Returns:
            Lista de SearchResult
        """
        where_filter = None
        if filter_source:
            where_filter = {"source_id": filter_source}
        
        results = self.blocks_collection.query(
            query_embeddings=[query_embedding],
            n_results=k,
            where=where_filter,
            include=["documents", "metadatas", "distances"],
        )
        
        return self._parse_results(results, "block")
    
    def search_both(
        self,
        query_embedding: list[float],
        k_chunks: int = 10,
        k_blocks: int = 3,
        filter_source: Optional[str] = None,
    ) -> tuple[list[SearchResult], list[SearchResult]]:
        """
        Busca en ambas colecciones simultáneamente.
        
        Returns:
            (chunk_results, block_results)
        """
        chunks = self.search_chunks(query_embedding, k_chunks, filter_source)
        blocks = self.search_blocks(query_embedding, k_blocks, filter_source)
        return chunks, blocks
    
    def get_chunk_by_id(self, chunk_id: str) -> Optional[SearchResult]:
        """Obtiene un chunk por ID."""
        try:
            result = self.chunks_collection.get(
                ids=[chunk_id],
                include=["documents", "metadatas"],
            )
            if result["ids"]:
                return SearchResult(
                    id=result["ids"][0],
                    content=result["documents"][0] if result["documents"] else "",
                    score=1.0,
                    metadata=result["metadatas"][0] if result["metadatas"] else {},
                    granularity="chunk",
                )
        except Exception:
            pass
        return None
    
    def get_block_by_id(self, block_id: str) -> Optional[SearchResult]:
        """Obtiene un bloque por ID."""
        try:
            result = self.blocks_collection.get(
                ids=[block_id],
                include=["documents", "metadatas"],
            )
            if result["ids"]:
                return SearchResult(
                    id=result["ids"][0],
                    content=result["documents"][0] if result["documents"] else "",
                    score=1.0,
                    metadata=result["metadatas"][0] if result["metadatas"] else {},
                    granularity="block",
                )
        except Exception:
            pass
        return None
    
    def get_parent_block(self, chunk_id: str) -> Optional[SearchResult]:
        """Obtiene el bloque padre de un chunk."""
        chunk = self.get_chunk_by_id(chunk_id)
        if chunk and chunk.metadata.get("block_id"):
            return self.get_block_by_id(chunk.metadata["block_id"])
        return None
    
    def get_neighbor_chunks(
        self,
        chunk_id: str,
        window: int = 1,
    ) -> list[SearchResult]:
        """
        Obtiene chunks vecinos.
        
        Args:
            chunk_id: ID del chunk central
            window: Cuántos vecinos a cada lado
            
        Returns:
            Lista ordenada de chunks [prev..., current, ...next]
        """
        neighbors = []
        
        chunk = self.get_chunk_by_id(chunk_id)
        if not chunk:
            return neighbors
        
        # Navegar hacia atrás
        prev_ids = []
        current_id = chunk.metadata.get("prev_chunk_id")
        for _ in range(window):
            if not current_id:
                break
            prev_chunk = self.get_chunk_by_id(current_id)
            if prev_chunk:
                prev_ids.insert(0, prev_chunk)
                current_id = prev_chunk.metadata.get("prev_chunk_id")
            else:
                break
        
        neighbors.extend(prev_ids)
        neighbors.append(chunk)
        
        # Navegar hacia adelante
        current_id = chunk.metadata.get("next_chunk_id")
        for _ in range(window):
            if not current_id:
                break
            next_chunk = self.get_chunk_by_id(current_id)
            if next_chunk:
                neighbors.append(next_chunk)
                current_id = next_chunk.metadata.get("next_chunk_id")
            else:
                break
        
        return neighbors
    
    def get_block_chunks(self, block_id: str) -> list[SearchResult]:
        """Obtiene todos los chunks de un bloque."""
        block = self.get_block_by_id(block_id)
        if not block:
            return []
        
        chunk_ids_json = block.metadata.get("chunk_ids_json", "[]")
        try:
            chunk_ids = json.loads(chunk_ids_json)
        except json.JSONDecodeError:
            return []
        
        chunks = []
        for cid in chunk_ids:
            chunk = self.get_chunk_by_id(cid)
            if chunk:
                chunks.append(chunk)
        
        # Ordenar por posición
        chunks.sort(key=lambda c: c.metadata.get("position_in_block", 0))
        return chunks
    
    def delete_source(self, source_id: str) -> dict[str, int]:
        """
        Elimina todos los datos de una fuente.
        
        Returns:
            Estadísticas de eliminación
        """
        # Obtener IDs a eliminar
        chunk_results = self.chunks_collection.get(
            where={"source_id": source_id},
            include=[],
        )
        block_results = self.blocks_collection.get(
            where={"source_id": source_id},
            include=[],
        )
        
        chunks_deleted = 0
        blocks_deleted = 0
        
        if chunk_results["ids"]:
            self.chunks_collection.delete(ids=chunk_results["ids"])
            chunks_deleted = len(chunk_results["ids"])
        
        if block_results["ids"]:
            self.blocks_collection.delete(ids=block_results["ids"])
            blocks_deleted = len(block_results["ids"])
        
        return {
            "chunks_deleted": chunks_deleted,
            "blocks_deleted": blocks_deleted,
        }
    
    def get_stats(self) -> dict[str, Any]:
        """Obtiene estadísticas del índice."""
        return {
            "chunks_count": self.chunks_collection.count(),
            "blocks_count": self.blocks_collection.count(),
            "index_path": str(self.index_path),
        }
    
    def cleanup(self):
        """Elimina el índice completo."""
        if self.index_path.exists():
            shutil.rmtree(self.index_path)
        self._client = None
        self._chunks_collection = None
        self._blocks_collection = None
    
    def _parse_results(
        self,
        raw_results: dict,
        granularity: str,
    ) -> list[SearchResult]:
        """Parsea resultados de ChromaDB a SearchResult."""
        results = []
        
        if not raw_results["ids"] or not raw_results["ids"][0]:
            return results
        
        ids = raw_results["ids"][0]
        documents = raw_results.get("documents", [[]])[0]
        metadatas = raw_results.get("metadatas", [[]])[0]
        distances = raw_results.get("distances", [[]])[0]
        
        for i, doc_id in enumerate(ids):
            # ChromaDB retorna distancia, convertir a score (1 - distancia para coseno)
            distance = distances[i] if i < len(distances) else 0
            score = 1 - distance  # Cosine distance → similarity
            
            results.append(SearchResult(
                id=doc_id,
                content=documents[i] if i < len(documents) else "",
                score=score,
                metadata=metadatas[i] if i < len(metadatas) else {},
                granularity=granularity,
            ))
        
        return results


# =============================================================================
# FUNCIONES DE CONVENIENCIA
# =============================================================================

def create_index(
    source_id: str,
    base_path: Path | str = DEFAULT_INDEX_DIR,
) -> HierarchicalIndex:
    """Crea instancia del índice para una fuente."""
    return HierarchicalIndex(base_path, source_id)


def index_document(
    hierarchical_doc,
    doc_embeddings,
    base_path: Path | str = DEFAULT_INDEX_DIR,
) -> dict[str, Any]:
    """
    Función de conveniencia para indexar un documento.
    
    Args:
        hierarchical_doc: Documento jerárquico
        doc_embeddings: Embeddings del documento
        base_path: Directorio del índice
        
    Returns:
        Estadísticas de indexación
    """
    index = HierarchicalIndex(base_path, hierarchical_doc.source_id)
    return index.index_document(hierarchical_doc, doc_embeddings)