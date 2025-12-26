"""
context_indexer.py — Indexador de Contexto V3 (RAG Avanzado)

Orquesta el pipeline de indexación jerárquica:
1. HierarchicalChunker: Divide en bloques y chunks
2. MultiGranularEmbedder: Genera embeddings multi-nivel
3. HierarchicalIndex: Almacena en ChromaDB

Este módulo es el punto de entrada para indexar documentos
y proporciona la interfaz simplificada para el grafo.

MIGRACIÓN desde V2.1:
- Reemplaza el chunking por ventana fija
- Ahora preserva estructura jerárquica
- Soporta búsqueda a nivel chunk Y bloque
"""

from __future__ import annotations

import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from dotenv import load_dotenv

load_dotenv()

# Importar componentes de indexing
from core.logic.phase1.indexing.hierarchical_chunker import (
    HierarchicalChunker,
    HierarchicalDocument,
    chunk_document,
)
from core.logic.phase1.indexing.multi_granular_embedder import (
    MultiGranularEmbedder,
    DocumentEmbeddings,
    embed_hierarchical_document,
)
from core.logic.phase1.indexing.hierarchical_index import (
    HierarchicalIndex,
    SearchResult,
    create_index,
)


# =============================================================================
# CONFIGURACIÓN
# =============================================================================

DEFAULT_VECTOR_DB_DIR = Path(os.getenv("DATA_PATH", "./data")) / "temp" / "hierarchical_index"
DEFAULT_CHUNK_SIZE = 800
DEFAULT_CHUNK_OVERLAP = 150
DEFAULT_EMBEDDING_MODEL = "text-embedding-3-small"


# =============================================================================
# CONTEXT INDEXER V3
# =============================================================================

