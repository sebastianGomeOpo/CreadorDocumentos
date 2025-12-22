"""
class_redactor.py — Redactor de Clase

Este módulo ensambla la "Clase Ordenada" final combinando el temario
con los chunks de contenido correspondientes.

RESPONSABILIDAD:
Generar un documento Markdown limpio, legible y estructurado que sirva
como fuente de verdad para la Fase 2.
"""

from __future__ import annotations

from typing import Any


def redact_class_simple(
    ordered_outline: list[dict[str, Any]], 
    chunks: list[dict[str, Any]]
) -> tuple[str, list[dict[str, Any]]]:
    """
    Ensambla la clase concatenando chunks bajo sus respectivos temas.
    
    Returns:
        tuple: (Markdown final, Lista de advertencias)
    """
    markdown_lines = []
    warnings = []
    
    markdown_lines.append("# Clase Ordenada y Procesada")
    markdown_lines.append(f"> Generado automáticamente por ZK Foundry Static | Temas: {len(ordered_outline)}")
    markdown_lines.append("")
    markdown_lines.append("---")
    markdown_lines.append("")

    # Indexar chunks por topic_id para acceso rápido
    chunks_by_topic = {}
    for chunk in chunks:
        t_id = chunk["topic_id"]
        if t_id not in chunks_by_topic:
            chunks_by_topic[t_id] = []
        chunks_by_topic[t_id].append(chunk)

    # Iterar sobre el temario
    for item in ordered_outline:
        topic_id = item["topic_id"]
        topic_name = item["topic_name"]
        
        # Escribir Header
        markdown_lines.append(f"## {item['position']}. {topic_name}")
        if item.get("rationale"):
            markdown_lines.append(f"<!-- Rationale: {item['rationale']} -->")
        markdown_lines.append("")
        
        # Obtener y escribir chunks
        topic_chunks = chunks_by_topic.get(topic_id, [])
        
        if not topic_chunks:
            markdown_lines.append(f"*⚠️ [Warning: No se detectó contenido explícito para este tema en el texto original]*")
            markdown_lines.append("")
            warnings.append({
                "type": "gap",
                "description": f"Tema vacío: {topic_name}",
                "location": topic_id,
                "severity": "medium"
            })
        else:
            for chunk in topic_chunks:
                # Limpieza básica: si el chunk empieza con el mismo header que acabamos de escribir, lo quitamos
                content = chunk["content"]
                lines = content.split('\n')
                if lines and lines[0].strip().lstrip('#').strip().lower() == topic_name.lower():
                    content = "\n".join(lines[1:]).strip()
                
                if content:
                    markdown_lines.append(content)
                    markdown_lines.append("")
                    # Añadir ancla oculta para trazabilidad futura
                    markdown_lines.append(f"<!-- src_chunk: {chunk['id']} -->")
                    markdown_lines.append("")

    return "\n".join(markdown_lines), warnings


def generate_ordered_class(
    ordered_outline: list[dict[str, Any]], 
    chunks: list[dict[str, Any]]
) -> dict[str, Any]:
    """
    Función principal de redacción.
    """
    content, warnings = redact_class_simple(ordered_outline, chunks)
    
    return {
        "ordered_class_markdown": content,
        "warnings": warnings
    }