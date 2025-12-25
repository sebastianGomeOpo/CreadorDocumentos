"""
writer_agent.py — El Redactor Aislado

Un agente de redacción que opera con contexto MÍNIMO.
Solo ve SU chunk, sus directivas, y el mapa de navegación.

PRINCIPIO FUNDAMENTAL:
"Lo que no está en mi contexto, no puede contaminar mi salida"

RESPONSABILIDAD:
- Leer UN chunk desde disco
- Aplicar directivas must_include/must_exclude
- Usar contexto de navegación para transiciones
- Generar markdown limpio y coherente

AISLAMIENTO:
- NO tiene acceso al contenido de otros chunks
- NO tiene acceso al texto completo de la clase
- Su ventana de contexto está prístina

CONEXIONES:
- Input: chunk_path + directives + navigation_context
- Output: WriterResult con markdown compilado
- Llamado por: phase1_graph.py via Send() (paralelo)
"""

from __future__ import annotations

import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage

from core.state_schema import NavigationContext, WriterResult, WriterTaskState

load_dotenv()


# =============================================================================
# CONFIGURACIÓN
# =============================================================================

MAX_RETRIES = 2
DEFAULT_WORD_TARGET = 300


# =============================================================================
# PROMPTS
# =============================================================================

WRITER_SYSTEM_PROMPT = """Eres un redactor experto de material educativo. Tu tarea es transformar contenido crudo en una sección bien redactada de una clase.

REGLAS INQUEBRANTABLES:

1. FIDELIDAD AL CONTENIDO
   - Solo usa información que está EN el texto proporcionado
   - NO inventes datos, ejemplos o afirmaciones
   - Si algo no está claro, redáctalo de forma que refleje esa ambigüedad

2. CONTENCIÓN TEMÁTICA
   - DEBES incluir los conceptos en "MUST_INCLUDE"
   - NUNCA menciones los conceptos en "MUST_EXCLUDE" (pertenecen a otras secciones)
   - Mantente estrictamente dentro de los límites de TU sección

3. ESTRUCTURA
   - Inicia con el header de la sección (##)
   - Desarrolla el contenido en párrafos fluidos
   - Usa sub-headers (###) solo si es necesario para organizar ideas complejas
   - NO uses bullet points a menos que el contenido original los tenga

4. TRANSICIONES
   - Usa el contexto de navegación para crear transiciones suaves
   - Si hay un tema anterior, puedes hacer una breve referencia
   - Si hay un tema siguiente, puedes anticiparlo sutilmente al final

5. TONO
   - Blog Divulgativo Tecnico pero accesible
   - Directo, sin muletillas
   - Evita redundancias

FORMATO DE SALIDA:
Markdown puro, comenzando con el header ## de la sección.
NO incluyas explicaciones meta, solo el contenido redactado.
"""


WRITER_USER_TEMPLATE = """## INFORMACIÓN DE TU SECCIÓN

**Tema:** {topic_name}
**Posición:** Sección {sequence_id} de {total_sections}
**Contexto de navegación:** {navigation_hint}

### DIRECTIVAS DE CONTENCIÓN

**MUST_INCLUDE (conceptos que DEBEN estar):**
{must_include}

**MUST_EXCLUDE (conceptos que NO deben mencionarse):**
{must_exclude}

**KEY_CONCEPTS (conceptos clave del tema):**
{key_concepts}

### CONTENIDO CRUDO A REDACTAR

{chunk_content}

---

Ahora redacta la sección. Recuerda:
- Comienza con ## {topic_name}
- Incluye todos los MUST_INCLUDE
- Evita mencionar cualquier MUST_EXCLUDE
- Mantén el foco en TU tema
"""


# =============================================================================
# FUNCIONES AUXILIARES
# =============================================================================

