"""
Phase 1 Logic Module
--------------------
Componentes para la ingesta, análisis y estructuración de textos crudos.
"""

from core.logic.phase1.topic_scout import scan_for_topics
from core.logic.phase1.topic_sorter import create_ordered_outline
from core.logic.phase1.semantic_chunker import semantic_segmentation
from core.logic.phase1.class_redactor import generate_ordered_class

__all__ = [
    "scan_for_topics",
    "create_ordered_outline",
    "semantic_segmentation",
    "generate_ordered_class",
]