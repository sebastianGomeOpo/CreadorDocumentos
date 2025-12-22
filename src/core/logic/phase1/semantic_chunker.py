"""
semantic_chunker.py — Cortador Semántico (AI Powered)

Divide el texto y asigna fragmentos a los temas del outline usando LLM.
"""

from __future__ import annotations

import json
from typing import Any, List
from langchain_core.messages import HumanMessage, SystemMessage
from core.state_schema import generate_chunk_id


def chunk_by_structure_heuristic(raw_content: str, ordered_outline: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Chunking básico por headers."""
    chunks = []
    # (Implementación simplificada de fallback que asigna todo al primer tema si falla)
    topic_id = ordered_outline[0]["topic_id"] if ordered_outline else "topic_001"
    
    chunks.append({
        "id": generate_chunk_id(raw_content[:50], topic_id),
        "topic_id": topic_id,
        "content": raw_content,
        "start_position": 0,
        "end_position": len(raw_content),
        "anchor_text": raw_content[:100],
        "word_count": len(raw_content.split())
    })
    return chunks


def semantic_segmentation_llm(raw_content: str, ordered_outline: list[dict[str, Any]], llm: Any) -> list[dict[str, Any]]:
    """
    Segmentación asistida por IA.
    Estrategia: Divide por párrafos grandes y pide al LLM clasificar cada bloque.
    """
    
    # 1. Pre-procesamiento: Dividir en bloques manejables (párrafos)
    paragraphs = [p.strip() for p in raw_content.split('\n\n') if p.strip()]
    
    # Si hay demasiados párrafos, el LLM puede saturarse. 
    # Para producción real, esto debería hacerse en batches.
    # Aquí procesamos los primeros N párrafos para demostración o bloques combinados.
    
    chunks = []
    current_pos = 0
    
    # Crear mapa de temas para el prompt
    topics_desc = "\n".join([f"{item['topic_id']}: {item['topic_name']}" for item in ordered_outline])
    
    system_prompt = f"""Tienes una lista de temas (IDs y Nombres):
{topics_desc}

Tu tarea es analizar el siguiente fragmento de texto y determinar a qué Topic ID pertenece mejor.
Responde SOLO con el Topic ID (ej. topic_003). Si no estás seguro, usa el tema más general o el primero.
"""

    # Procesar cada párrafo (o agruparlos)
    # Nota: Llamar al LLM por cada párrafo es lento. 
    # Optimizamos agrupando o pidiendo clasificación en batch.
    
    # Opción Batch Simple:
    batch_size = 5
    for i in range(0, len(paragraphs), batch_size):
        batch = paragraphs[i:i+batch_size]
        batch_text = "\n\n".join([f"--- BLOQUE {j} ---\n{p}" for j, p in enumerate(batch)])
        
        prompt = f"""Clasifica los siguientes bloques de texto según los temas dados.
        Devuelve un JSON listando el topic_id para cada bloque secuencialmente: ["topic_A", "topic_B", ...]
        
        Texto:
        {batch_text}
        """
        
        try:
            response = llm.invoke([SystemMessage(content=system_prompt), HumanMessage(content=prompt)])
            # Intentar parsear JSON del response content (suponiendo que devuelve array)
            # Limpieza básica de markdown json
            content = response.content.replace("```json", "").replace("```", "").strip()
            if "[" in content:
                assignments = json.loads(content)
            else:
                # Fallback: asignar todo al primer tema si el formato falla
                assignments = [ordered_outline[0]["topic_id"]] * len(batch)
                
        except Exception as e:
            print(f"Error clasificando batch: {e}")
            assignments = [ordered_outline[0]["topic_id"]] * len(batch)
            
        # Crear chunks
        for j, para in enumerate(batch):
            topic_id = assignments[j] if j < len(assignments) else ordered_outline[0]["topic_id"]
            
            # Validar que el topic_id existe, si no, usar el primero
            if not any(t['topic_id'] == topic_id for t in ordered_outline):
                topic_id = ordered_outline[0]["topic_id"]

            chunk_id = generate_chunk_id(para, topic_id)
            chunks.append({
                "id": chunk_id,
                "topic_id": topic_id,
                "content": para,
                "start_position": current_pos,
                "end_position": current_pos + len(para),
                "anchor_text": para[:100],
                "word_count": len(para.split())
            })
            current_pos += len(para) + 2

    return chunks


def semantic_segmentation(raw_content: str, ordered_outline: list[dict[str, Any]], llm: Any | None = None) -> list[dict[str, Any]]:
    """Función principal."""
    if llm and ordered_outline:
        return semantic_segmentation_llm(raw_content, ordered_outline, llm)
    return chunk_by_structure_heuristic(raw_content, ordered_outline)