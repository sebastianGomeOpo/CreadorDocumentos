"""
multi_granular_embedder.py — Embeddings Multi-Granulares

Genera embeddings a dos niveles:
- CHUNK: Embedding del fragmento individual (precisión)
- BLOCK: Embedding del bloque padre (coherencia/contexto)

Opcionalmente genera embedding "contextualizado":
- Chunk + contexto del padre = mejor representación semántica

Esto permite:
- Búsqueda precisa a nivel chunk
- Búsqueda amplia a nivel bloque
- Re-ranking con contexto
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional
import numpy as np

from dotenv import load_dotenv

load_dotenv()


# =============================================================================
# ESTRUCTURAS DE DATOS
# =============================================================================

@dataclass
class EmbeddingResult:
    """Resultado de embedding con metadata."""
    id: str
    embedding: list[float]
    text_preview: str
    token_count: int
    granularity: str  # "chunk" | "block" | "contextualized"


@dataclass
class ChunkEmbeddings:
    """Embeddings completos de un chunk."""
    chunk_id: str
    chunk_embedding: list[float]
    block_embedding: Optional[list[float]] = None
    contextualized_embedding: Optional[list[float]] = None


@dataclass
class DocumentEmbeddings:
    """Todos los embeddings de un documento."""
    source_id: str
    chunk_embeddings: dict[str, ChunkEmbeddings]
    block_embeddings: dict[str, list[float]]
    embedding_model: str
    embedding_dim: int


# =============================================================================
# CONFIGURACIÓN
# =============================================================================

DEFAULT_EMBEDDING_MODEL = "text-embedding-3-small"
EMBEDDING_DIMENSIONS = {
    "text-embedding-3-small": 1536,
    "text-embedding-3-large": 3072,
    "text-embedding-ada-002": 1536,
}

# Límite de tokens por request (modelo small)
MAX_TOKENS_PER_BATCH = 8000
APPROX_CHARS_PER_TOKEN = 4


# =============================================================================
# EMBEDDER BASE
# =============================================================================

class MultiGranularEmbedder:
    """
    Generador de embeddings multi-granulares.
    
    Soporta:
    - Embeddings individuales de chunks
    - Embeddings de bloques padre
    - Embeddings contextualizados (chunk + padre)
    - Batch processing para eficiencia
    """
    
    def __init__(
        self,
        model: str = DEFAULT_EMBEDDING_MODEL,
        api_key: Optional[str] = None,
    ):
        self.model = model
        self.api_key = api_key or os.getenv("OPENAI_API_KEY")
        self.embedding_dim = EMBEDDING_DIMENSIONS.get(model, 1536)
        
        self._embeddings_client = None
    
    @property
    def embeddings_client(self):
        """Lazy loading del cliente de embeddings."""
        if self._embeddings_client is None:
            from langchain_openai import OpenAIEmbeddings
            self._embeddings_client = OpenAIEmbeddings(
                model=self.model,
                api_key=self.api_key,
            )
        return self._embeddings_client
    
    def embed_text(self, text: str) -> list[float]:
        """Genera embedding para un texto."""
        return self.embeddings_client.embed_query(text)
    
    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """Genera embeddings para múltiples textos (batch)."""
        if not texts:
            return []
        return self.embeddings_client.embed_documents(texts)
    
    def embed_document(
        self,
        hierarchical_doc,  # HierarchicalDocument
        include_contextualized: bool = True,
    ) -> DocumentEmbeddings:
        """
        Genera todos los embeddings para un documento jerárquico.
        
        Args:
            hierarchical_doc: Documento con estructura jerárquica
            include_contextualized: Si generar embeddings chunk+contexto
            
        Returns:
            DocumentEmbeddings con todos los embeddings
        """
        # 1. Preparar textos para batch embedding
        chunk_texts = []
        chunk_ids = []
        block_texts = []
        block_ids = []
        contextualized_texts = []
        contextualized_ids = []
        
        for chunk in hierarchical_doc.chunks:
            chunk_texts.append(chunk.content)
            chunk_ids.append(chunk.chunk_id)
            
            if include_contextualized:
                # Obtener contexto del padre
                parent = hierarchical_doc.get_parent(chunk.chunk_id)
                if parent:
                    context = self._build_context(chunk, parent)
                    contextualized_texts.append(context)
                    contextualized_ids.append(chunk.chunk_id)
        
        for block in hierarchical_doc.blocks:
            block_texts.append(block.summary)
            block_ids.append(block.block_id)
        
        # 2. Generar embeddings en batches
        chunk_embeds = self._batch_embed(chunk_texts)
        block_embeds = self._batch_embed(block_texts)
        
        contextualized_embeds = []
        if include_contextualized and contextualized_texts:
            contextualized_embeds = self._batch_embed(contextualized_texts)
        
        # 3. Construir resultado
        chunk_embeddings = {}
        for i, chunk_id in enumerate(chunk_ids):
            chunk_emb = ChunkEmbeddings(
                chunk_id=chunk_id,
                chunk_embedding=chunk_embeds[i] if i < len(chunk_embeds) else [],
            )
            
            # Añadir block embedding
            chunk = hierarchical_doc.chunk_index[chunk_id]
            block_idx = next(
                (j for j, bid in enumerate(block_ids) if bid == chunk.block_id),
                None
            )
            if block_idx is not None:
                chunk_emb.block_embedding = block_embeds[block_idx]
            
            # Añadir contextualized embedding
            if include_contextualized and chunk_id in contextualized_ids:
                ctx_idx = contextualized_ids.index(chunk_id)
                if ctx_idx < len(contextualized_embeds):
                    chunk_emb.contextualized_embedding = contextualized_embeds[ctx_idx]
            
            chunk_embeddings[chunk_id] = chunk_emb
        
        block_embeddings = {
            block_id: block_embeds[i]
            for i, block_id in enumerate(block_ids)
            if i < len(block_embeds)
        }
        
        return DocumentEmbeddings(
            source_id=hierarchical_doc.source_id,
            chunk_embeddings=chunk_embeddings,
            block_embeddings=block_embeddings,
            embedding_model=self.model,
            embedding_dim=self.embedding_dim,
        )
    
    def embed_chunks_only(
        self,
        hierarchical_doc,
    ) -> dict[str, list[float]]:
        """
        Genera solo embeddings de chunks (más rápido).
        
        Returns:
            Dict chunk_id → embedding
        """
        texts = [chunk.content for chunk in hierarchical_doc.chunks]
        ids = [chunk.chunk_id for chunk in hierarchical_doc.chunks]
        
        embeddings = self._batch_embed(texts)
        
        return {
            chunk_id: embeddings[i]
            for i, chunk_id in enumerate(ids)
            if i < len(embeddings)
        }
    
    def embed_blocks_only(
        self,
        hierarchical_doc,
    ) -> dict[str, list[float]]:
        """
        Genera solo embeddings de bloques.
        
        Returns:
            Dict block_id → embedding
        """
        texts = [block.summary for block in hierarchical_doc.blocks]
        ids = [block.block_id for block in hierarchical_doc.blocks]
        
        embeddings = self._batch_embed(texts)
        
        return {
            block_id: embeddings[i]
            for i, block_id in enumerate(ids)
            if i < len(embeddings)
        }
    
    def embed_query(self, query: str) -> list[float]:
        """
        Genera embedding para una query de búsqueda.
        
        Args:
            query: Texto de búsqueda
            
        Returns:
            Vector embedding
        """
        return self.embed_text(query)
    
    def embed_queries(self, queries: list[str]) -> list[list[float]]:
        """
        Genera embeddings para múltiples queries.
        
        Args:
            queries: Lista de textos de búsqueda
            
        Returns:
            Lista de vectores embedding
        """
        return self._batch_embed(queries)
    
    def _build_context(self, chunk, block) -> str:
        """
        Construye texto contextualizado para embedding.
        
        Combina:
        - Heading del bloque (si existe)
        - Contenido del chunk
        - Hint de posición
        """
        parts = []
        
        if block.heading:
            parts.append(f"[{block.heading}]")
        
        parts.append(chunk.content)
        
        # Añadir hint de posición si no es único
        if chunk.total_in_block > 1:
            pos = chunk.position_in_block + 1
            total = chunk.total_in_block
            parts.append(f"[Parte {pos}/{total}]")
        
        return " ".join(parts)
    
    def _batch_embed(self, texts: list[str]) -> list[list[float]]:
        """
        Genera embeddings en batches para evitar límites de API.
        """
        if not texts:
            return []
        
        all_embeddings = []
        current_batch = []
        current_tokens = 0
        
        for text in texts:
            estimated_tokens = len(text) // APPROX_CHARS_PER_TOKEN
            
            if current_tokens + estimated_tokens > MAX_TOKENS_PER_BATCH and current_batch:
                # Procesar batch actual
                batch_embeddings = self.embed_texts(current_batch)
                all_embeddings.extend(batch_embeddings)
                current_batch = []
                current_tokens = 0
            
            current_batch.append(text)
            current_tokens += estimated_tokens
        
        # Procesar último batch
        if current_batch:
            batch_embeddings = self.embed_texts(current_batch)
            all_embeddings.extend(batch_embeddings)
        
        return all_embeddings


# =============================================================================
# FUNCIONES DE UTILIDAD
# =============================================================================

def cosine_similarity(vec1: list[float], vec2: list[float]) -> float:
    """Calcula similitud coseno entre dos vectores."""
    a = np.array(vec1)
    b = np.array(vec2)
    
    norm_a = np.linalg.norm(a)
    norm_b = np.linalg.norm(b)
    
    if norm_a == 0 or norm_b == 0:
        return 0.0
    
    return float(np.dot(a, b) / (norm_a * norm_b))


def batch_cosine_similarity(
    query_vec: list[float],
    doc_vecs: list[list[float]],
) -> list[float]:
    """Calcula similitud coseno de una query contra múltiples documentos."""
    query = np.array(query_vec)
    docs = np.array(doc_vecs)
    
    query_norm = np.linalg.norm(query)
    if query_norm == 0:
        return [0.0] * len(doc_vecs)
    
    query_normalized = query / query_norm
    
    doc_norms = np.linalg.norm(docs, axis=1, keepdims=True)
    doc_norms = np.where(doc_norms == 0, 1, doc_norms)  # Evitar división por 0
    docs_normalized = docs / doc_norms
    
    similarities = np.dot(docs_normalized, query_normalized)
    return similarities.tolist()


# =============================================================================
# FUNCIONES DE CONVENIENCIA
# =============================================================================

def embed_hierarchical_document(
    hierarchical_doc,
    model: str = DEFAULT_EMBEDDING_MODEL,
    include_contextualized: bool = True,
) -> DocumentEmbeddings:
    """
    Función de conveniencia para embeddings completos.
    
    Args:
        hierarchical_doc: Documento jerárquico
        model: Modelo de embeddings
        include_contextualized: Incluir embeddings contextualizados
        
    Returns:
        DocumentEmbeddings completo
    """
    embedder = MultiGranularEmbedder(model=model)
    return embedder.embed_document(hierarchical_doc, include_contextualized)


def create_embedder(model: str = DEFAULT_EMBEDDING_MODEL) -> MultiGranularEmbedder:
    """Crea instancia del embedder."""
    return MultiGranularEmbedder(model=model)