"""
topic_scout.py — Detector de Temas

Este módulo analiza el texto crudo para identificar los temas principales.
Soporta dos modos:
1. Heurístico (Regex): Extrae headers Markdown (#, ##, ###)
2. LLM: Analiza el contenido semántico (pendiente de implementación completa)

RESPONSABILIDAD:
Convertir texto sin estructura en una lista de candidatos a temas (Topic objects).
"""

from __future__ import annotations

import re
from typing import Any

from core.state_schema import Topic


def scout_topics_heuristic(raw_content: str) -> list[dict[str, Any]]:
    """
    Extrae temas basándose en la estructura Markdown del documento.
    Asume que los headers (#, ##) representan los temas principales.
    """
    topics = []
    lines = raw_content.split('\n')
    
    count = 0
    current_topic = None
    
    # Si no hay headers, creamos un tema general
    if not any(line.strip().startswith('#') for line in lines):
        return [{
            "id": "topic_001",
            "name": "Contenido General",
            "description": "Tema principal detectado automáticamente",
            "keywords": ["general"],
            "estimated_complexity": "intermediate",
            "prerequisites": []
        }]

    for line in lines:
        line = line.strip()
        # Detectar headers H1, H2, H3
        if line.startswith('#'):
            # Limpiar header (quitar # y espacios)
            header_level = len(line.split()[0])
            name = line.lstrip('#').strip()
            
            # Solo procesar si tiene texto
            if not name:
                continue
                
            count += 1
            topic_id = f"topic_{count:03d}"
            
            # Determinar complejidad por nivel de header (heurística simple)
            complexity = "basic" if header_level == 1 else "intermediate"
            if header_level >= 3:
                complexity = "advanced"
            
            topics.append({
                "id": topic_id,
                "name": name,
                "description": f"Sección extraída: {name}",
                "keywords": [w.lower() for w in name.split() if len(w) > 3],
                "estimated_complexity": complexity,
                "prerequisites": [],
                "_original_header_level": header_level # Metadata interna útil para el sorter
            })

    return topics


async def scout_topics_llm(raw_content: str, llm: Any) -> list[dict[str, Any]]:
    """
    (Placeholder) Extrae temas usando un LLM para textos sin formato Markdown.
    """
    # TODO: Implementar extracción semántica real
    return scout_topics_heuristic(raw_content)


def scan_for_topics(raw_content: str, llm: Any | None = None) -> list[dict[str, Any]]:
    """
    Función principal de entrada.
    """
    if llm:
        # En el futuro, usaríamos await aquí o envolveríamos en runner asíncrono
        # Por ahora, fallback a heurístico para el MVP estático
        return scout_topics_heuristic(raw_content)
    
    return scout_topics_heuristic(raw_content)