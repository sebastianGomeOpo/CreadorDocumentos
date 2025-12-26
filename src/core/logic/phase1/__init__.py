"""
Phase 1 Logic Module — RAG Avanzado V3
--------------------------------------
Arquitectura paralela con RAG jerárquico para procesamiento de contenido.

Componentes:
- master_planner: Genera MasterPlan con directivas
- context_indexer: Orquesta indexación jerárquica
- writer_agent: Redacta secciones con Evidence Pack
- assembler: Ensambla documento final

Submodulos:
- indexing/: Chunking jerárquico y embeddings multi-nivel
- retrieval/: Pipeline de retrieval multi-canal
"""

# Master Planner
from core.logic.phase1.master_planner import (
    create_master_plan,
)

# Context Indexer (V3 - RAG Jerárquico)
from core.logic.phase1.context_indexer import (
    ContextIndexer,
    TopicRetriever,
    index_content_for_rag,
    search_context,
    cleanup_vector_db,
    create_topic_retriever,
)

# Writer Agent (V3)
from core.logic.phase1.writer_agent import (
    WriterAgent,
    WriterResult,
    run_writer_agent,
    create_writer,
    write_single_section,
)

# Assembler
from core.logic.phase1.assembler import (
    run_assembler,
)

# Legacy: Chunk Persister (mantener para compatibilidad)
try:
    from core.logic.phase1.chunk_persister import (
        persist_chunks_to_disk,
        read_chunk_from_disk,
        cleanup_temp_chunks,
        ChunkPersister,
    )
    _HAS_CHUNK_PERSISTER = True
except ImportError:
    _HAS_CHUNK_PERSISTER = False

__all__ = [
    # Master Planner
    "create_master_plan",
    
    # Context Indexer (V3)
    "ContextIndexer",
    "TopicRetriever",
    "index_content_for_rag",
    "search_context",
    "cleanup_vector_db",
    "create_topic_retriever",
    
    # Writer Agent (V3)
    "WriterAgent",
    "WriterResult",
    "run_writer_agent",
    "create_writer",
    "write_single_section",
    
    # Assembler
    "run_assembler",
]

# Añadir exports de legacy si están disponibles
if _HAS_CHUNK_PERSISTER:
    __all__.extend([
        "persist_chunks_to_disk",
        "read_chunk_from_disk",
        "cleanup_temp_chunks",
        "ChunkPersister",
    ])