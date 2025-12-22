"""
topic_sorter.py — Organizador Didáctico (AI Powered)

Ordena una lista de temas en una secuencia lógica de aprendizaje.
"""

from __future__ import annotations

from typing import Any, List
from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel,Field # This is the new version

class OutlineItemSchema(BaseModel):
    position: int
    topic_id: str
    topic_name: str
    rationale: str = Field(description="Por qué este tema va en esta posición")

class OutlineSchema(BaseModel):
    items: List[OutlineItemSchema]


def sort_topics_heuristic(topics: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Ordenamiento simple por ID/aparición."""
    ordered = []
    for i, topic in enumerate(topics):
        ordered.append({
            "position": i + 1,
            "topic_id": topic["id"],
            "topic_name": topic["name"],
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
    
    # Preparar input simplificado para el LLM
    topics_str = "\n".join([f"- ID: {t['id']}, Nombre: {t['name']}, Desc: {t['description']}" for t in topics])
    
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


def create_ordered_outline(topics: list[dict[str, Any]], llm: Any | None = None) -> list[dict[str, Any]]:
    """Función principal."""
    if llm:
        return sort_topics_llm(topics, llm)
    return sort_topics_heuristic(topics)