"""
Phase 2 Logic Module
--------------------
Componentes para la atomización, generación y validación de conocimiento.
"""

from core.logic.phase2.atomic_planner import create_atomic_plan
from core.logic.phase2.atomic_generator import generate_atomic_notes
from core.logic.phase2.epistemic_validator import run_epistemic_validation
from core.logic.phase2.graph_rag_builder import (
    build_rag_context, 
    integrate_approved_bundle
)
from core.logic.phase2.vector_indexer import (
    index_lesson_chunks,
    index_approved_notes,
    search_similar_notes,
    check_for_duplicates
)

__all__ = [
    "create_atomic_plan",
    "generate_atomic_notes",
    "run_epistemic_validation",
    "build_rag_context",
    "integrate_approved_bundle",
    "index_lesson_chunks",
    "index_approved_notes",
    "search_similar_notes",
    "check_for_duplicates",
]