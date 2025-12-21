"""
vector_indexer.py — Indexador Vectorial

Este módulo gestiona la indexación vectorial de chunks y notas
para búsqueda semántica en el GraphRAG.

RESPONSABILIDAD:
- Indexar chunks de clases (VectorDB-A)
- Indexar notas atómicas (VectorDB-B)
- Búsqueda por similitud
- Detección de duplicados

DOS ÍNDICES:
- VectorDB-A: Chunks/segmentos de clases (evidencia)
- VectorDB-B: Notas atómicas (conceptos)

CONEXIONES:
- Llamado por: phase2_graph.py (contexto, post-commit)
- Lee/Escribe: data/index/vector_chunks/, data/index/vector_notes/
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

# ChromaDB para almacenamiento vectorial
try:
    import chromadb
    from chromadb.config import Settings
    CHROMADB_AVAILABLE = True
except ImportError:
    CHROMADB_AVAILABLE = False
    chromadb = None


# =============================================================================
# CONFIGURACIÓN
# =============================================================================

DEFAULT_EMBEDDING_DIMENSION = 384  # Para modelos pequeños
SIMILARITY_THRESHOLD_DUPLICATE = 0.95
SIMILARITY_THRESHOLD_SIMILAR = 0.7


# =============================================================================
# EMBEDDING SIMPLE (fallback sin modelo externo)
# =============================================================================

def simple_hash_embedding(text: str, dimension: int = DEFAULT_EMBEDDING_DIMENSION) -> list[float]:
    """
    Genera un "embedding" simple basado en hash.
    
    NOTA: Esto es un placeholder. En producción, usar un modelo
    de embeddings real (OpenAI, Sentence Transformers, etc.)
    
    Args:
        text: Texto a embeber
        dimension: Dimensión del vector
        
    Returns:
        Vector de floats
    """
    # Normalizar texto
    text = text.lower().strip()
    
    # Generar hash
    hash_bytes = hashlib.sha256(text.encode()).digest()
    
    # Convertir a floats entre -1 y 1
    embedding = []
    for i in range(dimension):
        byte_idx = i % len(hash_bytes)
        value = (hash_bytes[byte_idx] / 127.5) - 1.0
        embedding.append(value)
    
    return embedding


# =============================================================================
# CLASE BASE DE VECTOR STORE
# =============================================================================

class VectorStore:
    """
    Clase base para almacenamiento vectorial.
    
    Implementación simple basada en archivos JSON.
    Para producción, usar ChromaDB u otro vector store.
    """
    
    def __init__(
        self,
        store_path: Path | str,
        collection_name: str,
        embedding_fn: Callable[[str], list[float]] | None = None,
    ):
        self.store_path = Path(store_path)
        self.collection_name = collection_name
        self.embedding_fn = embedding_fn or simple_hash_embedding
        
        self.store_path.mkdir(parents=True, exist_ok=True)
        self.index_file = self.store_path / f"{collection_name}_index.json"
        
        # Cargar índice existente
        self.documents: dict[str, dict[str, Any]] = {}
        self._load()
    
    def _load(self) -> None:
        """Carga el índice desde disco."""
        if self.index_file.exists():
            try:
                with open(self.index_file, "r") as f:
                    self.documents = json.load(f)
            except Exception as e:
                print(f"Error cargando índice: {e}")
                self.documents = {}
    
    def _save(self) -> None:
        """Persiste el índice a disco."""
        with open(self.index_file, "w") as f:
            json.dump(self.documents, f, indent=2, ensure_ascii=False)
    
    def add(
        self,
        doc_id: str,
        text: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """
        Añade un documento al índice.
        
        Args:
            doc_id: ID único del documento
            text: Texto a indexar
            metadata: Metadatos adicionales
        """
        embedding = self.embedding_fn(text)
        
        self.documents[doc_id] = {
            "text": text,
            "embedding": embedding,
            "metadata": metadata or {},
            "indexed_at": datetime.now().isoformat(),
        }
        
        self._save()
    
    def add_batch(
        self,
        documents: list[dict[str, Any]],
    ) -> int:
        """
        Añade múltiples documentos.
        
        Args:
            documents: Lista de {id, text, metadata}
            
        Returns:
            Número de documentos añadidos
        """
        count = 0
        for doc in documents:
            self.add(
                doc_id=doc["id"],
                text=doc["text"],
                metadata=doc.get("metadata"),
            )
            count += 1
        
        return count
    
    def search(
        self,
        query: str,
        top_k: int = 5,
        min_similarity: float = 0.0,
    ) -> list[dict[str, Any]]:
        """
        Busca documentos similares.
        
        Args:
            query: Texto de búsqueda
            top_k: Número de resultados
            min_similarity: Similitud mínima (0-1)
            
        Returns:
            Lista de {id, text, metadata, similarity}
        """
        if not self.documents:
            return []
        
        query_embedding = self.embedding_fn(query)
        
        # Calcular similitudes
        results = []
        for doc_id, doc in self.documents.items():
            similarity = self._cosine_similarity(query_embedding, doc["embedding"])
            
            if similarity >= min_similarity:
                results.append({
                    "id": doc_id,
                    "text": doc["text"],
                    "metadata": doc["metadata"],
                    "similarity": similarity,
                })
        
        # Ordenar por similitud
        results.sort(key=lambda x: x["similarity"], reverse=True)
        
        return results[:top_k]
    
    def find_duplicates(
        self,
        text: str,
        threshold: float = SIMILARITY_THRESHOLD_DUPLICATE,
    ) -> list[dict[str, Any]]:
        """
        Encuentra posibles duplicados.
        
        Args:
            text: Texto a comparar
            threshold: Umbral de similitud para considerar duplicado
            
        Returns:
            Lista de documentos que podrían ser duplicados
        """
        return self.search(text, top_k=5, min_similarity=threshold)
    
    def delete(self, doc_id: str) -> bool:
        """Elimina un documento del índice."""
        if doc_id in self.documents:
            del self.documents[doc_id]
            self._save()
            return True
        return False
    
    def get(self, doc_id: str) -> dict[str, Any] | None:
        """Obtiene un documento por ID."""
        return self.documents.get(doc_id)
    
    def count(self) -> int:
        """Número de documentos en el índice."""
        return len(self.documents)
    
    @staticmethod
    def _cosine_similarity(a: list[float], b: list[float]) -> float:
        """Calcula similitud coseno entre dos vectores."""
        dot_product = sum(x * y for x, y in zip(a, b))
        norm_a = sum(x * x for x in a) ** 0.5
        norm_b = sum(x * x for x in b) ** 0.5
        
        if norm_a == 0 or norm_b == 0:
            return 0.0
        
        return dot_product / (norm_a * norm_b)


# =============================================================================
# CHROMADB IMPLEMENTATION (si está disponible)
# =============================================================================

class ChromaVectorStore:
    """
    Vector store usando ChromaDB.
    
    Más eficiente y con soporte para embeddings reales.
    """
    
    def __init__(
        self,
        store_path: Path | str,
        collection_name: str,
        embedding_fn: Callable[[str], list[float]] | None = None,
    ):
        if not CHROMADB_AVAILABLE:
            raise ImportError("ChromaDB no está instalado. Instalar con: pip install chromadb")
        
        self.store_path = Path(store_path)
        self.store_path.mkdir(parents=True, exist_ok=True)
        
        # Inicializar cliente persistente
        self.client = chromadb.Client(Settings(
            chroma_db_impl="duckdb+parquet",
            persist_directory=str(self.store_path),
            anonymized_telemetry=False,
        ))
        
        self.collection = self.client.get_or_create_collection(
            name=collection_name,
            metadata={"hnsw:space": "cosine"}
        )
        
        self.embedding_fn = embedding_fn or simple_hash_embedding
    
    def add(
        self,
        doc_id: str,
        text: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Añade un documento."""
        embedding = self.embedding_fn(text)
        
        self.collection.add(
            ids=[doc_id],
            embeddings=[embedding],
            documents=[text],
            metadatas=[metadata or {}],
        )
    
    def search(
        self,
        query: str,
        top_k: int = 5,
        min_similarity: float = 0.0,
    ) -> list[dict[str, Any]]:
        """Busca documentos similares."""
        query_embedding = self.embedding_fn(query)
        
        results = self.collection.query(
            query_embeddings=[query_embedding],
            n_results=top_k,
        )
        
        formatted = []
        if results["ids"] and results["ids"][0]:
            for i, doc_id in enumerate(results["ids"][0]):
                similarity = 1.0 - (results["distances"][0][i] if results["distances"] else 0)
                
                if similarity >= min_similarity:
                    formatted.append({
                        "id": doc_id,
                        "text": results["documents"][0][i] if results["documents"] else "",
                        "metadata": results["metadatas"][0][i] if results["metadatas"] else {},
                        "similarity": similarity,
                    })
        
        return formatted
    
    def delete(self, doc_id: str) -> bool:
        """Elimina un documento."""
        try:
            self.collection.delete(ids=[doc_id])
            return True
        except:
            return False
    
    def count(self) -> int:
        """Número de documentos."""
        return self.collection.count()