def get_llm() -> BaseChatModel | None:
    """Obtiene instancia del LLM configurado."""
    try:
        from langchain_openai import ChatOpenAI
        
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            return None
        
        model = os.getenv("DEFAULT_LLM_MODEL", "gpt-4o-mini")
        
        return ChatOpenAI(
            model=model,
            temperature=0.3,  # Un poco de creatividad para redacción
            api_key=api_key
        )
    except Exception as e:
        print(f"Error inicializando LLM: {e}")
        return None


def read_chunk(chunk_path: str | Path) -> str:
    """Lee un chunk desde disco."""
    with open(chunk_path, "r", encoding="utf-8") as f:
        return f.read()


def format_list(items: list[str]) -> str:
    """Formatea una lista para el prompt."""
    if not items:
        return "(ninguno)"
    return "\n".join(f"- {item}" for item in items)


def validate_output(
    markdown: str,
    must_include: list[str],
    must_exclude: list[str],
) -> tuple[list[str], list[str], list[str]]:
    """
    Valida que el output cumpla con las directivas.
    
    Returns:
        (followed_includes, violated_excludes, warnings)
    """
    markdown_lower = markdown.lower()
    
    followed = []
    for term in must_include:
        if term.lower() in markdown_lower:
            followed.append(term)
    
    violated = []
    for term in must_exclude:
        if term.lower() in markdown_lower:
            violated.append(term)
    
    warnings = []
    
    # Advertencia si faltan must_includes
    missing = set(must_include) - set(followed)
    if missing:
        warnings.append(f"Faltan conceptos must_include: {', '.join(missing)}")
    
    # Advertencia si hay violaciones
    if violated:
        warnings.append(f"Se mencionaron conceptos must_exclude: {', '.join(violated)}")
    
    # Advertencia si es muy corto
    word_count = len(markdown.split())
    if word_count < 100:
        warnings.append(f"Sección muy corta ({word_count} palabras)")
    
    return followed, violated, warnings


# =============================================================================
# REDACCIÓN HEURÍSTICA (sin LLM)
# =============================================================================

def write_section_heuristic(
    chunk_content: str,
    topic_name: str,
    navigation: NavigationContext | None,
) -> str:
    """
    Fallback heurístico para redacción sin LLM.
    Limpia el texto y añade estructura básica.
    """
    lines = []
    
    # Header
    lines.append(f"## {topic_name}")
    lines.append("")
    
    # Transición inicial si hay tema anterior
    if navigation and navigation.previous_topic:
        lines.append(f"Continuando desde {navigation.previous_topic}, ahora exploraremos este tema.")
        lines.append("")
    
    # Contenido (limpieza básica)
    content = chunk_content
    
    # Remover headers duplicados
    import re
    content = re.sub(r'^#{1,3}\s+.*$', '', content, flags=re.MULTILINE)
    content = content.strip()
    
    if content:
        lines.append(content)
    else:
        lines.append("*[Contenido pendiente de desarrollo]*")
    
    lines.append("")
    
    # Transición final si hay tema siguiente
    if navigation and navigation.next_topic:
        lines.append(f"En la siguiente sección, abordaremos {navigation.next_topic}.")
    
    return "\n".join(lines)


# =============================================================================
# REDACCIÓN CON LLM
# =============================================================================

def write_section_llm(
    chunk_content: str,
    topic_name: str,
    sequence_id: int,
    total_sections: int,
    must_include: list[str],
    must_exclude: list[str],
    key_concepts: list[str],
    navigation: NavigationContext | None,
    llm: BaseChatModel,
) -> str:
    """
    Redacta una sección usando el LLM.
    
    Args:
        chunk_content: Contenido crudo del chunk
        topic_name: Nombre del tema
        sequence_id: Posición en la secuencia
        total_sections: Total de secciones
        must_include: Conceptos obligatorios
        must_exclude: Conceptos prohibidos
        key_concepts: Conceptos clave
        navigation: Contexto de navegación
        llm: Modelo de lenguaje
        
    Returns:
        Markdown de la sección
    """
    # Preparar contexto de navegación
    nav_hint = navigation.get_transition_hint() if navigation else "Sección estándar"
    
    # Construir prompt
    user_prompt = WRITER_USER_TEMPLATE.format(
        topic_name=topic_name,
        sequence_id=sequence_id,
        total_sections=total_sections,
        navigation_hint=nav_hint,
        must_include=format_list(must_include),
        must_exclude=format_list(must_exclude),
        key_concepts=format_list(key_concepts),
        chunk_content=chunk_content[:8000],  # Límite de seguridad
    )
    
    messages = [
        SystemMessage(content=WRITER_SYSTEM_PROMPT),
        HumanMessage(content=user_prompt),
    ]
    
    response = llm.invoke(messages)
    return response.content


