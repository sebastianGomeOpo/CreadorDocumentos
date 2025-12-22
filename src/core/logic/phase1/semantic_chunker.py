"""
semantic_chunker.py — Cortador Semántico

Este módulo divide el texto crudo en fragmentos (chunks) y los asocia
con los temas identificados en el temario ordenado.

RESPONSABILIDAD:
- Dividir texto preservando contexto.
- Asociar cada fragmento al `topic_id` correcto.
- Calcular metadatos (posición, palabras).
"""

from __future__ import annotations

import re
from typing import Any
from core.state_schema import generate_chunk_id


def chunk_by_structure(raw_content: str, ordered_outline: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Corta el texto usando los nombres de los temas como separadores.
    Esto es mucho más preciso que cortar por párrafos arbitrarios para clases estructuradas.
    """
    chunks = []
    
    # 1. Crear un mapa de nombres de temas a IDs
    # Normalizamos a lowercase para búsqueda
    topic_map = {item["topic_name"].lower(): item["topic_id"] for item in ordered_outline}
    
    # 2. Si no hay outline, todo es un solo chunk
    if not ordered_outline:
        chunk_id = generate_chunk_id(raw_content[:50], "topic_001")
        return [{
            "id": chunk_id,
            "topic_id": "topic_001",
            "content": raw_content,
            "start_position": 0,
            "end_position": len(raw_content),
            "anchor_text": raw_content[:100],
            "word_count": len(raw_content.split())
        }]

    # 3. Estrategia de Splitting por Headers
    # Buscamos las líneas que coinciden con los nombres de los topics
    lines = raw_content.split('\n')
    current_topic_id = ordered_outline[0]["topic_id"] # Default al primero
    current_buffer = []
    current_start_pos = 0
    pointer = 0
    
    for line in lines:
        line_clean = line.strip().lstrip('#').strip().lower()
        
        # ¿Es esta línea un header de un tema conocido?
        is_header = False
        found_topic_id = None
        
        # Verificación exacta o muy cercana
        if line.strip().startswith('#') and line_clean in topic_map:
            is_header = True
            found_topic_id = topic_map[line_clean]
        
        if is_header and found_topic_id:
            # Guardar el chunk anterior si existe
            if current_buffer:
                content = "\n".join(current_buffer).strip()
                if content:
                    chunks.append(_create_chunk(content, current_topic_id, current_start_pos))
            
            # Iniciar nuevo chunk
            current_topic_id = found_topic_id
            current_buffer = [] # No incluimos el header en el contenido del chunk para evitar ruido, o sí?
            # Incluyamos el header para contexto
            current_buffer.append(line)
            current_start_pos = pointer
        else:
            current_buffer.append(line)
            
        pointer += len(line) + 1 # +1 por el salto de línea

    # Guardar el último chunk
    if current_buffer:
        content = "\n".join(current_buffer).strip()
        if content:
            chunks.append(_create_chunk(content, current_topic_id, current_start_pos))

    return chunks


def _create_chunk(content: str, topic_id: str, start_pos: int) -> dict[str, Any]:
    """Helper para crear el diccionario del chunk con ID determinístico."""
    chunk_id = generate_chunk_id(content, topic_id)
    return {
        "id": chunk_id,
        "topic_id": topic_id,
        "content": content,
        "start_position": start_pos,
        "end_position": start_pos + len(content),
        "anchor_text": content[:100].replace('\n', ' '),
        "word_count": len(content.split())
    }


def semantic_segmentation(raw_content: str, ordered_outline: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Función principal de chunking."""
    # Usamos la estrategia estructural que es robusta para documentos Markdown
    return chunk_by_structure(raw_content, ordered_outline)