# =============================================================================
# ÍNDICES ESPECÍFICOS
# =============================================================================

class ChunkIndex:
    """
    Índice de chunks (VectorDB-A).
    
    Indexa fragmentos de clases para recuperación de evidencia.
    """
    
    def __init__(self, index_path: Path | str):
        store_path = Path(index_path) / "vector_chunks"
        
        # Usar implementación simple por defecto
        self.store = VectorStore(
            store_path=store_path,
            collection_name="chunks",
        )
    
    def index_chunks(
        self,
        chunks: list[dict[str, Any]],
        lesson_id: str,
    ) -> int:
        """
        Indexa chunks de una lección.
        
        Args:
            chunks: Lista de chunks con {id, content, topic_id, ...}
            lesson_id: ID de la lección fuente
            
        Returns:
            Número de chunks indexados
        """
        documents = []
        for chunk in chunks:
            documents.append({
                "id": chunk.get("id", ""),
                "text": chunk.get("content", ""),
                "metadata": {
                    "lesson_id": lesson_id,
                    "topic_id": chunk.get("topic_id", ""),
                    "word_count": chunk.get("word_count", 0),
                    "anchor_text": chunk.get("anchor_text", "")[:100],
                }
            })
        
        return self.store.add_batch(documents)
    
    def search_evidence(
        self,
        query: str,
        top_k: int = 5,
    ) -> list[dict[str, Any]]:
        """Busca chunks relevantes como evidencia."""
        return self.store.search(query, top_k=top_k, min_similarity=0.3)
    
    def get_stats(self) -> dict[str, Any]:
        """Estadísticas del índice."""
        return {
            "total_chunks": self.store.count(),
            "collection": "chunks",
        }


