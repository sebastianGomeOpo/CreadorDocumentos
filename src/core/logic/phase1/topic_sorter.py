"""
topic_sorter.py — Organizador Didáctico (AI Powered)

Ordena una lista de temas en una secuencia lógica de aprendizaje.
"""

from __future__ import annotations

from typing import Any, List
from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field

# =============================================================================
# SCHEMAS
# =============================================================================

class OutlineItemSchema(BaseModel):
    position: int
    topic_id: str
    topic_name: str
    rationale: str = Field(description="Por qué este tema va en esta posición")

class OutlineSchema(BaseModel):
    items: List[OutlineItemSchema]

# =============================================================================
# FUNCIONES AUXILIARES
# =============================================================================

def _normalize_topics(topics: list[Any]) -> list[dict[str, Any]]:
    """
    Convierte cualquier entrada (lista de strings o dicts) 
    a una lista estandarizada de diccionarios.
    Evita el error 'string indices must be integers'.
    """
    normalized = []
    for i, t in enumerate(topics):
        if isinstance(t, dict):
            # Ya es un diccionario, aseguramos que tenga ID
            if "id" not in t:
                t["id"] = f"topic_{i:03d}"
            if "name" not in t:
                t["name"] = "Tema sin nombre"
            normalized.append(t)
        elif isinstance(t, str):
            # Es un string, lo convertimos a dict
            normalized.append({
                "id": f"topic_{i:03d}",
                "name": t,
                "description": "Tema detectado",
                "relevance": 50
            })
        else:
            # Caso raro, convertir a string y luego a dict
            normalized.append({
                "id": f"topic_{i:03d}",
                "name": str(t),
                "description": "Objeto desconocido"
            })
    return normalized

# =============================================================================
# LÓGICA DE ORDENAMIENTO
# =============================================================================

def sort_topics_heuristic(topics: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Ordenamiento simple por ID/aparición (Fallback)."""
    ordered = []
    for i, topic in enumerate(topics):
        # Ahora topic siempre será un dict gracias a _normalize_topics
        ordered.append({
            "position": i + 1,
            "topic_id": topic.get("id", f"topic_{i:03d}"),
            "topic_name": topic.get("name", "Sin nombre"),
            "rationale": "Orden original del documento",
            "subtopics": []
        })
    return ordered

def sort_topics_llm(topics: list[dict[str, Any]], llm: Any) -> list[dict[str, Any]]:
    """Ordena temas usando LLM para flujo didáctico."""
    
    system_prompt = """Eres un arquitecto de planes de estudio.
    Recibirás una lista de temas desordenados. Tu tarea es organizarlos en una secuencia lógica de aprendizaje.
    
    Criterios:
    1. De lo simple a lo complejo.
    2. Prerrequisitos primero.
    3. Agrupa temas relacionados.
    4. Provee una justificación (rationale) para cada posición.
    """
    
    structured_llm = llm.with_structured_output(OutlineSchema)
    
    # Preparar input seguro para el LLM
    topics_str = "\n".join([
        f"- ID: {t.get('id')}, Nombre: {t.get('name')}, Desc: {t.get('description', '')}" 
        for t in topics
    ])
    
    try:
        prompt = ChatPromptTemplate.from_messages([
            ("system", system_prompt),
            ("human", "Lista de temas:\n{topics}")
        ])
        
        chain = prompt | structured_llm
        result = chain.invoke({"topics": topics_str})
        
        final_outline = []
        for item in result.items:
            final_outline.append({
                "position": item.position,
                "topic_id": item.topic_id,
                "topic_name": item.topic_name,
                "rationale": item.rationale,
                "subtopics": [] 
            })
        return final_outline
        
    except Exception as e:
        print(f"Error en Topic Sorter LLM: {e}")
        return sort_topics_heuristic(topics)

# =============================================================================
# PUNTO DE ENTRADA
# =============================================================================

def create_ordered_outline(topics: list[Any], llm: Any | None = None) -> list[dict[str, Any]]:
    """Función principal llamada desde el grafo."""
    
    # 1. Normalizar entrada para evitar crashes
    clean_topics = _normalize_topics(topics)
    
    # 2. Elegir estrategia
    if llm:
        return sort_topics_llm(clean_topics, llm)
    return sort_topics_heuristic(clean_topics)