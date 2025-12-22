"""
class_redactor.py — Limpiador de contenido (AI Activated)
"""
from langchain_core.messages import SystemMessage, HumanMessage
from langchain_core.language_models import BaseChatModel

# Se ha renombrado la función para coincidir con tu import
def generate_ordered_class(raw_content: str, llm: BaseChatModel = None) -> dict:
    """
    Toma texto crudo y lo convierte en Markdown estructurado usando LLM.
    """
    if not raw_content.strip():
        return {"ordered_content": ""}

    if llm:
        system_prompt = """Eres un experto editor de contenido educativo.
        Tu tarea es:
        1. Recibir una transcripción o notas crudas de una clase.
        2. Limpiar el ruido (muletillas, repeticiones, marcas de tiempo).
        3. Estructurarlo en Markdown claro con Títulos (##) y Subtítulos (###).
        4. NO resumas excesivamente, mantén la profundidad técnica.
        5. El resultado debe estar listo para ser estudiado.
        """
        
        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=f"TEXTO CRUDO:\n{raw_content[:25000]}") # Límite tokens
        ]
        
        try:
            response = llm.invoke(messages)
            return {"ordered_content": response.content}
        except Exception as e:
            print(f"Error en Redactor AI: {e}")
            return {"ordered_content": raw_content} # Fallback
            
    else:
        # Fallback simple si no hay LLM
        return {"ordered_content": raw_content}