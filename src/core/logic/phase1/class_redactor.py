"""
class_redactor.py — Redactor de Clase (AI Powered)

Ensambla y redacta el contenido final de la clase utilizando LLM para
suavizar transiciones y mejorar la coherencia.
"""

from __future__ import annotations

from typing import Any
from langchain_core.messages import HumanMessage, SystemMessage


def redact_topic_content_llm(topic_name: str, chunks: list[dict], llm: Any) -> str:
    """Redacta el contenido de un solo tema usando los chunks disponibles."""
    
    if not chunks:
        return "_No hay contenido disponible para este tema._"
    
    # Combinar contenido de los chunks
    raw_text = "\n\n".join([c["content"] for c in chunks])
    
    prompt = f"""Eres un redactor técnico experto.
    Tu tarea es escribir la sección "{topic_name}" de una clase educativa.
    
    Usa la siguiente información cruda (Chunks):
    {raw_text[:8000]} # Limite de contexto
    
    Instrucciones:
    1. Escribe en formato Markdown limpio.
    2. Mantén un tono educativo y claro.
    3. Integra los fragmentos en una narrativa coherente.
    4. NO inventes información, usa solo lo provisto en los chunks.
    """
    
    try:
        response = llm.invoke([HumanMessage(content=prompt)])
        return response.content
    except Exception as e:
        print(f"Error redactando tema {topic_name}: {e}")
        return raw_text # Fallback al texto crudo


def generate_ordered_class(
    ordered_outline: list[dict[str, Any]], 
    chunks: list[dict[str, Any]],
    llm: Any | None = None
) -> dict[str, Any]:
    """Función principal de redacción."""
    
    markdown_lines = []
    warnings = []
    
    markdown_lines.append("# Clase Generada (AI Powered)")
    markdown_lines.append(f"> Estructurada automáticamente por ZK Foundry.")
    markdown_lines.append("---")
    markdown_lines.append("")

    # Agrupar chunks
    chunks_by_topic = {}
    for chunk in chunks:
        t_id = chunk["topic_id"]
        if t_id not in chunks_by_topic:
            chunks_by_topic[t_id] = []
        chunks_by_topic[t_id].append(chunk)

    # Iterar temario
    for item in ordered_outline:
        topic_id = item["topic_id"]
        topic_name = item["topic_name"]
        
        markdown_lines.append(f"## {item['position']}. {topic_name}")
        markdown_lines.append(f"<!-- Rationale: {item['rationale']} -->")
        markdown_lines.append("")
        
        topic_chunks = chunks_by_topic.get(topic_id, [])
        
        if not topic_chunks:
            markdown_lines.append("*[No se detectó contenido específico para esta sección]*")
            warnings.append({
                "type": "gap",
                "description": f"Tema vacío: {topic_name}",
                "location": topic_id
            })
        else:
            if llm:
                # Redacción inteligente
                content = redact_topic_content_llm(topic_name, topic_chunks, llm)
                markdown_lines.append(content)
            else:
                # Redacción simple (concatenación)
                for chunk in topic_chunks:
                    markdown_lines.append(chunk["content"])
        
        markdown_lines.append("")
        markdown_lines.append("---")
        markdown_lines.append("")

    return {
        "ordered_class_markdown": "\n".join(markdown_lines),
        "warnings": warnings
    }