# =============================================================================
# FUNCIÓN PRINCIPAL DEL WRITER
# =============================================================================

def run_writer_agent(task: WriterTaskState) -> WriterResult:
    """
    Ejecuta un Writer Agent para una tarea específica.
    
    Esta función es el punto de entrada para cada instancia paralela.
    
    Args:
        task: Estado de la tarea con toda la información necesaria
        
    Returns:
        WriterResult con el markdown y metadata
    """
    start_time = time.time()
    
    # Extraer datos del task
    chunk_path = task["chunk_path"]
    sequence_id = task["sequence_id"]
    topic_id = task["topic_id"]
    topic_name = task["topic_name"]
    must_include = task.get("must_include", [])
    must_exclude = task.get("must_exclude", [])
    key_concepts = task.get("key_concepts", [])
    nav_dict = task.get("navigation_context", {})
    
    # Reconstruir NavigationContext si existe
    navigation = None
    if nav_dict:
        navigation = NavigationContext(**nav_dict)
    
    try:
        # 1. Leer chunk desde disco (contexto mínimo)
        chunk_content = read_chunk(chunk_path)
        
        # 2. Obtener LLM
        llm = get_llm()
        
        # 3. Redactar sección
        if llm:
            compiled_markdown = write_section_llm(
                chunk_content=chunk_content,
                topic_name=topic_name,
                sequence_id=sequence_id,
                total_sections=navigation.total_sections if navigation else 1,
                must_include=must_include,
                must_exclude=must_exclude,
                key_concepts=key_concepts,
                navigation=navigation,
                llm=llm,
            )
        else:
            compiled_markdown = write_section_heuristic(
                chunk_content=chunk_content,
                topic_name=topic_name,
                navigation=navigation,
            )
        
        # 4. Validar output
        followed, violated, warnings = validate_output(
            compiled_markdown,
            must_include,
            must_exclude,
        )
        
        # 5. Calcular métricas
        word_count = len(compiled_markdown.split())
        processing_time = int((time.time() - start_time) * 1000)
        
        return WriterResult(
            sequence_id=sequence_id,
            topic_id=topic_id,
            topic_name=topic_name,
            compiled_markdown=compiled_markdown,
            word_count=word_count,
            processing_time_ms=processing_time,
            followed_must_include=followed,
            violated_must_exclude=violated,
            warnings=warnings,
            success=True,
            error_message=None,
        )
        
    except Exception as e:
        processing_time = int((time.time() - start_time) * 1000)
        
        # Fallback con contenido mínimo
        fallback_md = f"## {topic_name}\n\n*[Error durante la redacción: {str(e)}]*"
        
        return WriterResult(
            sequence_id=sequence_id,
            topic_id=topic_id,
            topic_name=topic_name,
            compiled_markdown=fallback_md,
            word_count=len(fallback_md.split()),
            processing_time_ms=processing_time,
            followed_must_include=[],
            violated_must_exclude=[],
            warnings=[f"Error: {str(e)}"],
            success=False,
            error_message=str(e),
        )


# =============================================================================
# WRAPPER PARA EL NODO DEL GRAFO
# =============================================================================

def writer_node(state: WriterTaskState) -> dict[str, Any]:
    """
    Wrapper del writer para usar como nodo en LangGraph.
    
    Args:
        state: Estado de la tarea (WriterTaskState)
        
    Returns:
        Diccionario con el resultado serializado
    """
    result = run_writer_agent(state)
    return result.model_dump()