class NoteIndex:
    """
    Índice de notas atómicas (VectorDB-B).
    
    Indexa notas para recuperación conceptual y detección de duplicados.
    """
    
    def __init__(self, index_path: Path | str):
        store_path = Path(index_path) / "vector_notes"
        
        self.store = VectorStore(
            store_path=store_path,
            collection_name="notes",
        )
    
    def index_note(self, note: dict[str, Any]) -> None:
        """
        Indexa una nota atómica.
        
        Args:
            note: Nota con {id, title, content, frontmatter, ...}
        """
        # Combinar título y contenido para mejor búsqueda
        text = f"{note.get('title', '')}\n\n{note.get('content', '')}"
        
        self.store.add(
            doc_id=note.get("id", ""),
            text=text,
            metadata={
                "title": note.get("title", ""),
                "type": note.get("frontmatter", {}).get("type", "note"),
                "tags": note.get("frontmatter", {}).get("tags", []),
                "source_id": note.get("source_id", ""),
            }
        )
    
    def index_notes(self, notes: list[dict[str, Any]]) -> int:
        """Indexa múltiples notas."""
        for note in notes:
            self.index_note(note)
        return len(notes)
    
    def search_similar(
        self,
        query: str,
        top_k: int = 5,
    ) -> list[dict[str, Any]]:
        """Busca notas conceptualmente similares."""
        return self.store.search(query, top_k=top_k, min_similarity=0.4)
    
    def find_duplicates(
        self,
        title: str,
        content: str,
    ) -> list[dict[str, Any]]:
        """
        Busca posibles duplicados de una nota.
        
        Args:
            title: Título de la nota
            content: Contenido de la nota
            
        Returns:
            Lista de notas que podrían ser duplicados
        """
        text = f"{title}\n\n{content}"
        return self.store.find_duplicates(text, threshold=0.85)
    
    def get_stats(self) -> dict[str, Any]:
        """Estadísticas del índice."""
        return {
            "total_notes": self.store.count(),
            "collection": "notes",
        }


# =============================================================================
# FUNCIONES DE CONVENIENCIA
# =============================================================================

def index_lesson_chunks(
    index_path: Path | str,
    chunks: list[dict[str, Any]],
    lesson_id: str,
) -> int:
    """
    Indexa chunks de una lección.
    
    Args:
        index_path: Path al directorio de índices
        chunks: Chunks a indexar
        lesson_id: ID de la lección
        
    Returns:
        Número de chunks indexados
    """
    index = ChunkIndex(index_path)
    return index.index_chunks(chunks, lesson_id)


def index_approved_notes(
    index_path: Path | str,
    notes: list[dict[str, Any]],
) -> int:
    """
    Indexa notas aprobadas.
    
    Args:
        index_path: Path al directorio de índices
        notes: Notas a indexar
        
    Returns:
        Número de notas indexadas
    """
    index = NoteIndex(index_path)
    return index.index_notes(notes)


def search_similar_notes(
    index_path: Path | str,
    query: str,
    top_k: int = 5,
) -> list[dict[str, Any]]:
    """
    Busca notas similares a una consulta.
    
    Args:
        index_path: Path al directorio de índices
        query: Texto de búsqueda
        top_k: Número de resultados
        
    Returns:
        Lista de notas similares
    """
    index = NoteIndex(index_path)
    return index.search_similar(query, top_k)


def check_for_duplicates(
    index_path: Path | str,
    title: str,
    content: str,
) -> list[dict[str, Any]]:
    """
    Verifica si una nota podría ser duplicado.
    
    Args:
        index_path: Path al directorio de índices
        title: Título de la nota
        content: Contenido de la nota
        
    Returns:
        Lista de posibles duplicados
    """
    index = NoteIndex(index_path)
    return index.find_duplicates(title, content)