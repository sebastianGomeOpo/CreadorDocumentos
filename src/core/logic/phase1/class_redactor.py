"""
class_redactor.py — Limpiador de contenido (AI Activated)

Toma texto crudo y lo convierte en Markdown estructurado usando LLM.
"""
from __future__ import annotations

from typing import Any, List
from langchain_core.messages import SystemMessage, HumanMessage
from langchain_core.language_models import BaseChatModel


def generate_ordered_class(
    ordered_outline: List[dict[str, Any]], 
    semantic_chunks: List[dict[str, Any]], 
    llm: BaseChatModel = None
) -> dict[str, Any]:
    """
    Genera un documento Markdown coherente a partir de un outline y chunks.
    
    Args:
        ordered_outline: Lista de temas ordenados (del topic_sorter)
        semantic_chunks: Lista de fragmentos de contenido (del semantic_chunker)
        llm: Modelo de lenguaje (opcional)
        
    Returns:
        Dict con 'ordered_class_markdown' y 'warnings'
    """
    # Validación básica
    if not ordered_outline:
        return {
            "ordered_class_markdown": "", 
            "warnings": [{"type": "gap", "description": "Outline vacío, no se pudo generar clase.", "severity": "high"}]
        }

    # 1. Agrupar chunks por topic_id para acceso rápido
    chunks_by_topic = {}
    for chunk in semantic_chunks:
        t_id = chunk.get("topic_id")
        if t_id:
            if t_id not in chunks_by_topic:
                chunks_by_topic[t_id] = []
            chunks_by_topic[t_id].append(chunk.get("content", ""))

    full_document_parts = []
    warnings = []

    # 2. Iterar sobre el outline para construir el documento en orden
    for item in ordered_outline:
        topic_id = item.get("topic_id")
        topic_name = item.get("topic_name", "Sin Título")
        
        # Obtener contenido crudo concatenado para este tema
        raw_topic_content = "\n\n".join(chunks_by_topic.get(topic_id, []))
        
        if not raw_topic_content.strip():
            warnings.append({
                "type": "gap", 
                "description": f"El tema '{topic_name}' no tiene contenido asociado (chunks vacíos).", 
                "severity": "medium"
            })
            # Aun así agregamos el título para mantener estructura
            full_document_parts.append(f"## {topic_name}\n\n*(Sin contenido detectado)*")
            continue

        # 3. Generar redacción para esta sección
        # Si hay LLM, le pedimos que redacte una sección coherente.
        # Si no, concatenamos heurísticamente.
        section_content = _generate_section(topic_name, raw_topic_content, llm)
        full_document_parts.append(section_content)

    # 4. Ensamblar documento final
    final_markdown = "\n\n---\n\n".join(full_document_parts)

    return {
        "ordered_class_markdown": final_markdown,
        "warnings": warnings
    }


def _generate_section(title: str, content: str, llm: BaseChatModel = None) -> str:
    """
    Genera una sección específica (Título + Contenido redactado).
    """
    header = f"## {title}"
    
    # --- FALLBACK SIN LLM ---
    if not llm:
        return f"{header}\n\n{content}"

    # --- REDACCIÓN CON AI ---
    system_prompt = """Eres un experto redactor de material educativo.
Tu tarea es recibir fragmentos de texto crudos pertenecientes a un mismo tema y redactarlos como una sección coherente y fluida de una clase.

Reglas:
1. NO incluyas el título principal (ya se agrega externamente), empieza redactando el contenido.
2. Puedes usar subtítulos (###) para organizar ideas internas si es necesario.
3. Elimina redundancias, muletillas y marcas de tiempo.
4. Mantén un tono académico, claro y explicativo.
5. Integra los fragmentos para que no parezca una lista de citas desconectadas.
6. NO inventes información, apégate estrictamente al contenido provisto.
"""
    
    user_msg = f"TEMA: {title}\n\nFRAGMENTOS DE CONTENIDO:\n{content[:15000]}" # Límite de seguridad
    
    try:
        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_msg)
        ]
        response = llm.invoke(messages)
        # Retornamos Título + Contenido generado
        return f"{header}\n\n{response.content}"
        
    except Exception as e:
        print(f"Error redactando sección '{title}': {e}")
        # Fallback en caso de error del LLM
        return f"{header}\n\n{content}"