"""
topic_sorter.py — Organizador Didáctico

Este módulo toma una lista de temas desordenados y propone un
orden lógico (temario) para la clase.

RESPONSABILIDAD:
Crear una estructura `OrderedOutlineItem` donde cada tema tiene
una posición y una justificación.
"""

from __future__ import annotations

from typing import Any


def sort_topics_heuristic(topics: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Ordena los temas basándose en su orden de aparición original.
    Mantiene la jerarquía implícita si existen metadatos de nivel de header.
    """
    ordered_outline = []
    
    for i, topic in enumerate(topics):
        # Generar una justificación básica
        rationale = f"Tema {i+1} en la secuencia original del documento."
        
        # Detectar si es un subtema basado en la metadata interna del scout
        original_level = topic.get("_original_header_level", 2)
        
        # Si quisiéramos hacer anidamiento real, aquí iría la lógica.
        # Para este MVP, aplanamos la estructura pero mantenemos el orden.
        
        ordered_outline.append({
            "position": i + 1,
            "topic_id": topic["id"],
            "topic_name": topic["name"],
            "rationale": rationale,
            "subtopics": [] # Podríamos rellenar esto si el topic es H1 y siguen H2s
        })
        
    return ordered_outline


def create_ordered_outline(topics: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Función principal para generar el temario.
    """
    # En una versión avanzada, aquí un LLM reordenaría por dependencias
    # (ej. "Conceptos Básicos" antes de "Aplicaciones Avanzadas").
    # Por ahora, respetamos el flujo del autor.
    return sort_topics_heuristic(topics)