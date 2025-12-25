"""
Phase 1 Logic Module V2.1
-------------------------
Arquitectura paralela con RAG para procesamiento de contenido.

Componentes:
- master_planner: Genera el MasterPlan con directivas
- context_indexer: Crea índice vectorial para RAG (reemplaza chunk_persister)
- writer_agent: Redactor con búsqueda RAG
- assembler: Ensambla resultados en productos finales
"""

from core.logic.phase1.master_planner import (
    create_master_plan,
    run_master_planner,
)
from core.logic.phase1.context_indexer import (
    index_content_for_rag,
    search_context,
    cleanup_vector_db,
    ContextIndexer,
    TopicRetriever,
)
from core.logic.phase1.writer_agent import (
    run_writer_agent,
    writer_node,
)
from core.logic.phase1.assembler import (
    run_assembler,
    Assembler,
)

__all__ = [
    # Master Planner
    "create_master_plan",
    "run_master_planner",
    
    # Context Indexer (V2.1 - RAG)
    "index_content_for_rag",
    "search_context",
    "cleanup_vector_db",
    "ContextIndexer",
    "TopicRetriever",
    
    # Writer Agent
    "run_writer_agent",
    "writer_node",
    
    # Assembler
    "run_assembler",
    "Assembler",
]