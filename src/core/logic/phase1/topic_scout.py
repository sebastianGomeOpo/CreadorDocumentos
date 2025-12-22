"""
topic_scout.py — Detector de Temas (AI Powered)

Analiza el texto crudo para identificar temas principales usando OpenAI.
"""

from __future__ import annotations

import json
from typing import Any, List

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel,Field # This is the new version

# Esquema para salida estructurada del LLM
class TopicSchema(BaseModel):
    id: str = Field(description="ID único del tema (topic_XXX)")
    name: str = Field(description="Nombre corto y claro del tema")
    description: str = Field(description="Breve descripción del contenido que abarca")
    keywords: List[str] = Field(description="Palabras clave asociadas")
    complexity: str = Field(description="basic, intermediate, o advanced")

class TopicListSchema(BaseModel):
    topics: List[TopicSchema]


def scout_topics_heuristic(raw_content: str) -> list[dict[str, Any]]:
    """Fallback heurístico si no hay LLM o falla."""
    topics = []
    lines = raw_content.split('\n')
    count = 0
    
    # Detección básica por headers
    for line in lines:
        if line.strip().startswith('#'):
            name = line.lstrip('#').strip()
            if not name: continue
            count += 1
            topics.append({
                "id": f"topic_{count:03d}",
                "name": name,
                "description": f"Sección extraída: {name}",
                "keywords": [],
                "estimated_complexity": "intermediate",
                "prerequisites": [],
            })
            
    if not topics:
        topics.append({
            "id": "topic_001",
            "name": "Contenido General",
            "description": "Tema principal detectado",
            "keywords": [],
            "estimated_complexity": "intermediate",
            "prerequisites": []
        })
    return topics


def scout_topics_llm(raw_content: str, llm: Any) -> list[dict[str, Any]]:
    """Extrae temas usando LLM."""
    
    system_prompt = """Eres un experto analista de contenidos educativos. 
    Tu tarea es leer el siguiente texto y extraer una lista de los temas principales (Topics) que se tratan.
    
    Reglas:
    1. Identifica temas coherentes y distintos.
    2. Asigna un nivel de complejidad estimado.
    3. Genera IDs secuenciales (topic_001, topic_002...).
    4. Sé exhaustivo pero agrupa ideas menores bajo temas más grandes.
    """
    
    # Usar salida estructurada si el modelo lo soporta (GPT-4o/Turbo)
    structured_llm = llm.with_structured_output(TopicListSchema)
    
    try:
        # Limitamos el contenido para no exceder tokens si es muy largo
        content_preview = raw_content[:15000] 
        
        prompt = ChatPromptTemplate.from_messages([
            ("system", system_prompt),
            ("human", "Texto a analizar:\n\n{text}")
        ])
        
        chain = prompt | structured_llm
        result = chain.invoke({"text": content_preview})
        
        # Convertir a formato de diccionario del sistema
        final_topics = []
        for t in result.topics:
            final_topics.append({
                "id": t.id,
                "name": t.name,
                "description": t.description,
                "keywords": t.keywords,
                "estimated_complexity": t.complexity,
                "prerequisites": []
            })
        return final_topics

    except Exception as e:
        print(f"Error en Topic Scout LLM: {e}. Usando fallback.")
        return scout_topics_heuristic(raw_content)


def scan_for_topics(raw_content: str, llm: Any | None = None) -> list[dict[str, Any]]:
    """Función principal."""
    if llm:
        return scout_topics_llm(raw_content, llm)
    return scout_topics_heuristic(raw_content)