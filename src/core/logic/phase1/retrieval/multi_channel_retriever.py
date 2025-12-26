"""
multi_channel_retriever.py — Retriever Multi-Canal

Genera candidatos desde múltiples canales:

1. DENSE (embeddings): Captura semántica, paráfrasis
2. SPARSE (BM25): Captura términos exactos, técnicos
3. PARENT: Busca en bloques para contexto amplio

Un Router decide los pesos de cada canal según:
- Tipo de faceta
- Tipo de contenido (transcripción vs documento)

La fusión produce candidatos diversos que luego
se refinan en el scorer y selector.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Optional
from enum import Enum

from dotenv import load_dotenv

load_dotenv()


# =============================================================================
# ESTRUCTURAS DE DATOS
# =============================================================================

@dataclass
class RetrievalCandidate:
    """Un candidato recuperado con scores por canal."""
    chunk_id: str
    content: str
    metadata: dict
    
    # Scores por canal
    dense_score: float = 0.0
    sparse_score: float = 0.0
    parent_score: float = 0.0
    
    # Score combinado (calculado por router)
    combined_score: float = 0.0
    
    # Fuente
    source_channel: str = ""  # "dense", "sparse", "parent"
    
    # Faceta que lo encontró
    facet_id: Optional[str] = None
    facet_name: Optional[str] = None
    
    def __hash__(self):
        return hash(self.chunk_id)
    
    def __eq__(self, other):
        if isinstance(other, RetrievalCandidate):
            return self.chunk_id == other.chunk_id
        return False


@dataclass
class RetrievalResult:
    """Resultado completo de retrieval multi-canal."""
    candidates: list[RetrievalCandidate]
    facet_coverage: dict[str, list[str]]  # facet_id → chunk_ids
    total_searched: int
    channels_used: list[str]
    
    @property
    def unique_chunks(self) -> int:
        return len(set(c.chunk_id for c in self.candidates))


class ChannelWeight(Enum):
    """Configuraciones de peso por tipo de búsqueda."""
    BALANCED = {"dense": 0.5, "sparse": 0.3, "parent": 0.2}
    SEMANTIC_HEAVY = {"dense": 0.7, "sparse": 0.15, "parent": 0.15}
    KEYWORD_HEAVY = {"dense": 0.3, "sparse": 0.5, "parent": 0.2}
    CONTEXT_HEAVY = {"dense": 0.4, "sparse": 0.2, "parent": 0.4}


# =============================================================================
# SPARSE RETRIEVER (BM25)
# =============================================================================

class SparseRetriever:
    """
    Retriever basado en BM25 para términos exactos.
    
    Útil para:
    - Términos técnicos
    - Nombres propios
    - Acrónimos
    """
    
    def __init__(self):
        self._index = None
        self._documents = []
        self._chunk_ids = []
        self._metadatas = []
    
    def build_index(
        self,
        chunks: list[dict],
    ) -> None:
        """
        Construye índice BM25 desde chunks.
        
        Args:
            chunks: Lista de {chunk_id, content, metadata}
        """
        try:
            from rank_bm25 import BM25Okapi
        except ImportError:
            raise ImportError("rank_bm25 no instalado. Ejecuta: pip install rank-bm25")
        
        self._documents = []
        self._chunk_ids = []
        self._metadatas = []
        
        for chunk in chunks:
            # Tokenizar (simple split)
            tokens = self._tokenize(chunk["content"])
            self._documents.append(tokens)
            self._chunk_ids.append(chunk["chunk_id"])
            self._metadatas.append(chunk.get("metadata", {}))
        
        if self._documents:
            self._index = BM25Okapi(self._documents)
    
    def search(
        self,
        query: str,
        k: int = 10,
    ) -> list[tuple[str, float, str, dict]]:
        """
        Busca por BM25.
        
        Returns:
            Lista de (chunk_id, score, content, metadata)
        """
        if not self._index:
            return []
        
        tokens = self._tokenize(query)
        scores = self._index.get_scores(tokens)
        
        # Ordenar por score
        indexed_scores = list(enumerate(scores))
        indexed_scores.sort(key=lambda x: x[1], reverse=True)
        
        results = []
        for idx, score in indexed_scores[:k]:
            if score > 0:
                # Reconstruir contenido
                content = " ".join(self._documents[idx])
                results.append((
                    self._chunk_ids[idx],
                    score,
                    content,
                    self._metadatas[idx],
                ))
        
        return results
    
    def _tokenize(self, text: str) -> list[str]:
        """Tokenización simple para BM25."""
        import re
        # Lowercase y split por no-alfanuméricos
        text = text.lower()
        tokens = re.findall(r'\b\w+\b', text)
        return tokens


# =============================================================================
# CHANNEL ROUTER
# =============================================================================

class ChannelRouter:
    """
    Decide pesos de canales según contexto.
    
    Factores:
    - Tipo de faceta (topic, must_include, etc.)
    - Complejidad estimada
    - Tipo de contenido
    """
    
    def __init__(self):
        # Pesos por tipo de faceta
        self.facet_weights = {
            "topic": ChannelWeight.SEMANTIC_HEAVY.value,
            "must_include": ChannelWeight.BALANCED.value,
            "key_concept": ChannelWeight.SEMANTIC_HEAVY.value,
            "navigation": ChannelWeight.CONTEXT_HEAVY.value,
            "expansion": ChannelWeight.BALANCED.value,
        }
    
    def get_weights(
        self,
        facet_type: str,
        complexity: str = "medium",
    ) -> dict[str, float]:
        """
        Obtiene pesos para los canales.
        
        Args:
            facet_type: Tipo de faceta
            complexity: Complejidad estimada
            
        Returns:
            Dict con pesos por canal
        """
        base_weights = self.facet_weights.get(
            facet_type,
            ChannelWeight.BALANCED.value
        )
        
        # Ajustar por complejidad
        if complexity == "high":
            # Más peso a parent para contexto
            return {
                "dense": base_weights["dense"] * 0.9,
                "sparse": base_weights["sparse"],
                "parent": base_weights["parent"] * 1.2,
            }
        elif complexity == "low":
            # Más peso a dense para precisión
            return {
                "dense": base_weights["dense"] * 1.1,
                "sparse": base_weights["sparse"] * 0.9,
                "parent": base_weights["parent"] * 0.8,
            }
        
        return base_weights
    
    def combine_scores(
        self,
        dense_score: float,
        sparse_score: float,
        parent_score: float,
        weights: dict[str, float],
    ) -> float:
        """Combina scores de múltiples canales."""
        # Normalizar pesos
        total = sum(weights.values())
        if total == 0:
            return 0.0
        
        normalized = {k: v / total for k, v in weights.items()}
        
        combined = (
            dense_score * normalized["dense"] +
            sparse_score * normalized["sparse"] +
            parent_score * normalized["parent"]
        )
        
        return combined


# =============================================================================
# MULTI-CHANNEL RETRIEVER
# =============================================================================

class MultiChannelRetriever:
    """
    Retriever que combina múltiples canales de búsqueda.
    
    Canales:
    1. Dense (ChromaDB): Similitud semántica
    2. Sparse (BM25): Términos exactos
    3. Parent (ChromaDB blocks): Contexto amplio
    
    El router decide pesos según tipo de faceta y complejidad.
    """
    
    def __init__(
        self,
        hierarchical_index,  # HierarchicalIndex
        enable_sparse: bool = True,
        enable_parent: bool = True,
    ):
        self.index = hierarchical_index
        self.enable_sparse = enable_sparse
        self.enable_parent = enable_parent
        
        self.router = ChannelRouter()
        self._sparse_retriever = None
        self._sparse_built = False
    
    @property
    def sparse_retriever(self) -> SparseRetriever:
        """Lazy init de sparse retriever."""
        if self._sparse_retriever is None:
            self._sparse_retriever = SparseRetriever()
        return self._sparse_retriever
    
    def build_sparse_index(self, source_id: str) -> None:
        """
        Construye índice BM25 desde los chunks indexados.
        
        Args:
            source_id: ID de la fuente
        """
        if not self.enable_sparse:
            return
        
        # Obtener todos los chunks del índice
        # Nota: ChromaDB no tiene "get all", usamos una búsqueda amplia
        # con un vector dummy o iteramos por IDs conocidos
        try:
            all_chunks = self.index.chunks_collection.get(
                where={"source_id": source_id},
                include=["documents", "metadatas"],
            )
            
            chunks = []
            for i, chunk_id in enumerate(all_chunks["ids"]):
                chunks.append({
                    "chunk_id": chunk_id,
                    "content": all_chunks["documents"][i] if all_chunks["documents"] else "",
                    "metadata": all_chunks["metadatas"][i] if all_chunks["metadatas"] else {},
                })
            
            self.sparse_retriever.build_index(chunks)
            self._sparse_built = True
            
        except Exception as e:
            print(f"Warning: Could not build sparse index: {e}")
    
    def retrieve(
        self,
        query_plan,  # QueryPlan
        source_id: str,
        k_per_facet: int = 10,
    ) -> RetrievalResult:
        """
        Ejecuta retrieval multi-canal para todas las facetas.
        
        Args:
            query_plan: Plan con facetas embedidas
            source_id: ID de la fuente
            k_per_facet: Candidatos por faceta
            
        Returns:
            RetrievalResult con candidatos fusionados
        """
        # Construir sparse index si no existe
        if self.enable_sparse and not self._sparse_built:
            self.build_sparse_index(source_id)
        
        all_candidates: dict[str, RetrievalCandidate] = {}
        facet_coverage: dict[str, list[str]] = {}
        channels_used = set()
        total_searched = 0
        
        complexity = query_plan.estimated_complexity
        
        # Procesar cada faceta
        for facet in query_plan.facets:
            facet_candidates = []
            facet_coverage[facet.facet_id] = []
            
            # Obtener pesos para esta faceta
            weights = self.router.get_weights(
                facet.facet_type.value,
                complexity,
            )
            
            # 1. Dense retrieval
            if facet.query_embedding:
                dense_results = self.index.search_chunks(
                    query_embedding=facet.query_embedding,
                    k=k_per_facet,
                    filter_source=source_id,
                )
                channels_used.add("dense")
                total_searched += len(dense_results)
                
                for result in dense_results:
                    candidate = self._get_or_create_candidate(
                        all_candidates,
                        result.id,
                        result.content,
                        result.metadata,
                    )
                    candidate.dense_score = max(candidate.dense_score, result.score)
                    candidate.facet_id = facet.facet_id
                    candidate.facet_name = facet.name
                    facet_candidates.append(candidate)
            
            # 2. Sparse retrieval (BM25)
            if self.enable_sparse and self._sparse_built:
                sparse_results = self.sparse_retriever.search(
                    query=facet.query_text,
                    k=k_per_facet // 2,
                )
                channels_used.add("sparse")
                total_searched += len(sparse_results)
                
                # Normalizar scores BM25 (pueden ser muy altos)
                max_sparse = max((r[1] for r in sparse_results), default=1.0)
                
                for chunk_id, score, content, metadata in sparse_results:
                    candidate = self._get_or_create_candidate(
                        all_candidates,
                        chunk_id,
                        content,
                        metadata,
                    )
                    normalized_score = score / max_sparse if max_sparse > 0 else 0
                    candidate.sparse_score = max(candidate.sparse_score, normalized_score)
                    if not candidate.facet_id:
                        candidate.facet_id = facet.facet_id
                        candidate.facet_name = facet.name
                    facet_candidates.append(candidate)
            
            # 3. Parent retrieval (búsqueda en bloques)
            if self.enable_parent and facet.query_embedding:
                parent_results = self.index.search_blocks(
                    query_embedding=facet.query_embedding,
                    k=3,
                    filter_source=source_id,
                )
                channels_used.add("parent")
                
                for block_result in parent_results:
                    # Obtener chunks de este bloque
                    block_chunks = self.index.get_block_chunks(block_result.id)
                    total_searched += len(block_chunks)
                    
                    for chunk_result in block_chunks:
                        candidate = self._get_or_create_candidate(
                            all_candidates,
                            chunk_result.id,
                            chunk_result.content,
                            chunk_result.metadata,
                        )
                        # Score del bloque propagado a sus chunks
                        candidate.parent_score = max(
                            candidate.parent_score,
                            block_result.score * 0.8  # Pequeño descuento
                        )
                        if not candidate.facet_id:
                            candidate.facet_id = facet.facet_id
                            candidate.facet_name = facet.name
                        facet_candidates.append(candidate)
            
            # Calcular score combinado para candidatos de esta faceta
            for candidate in facet_candidates:
                candidate.combined_score = self.router.combine_scores(
                    candidate.dense_score,
                    candidate.sparse_score,
                    candidate.parent_score,
                    weights,
                )
                facet_coverage[facet.facet_id].append(candidate.chunk_id)
        
        # Convertir a lista y ordenar por score combinado
        candidates_list = list(all_candidates.values())
        candidates_list.sort(key=lambda c: c.combined_score, reverse=True)
        
        return RetrievalResult(
            candidates=candidates_list,
            facet_coverage=facet_coverage,
            total_searched=total_searched,
            channels_used=list(channels_used),
        )
    
    def retrieve_for_single_query(
        self,
        query_embedding: list[float],
        query_text: str,
        source_id: str,
        k: int = 15,
    ) -> list[RetrievalCandidate]:
        """
        Retrieval simplificado para una sola query.
        
        Args:
            query_embedding: Vector de búsqueda
            query_text: Texto para BM25
            source_id: ID de la fuente
            k: Número de resultados
            
        Returns:
            Lista de candidatos ordenados
        """
        candidates: dict[str, RetrievalCandidate] = {}
        weights = ChannelWeight.BALANCED.value
        
        # Dense
        dense_results = self.index.search_chunks(
            query_embedding=query_embedding,
            k=k,
            filter_source=source_id,
        )
        
        for result in dense_results:
            candidate = self._get_or_create_candidate(
                candidates,
                result.id,
                result.content,
                result.metadata,
            )
            candidate.dense_score = result.score
        
        # Sparse
        if self.enable_sparse:
            if not self._sparse_built:
                self.build_sparse_index(source_id)
            
            sparse_results = self.sparse_retriever.search(query_text, k // 2)
            max_sparse = max((r[1] for r in sparse_results), default=1.0)
            
            for chunk_id, score, content, metadata in sparse_results:
                candidate = self._get_or_create_candidate(
                    candidates,
                    chunk_id,
                    content,
                    metadata,
                )
                candidate.sparse_score = score / max_sparse if max_sparse > 0 else 0
        
        # Combinar scores
        for candidate in candidates.values():
            candidate.combined_score = self.router.combine_scores(
                candidate.dense_score,
                candidate.sparse_score,
                candidate.parent_score,
                weights,
            )
        
        # Ordenar y retornar
        results = list(candidates.values())
        results.sort(key=lambda c: c.combined_score, reverse=True)
        return results[:k]
    
    def _get_or_create_candidate(
        self,
        candidates: dict[str, RetrievalCandidate],
        chunk_id: str,
        content: str,
        metadata: dict,
    ) -> RetrievalCandidate:
        """Obtiene o crea un candidato."""
        if chunk_id not in candidates:
            candidates[chunk_id] = RetrievalCandidate(
                chunk_id=chunk_id,
                content=content,
                metadata=metadata,
            )
        return candidates[chunk_id]


# =============================================================================
# FUNCIONES DE CONVENIENCIA
# =============================================================================

def create_retriever(
    hierarchical_index,
    enable_sparse: bool = True,
    enable_parent: bool = True,
) -> MultiChannelRetriever:
    """Crea instancia del retriever multi-canal."""
    return MultiChannelRetriever(
        hierarchical_index,
        enable_sparse=enable_sparse,
        enable_parent=enable_parent,
    )