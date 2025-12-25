"""
Phase 1 Logic Module V2
-----------------------
Arquitectura paralela para procesamiento de contenido.

Componentes:
- master_planner: Genera el MasterPlan con directivas
- chunk_persister: Persiste chunks a disco
- writer_agent: Redactor aislado con contexto mínimo
- assembler: Ensambla resultados en productos finales
"""

from core.logic.phase1.master_planner import (
    create_master_plan,
    run_master_planner,
)
from core.logic.phase1.chunk_persister import (
    persist_chunks_to_disk,
    read_chunk_from_disk,
    cleanup_temp_chunks,
    ChunkPersister,
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
    
    # Chunk Persister
    "persist_chunks_to_disk",
    "read_chunk_from_disk",
    "cleanup_temp_chunks",
    "ChunkPersister",
    
    # Writer Agent
    "run_writer_agent",
    "writer_node",
    
    # Assembler
    "run_assembler",
    "Assembler",
]