class ContextIndexer:
    """
    Indexador de contexto que coordina el pipeline jerárquico.
    
    Pipeline:
    1. Chunking jerárquico (bloques + chunks)
    2. Embeddings multi-granulares
    3. Indexación en ChromaDB
    
    Uso:
        indexer = ContextIndexer(db_path)
        stats = indexer.index(source_id, text)
        results = indexer.search(source_id, query, k=10)
    """
    
    def __init__(
        self,
        db_path: Path | str = DEFAULT_VECTOR_DB_DIR,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
        chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
        embedding_model: str = DEFAULT_EMBEDDING_MODEL,
    ):
        self.db_path = Path(db_path)
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.embedding_model = embedding_model
        
        # Componentes lazy-loaded
        self._chunker: Optional[HierarchicalChunker] = None
        self._embedder: Optional[MultiGranularEmbedder] = None
        self._index: Optional[HierarchicalIndex] = None
        
        # Cache de documentos indexados
        self._indexed_docs: dict[str, HierarchicalDocument] = {}
    
    @property
    def chunker(self) -> HierarchicalChunker:
        if self._chunker is None:
            self._chunker = HierarchicalChunker(
                chunk_size=self.chunk_size,
                chunk_overlap=self.chunk_overlap,
            )
        return self._chunker
    
    @property
    def embedder(self) -> MultiGranularEmbedder:
        if self._embedder is None:
            self._embedder = MultiGranularEmbedder(
                model=self.embedding_model,
            )
        return self._embedder
    
    def get_index(self, source_id: str) -> HierarchicalIndex:
        """Obtiene o crea índice para una fuente."""
        return HierarchicalIndex(
            base_path=self.db_path,
            source_id=source_id,
        )
    
    def index(
        self,
        source_id: str,
        text: str,
        metadata: Optional[dict] = None,
    ) -> dict[str, Any]:
        """
        Indexa un documento completo.
        
        Args:
            source_id: ID único de la fuente
            text: Contenido del documento
            metadata: Metadata adicional
            
        Returns:
            Estadísticas de indexación
        """
        start_time = datetime.now()
        
        # 1. Chunking jerárquico
        print(f"[ContextIndexer] Chunking documento {source_id}...")
        hierarchical_doc = self.chunker.chunk_document(text, source_id)
        
        # 2. Generar embeddings
        print(f"[ContextIndexer] Generando embeddings para {len(hierarchical_doc.chunks)} chunks...")
        doc_embeddings = self.embedder.embed_document(
            hierarchical_doc,
            include_contextualized=True,
        )
        
        # 3. Indexar en ChromaDB
        print(f"[ContextIndexer] Indexando en ChromaDB...")
        index = self.get_index(source_id)
        index_stats = index.index_document(hierarchical_doc, doc_embeddings)
        
        # 4. Cachear documento para referencias
        self._indexed_docs[source_id] = hierarchical_doc
        
        elapsed = (datetime.now() - start_time).total_seconds()
        
        return {
            "source_id": source_id,
            "blocks_count": len(hierarchical_doc.blocks),
            "chunks_count": len(hierarchical_doc.chunks),
            "chunks_indexed": index_stats.get("chunks_indexed", 0),
            "blocks_indexed": index_stats.get("blocks_indexed", 0),
            "embedding_model": self.embedding_model,
            "elapsed_seconds": elapsed,
            "db_path": str(index.index_path),
        }
    
    def search(
        self,
        source_id: str,
        query: str,
        k: int = 10,
    ) -> list[SearchResult]:
        """
        Busca chunks relevantes.
        
        Args:
            source_id: ID de la fuente
            query: Texto de búsqueda
            k: Número de resultados
            
        Returns:
            Lista de SearchResult
        """
        index = self.get_index(source_id)
        query_embedding = self.embedder.embed_query(query)
        
        return index.search_chunks(
            query_embedding=query_embedding,
            k=k,
            filter_source=source_id,
        )
    
    def search_with_context(
        self,
        source_id: str,
        query: str,
        k: int = 10,
        expand_neighbors: bool = True,
    ) -> list[dict]:
        """
        Busca chunks con contexto estructural.
        
        Args:
            source_id: ID de la fuente
            query: Texto de búsqueda
            k: Número de resultados
            expand_neighbors: Si expandir a vecinos
            
        Returns:
            Lista de dicts con chunk y contexto
        """
        index = self.get_index(source_id)
        query_embedding = self.embedder.embed_query(query)
        
        # Buscar chunks
        results = index.search_chunks(
            query_embedding=query_embedding,
            k=k,
            filter_source=source_id,
        )
        
        enriched = []
        seen_ids = set()
        
        for result in results:
            if result.id in seen_ids:
                continue
            seen_ids.add(result.id)
            
            entry = {
                "chunk_id": result.id,
                "content": result.content,
                "score": result.score,
                "metadata": result.metadata,
            }
            
            # Obtener padre
            parent = index.get_parent_block(result.id)
            if parent:
                entry["parent_heading"] = parent.metadata.get("heading", "")
                entry["parent_summary"] = parent.content[:200] if parent.content else ""
            
            # Obtener vecinos
            if expand_neighbors:
                neighbors = index.get_neighbor_chunks(result.id, window=1)
                entry["neighbors"] = [
                    {"id": n.id, "content": n.content[:100]}
                    for n in neighbors
                    if n.id != result.id
                ]
            
            enriched.append(entry)
        
        return enriched
    
    def get_document(self, source_id: str) -> Optional[HierarchicalDocument]:
        """Obtiene documento jerárquico cacheado."""
        return self._indexed_docs.get(source_id)
    
    def get_index_stats(self, source_id: str) -> dict[str, Any]:
        """Obtiene estadísticas del índice."""
        index = self.get_index(source_id)
        return index.get_stats()
    
    def delete(self, source_id: str) -> dict[str, int]:
        """
        Elimina datos de una fuente.
        
        Returns:
            Estadísticas de eliminación
        """
        index = self.get_index(source_id)
        stats = index.delete_source(source_id)
        
        if source_id in self._indexed_docs:
            del self._indexed_docs[source_id]
        
        return stats
    
    def cleanup(self, source_id: Optional[str] = None) -> None:
        """
        Limpia el índice.
        
        Args:
            source_id: Si se especifica, limpia solo esa fuente.
                       Si es None, limpia todo.
        """
        if source_id:
            index = self.get_index(source_id)
            index.cleanup()
            if source_id in self._indexed_docs:
                del self._indexed_docs[source_id]
        else:
            # Limpiar todo
            if self.db_path.exists():
                shutil.rmtree(self.db_path)
            self._indexed_docs.clear()


# =============================================================================
# TOPIC RETRIEVER (Para el Writer)
# =============================================================================

