"""
topic_scout.py — Detector de Temas (AI Activated)
"""
from typing import List
from pydantic import BaseModel, Field
from langchain_core.language_models import BaseChatModel
from langchain_core.prompts import ChatPromptTemplate

# Schema para salida estructurada
class TopicSchema(BaseModel):
    name: str = Field(description="Nombre corto del tema")
    relevance: int = Field(description="Relevancia 1-100")
    description: str = Field(description="Breve descripción del contenido")

class TopicListSchema(BaseModel):
    topics: List[TopicSchema]

# --- CORRECCIÓN AQUÍ: Se renombró de scan_topics a scan_for_topics ---
def scan_for_topics(content: str, llm: BaseChatModel = None) -> dict:
    """Detecta temas en el contenido usando LLM."""
    
    if not content:
        return {"topics": []}

    if llm:
        structured_llm = llm.with_structured_output(TopicListSchema)
        
        prompt = ChatPromptTemplate.from_messages([
            ("system", "Eres un analista curricular. Identifica los temas principales tratados en este texto de clase."),
            ("human", "Contenido de la clase:\n{text}")
        ])
        
        try:
            chain = prompt | structured_llm
            result = chain.invoke({"text": content[:15000]})
            
            # Convertir a formato diccionario simple
            topics_list = []
            for t in result.topics:
                topics_list.append({
                    "id": t.name.lower().replace(" ", "_"),
                    "name": t.name,
                    "relevance": t.relevance,
                    "type": "concept"
                })
            
            return {"topics": topics_list}
            
        except Exception as e:
            print(f"Error en Topic Scout AI: {e}")
            return _heuristic_fallback(content)
    else:
        return _heuristic_fallback(content)

def _heuristic_fallback(content):
    # Fallback tonto si falla la AI
    return {
        "topics": [
            {"id": "tema_general", "name": "Tema General", "relevance": 100, "type": "general"}
        ]
    }