class TopicRetriever:
    """
    Retriever especializado para temas del MasterPlan.
    
    Usa el pipeline completo:
    1. Facet Query Planner
    2. Multi-Channel Retriever
    3. Fusion Scorer
    4. Coverage Selector
    5. Context Assembler
    """
    
    def __init__(
        self,
        context_indexer: ContextIndexer,
        source_id: str,
    ):
        self.indexer = context_indexer
        self.source_id = source_id
        
        # Importar componentes de retrieval
        from core.logic.phase1.retrieval.facet_query_planner import (
            FacetQueryPlanner,
            get_recommended_k,
        )
        from core.logic.phase1.retrieval.multi_channel_retriever import (
            MultiChannelRetriever,
        )
        from core.logic.phase1.retrieval.fusion_scorer import FusionScorer
        from core.logic.phase1.retrieval.coverage_selector import CoverageSelector
        from core.logic.phase1.retrieval.context_assembler import ContextAssembler
        
        self.planner = FacetQueryPlanner(use_llm_expansion=True)
        self.retriever = MultiChannelRetriever(
            hierarchical_index=self.indexer.get_index(source_id),
            enable_sparse=True,
            enable_parent=True,
        )
        self.scorer = FusionScorer()
        self.selector = CoverageSelector()
        self.assembler = ContextAssembler(
            hierarchical_index=self.indexer.get_index(source_id)
        )
        self.get_recommended_k = get_recommended_k
    
    def retrieve_for_topic(
        self,
        topic_name: str,
        must_include: list[str],
        key_concepts: list[str],
        navigation_context: Optional[dict] = None,
        target_chunks: int = 8,
    ) -> dict[str, Any]:
        """
        Recupera contexto completo para un tema.
        
        Args:
            topic_name: Nombre del tema
            must_include: Conceptos obligatorios
            key_concepts: Conceptos clave
            navigation_context: Contexto de navegación
            target_chunks: Objetivo de chunks
            
        Returns:
            Dict con evidence_pack y métricas
        """
        # 1. Crear plan de queries
        query_plan = self.planner.create_plan(
            topic_name=topic_name,
            must_include=must_include,
            key_concepts=key_concepts,
            navigation_context=navigation_context,
        )
        
        # 2. Ajustar k según complejidad
        recommended = self.get_recommended_k(query_plan.estimated_complexity)
        k_chunks = recommended["chunks"]
        
        # 3. Recuperar candidatos multi-canal
        retrieval_result = self.retriever.retrieve(
            query_plan=query_plan,
            source_id=self.source_id,
            k_per_facet=k_chunks // len(query_plan.facets) + 2,
        )
        
        # 4. Scoring y ranking
        scoring_result = self.scorer.score_candidates(
            candidates=retrieval_result.candidates,
            query_plan=query_plan,
        )
        
        # 5. Selección por cobertura
        coverage_result = self.selector.select(
            scoring_result=scoring_result,
            query_plan=query_plan,
        )
        
        # 6. Ensamblar con contexto
        evidence_pack = self.assembler.assemble(
            coverage_result=coverage_result,
            query_plan=query_plan,
        )
        
        return {
            "evidence_pack": evidence_pack,
            "formatted_context": evidence_pack.formatted_context,
            "coverage": {
                "total_chunks": coverage_result.total_selected,
                "required_coverage": coverage_result.required_coverage_pct,
                "optional_coverage": coverage_result.optional_coverage_pct,
                "missing_required": coverage_result.missing_required,
                "is_complete": coverage_result.is_complete,
            },
            "metrics": {
                "candidates_retrieved": len(retrieval_result.candidates),
                "candidates_scored": len(scoring_result.candidates),
                "diversity_score": coverage_result.diversity_score,
                "coherence_score": coverage_result.coherence_score,
            },
        }


# =============================================================================
# FUNCIONES DE CONVENIENCIA
# =============================================================================

def index_content_for_rag(
    source_id: str,
    text: str,
    db_path: Path | str = DEFAULT_VECTOR_DB_DIR,
) -> dict[str, Any]:
    """
    Función de conveniencia para indexar contenido.
    
    Args:
        source_id: ID de la fuente
        text: Contenido a indexar
        db_path: Ruta del índice
        
    Returns:
        Estadísticas de indexación
    """
    indexer = ContextIndexer(db_path)
    return indexer.index(source_id, text)


def search_context(
    source_id: str,
    query: str,
    db_path: Path | str = DEFAULT_VECTOR_DB_DIR,
    k: int = 10,
) -> list[SearchResult]:
    """
    Función de conveniencia para buscar contexto.
    
    Args:
        source_id: ID de la fuente
        query: Texto de búsqueda
        db_path: Ruta del índice
        k: Número de resultados
        
    Returns:
        Lista de SearchResult
    """
    indexer = ContextIndexer(db_path)
    return indexer.search(source_id, query, k)


def cleanup_vector_db(
    db_path: Path | str = DEFAULT_VECTOR_DB_DIR,
    source_id: Optional[str] = None,
) -> None:
    """
    Función de conveniencia para limpiar el índice.
    
    Args:
        db_path: Ruta del índice
        source_id: Si se especifica, limpia solo esa fuente
    """
    indexer = ContextIndexer(db_path)
    indexer.cleanup(source_id)


def create_topic_retriever(
    source_id: str,
    db_path: Path | str = DEFAULT_VECTOR_DB_DIR,
) -> TopicRetriever:
    """
    Crea un TopicRetriever listo para usar.
    
    Args:
        source_id: ID de la fuente
        db_path: Ruta del índice
        
    Returns:
        TopicRetriever configurado
    """
    indexer = ContextIndexer(db_path)
    return TopicRetriever(indexer, source_id)