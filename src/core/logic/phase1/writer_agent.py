"""
writer_agent.py — El Redactor con RAG

Un agente de redacción que BUSCA activamente su contexto usando RAG.
En lugar de recibir un chunk pre-cortado, recupera información relevante.

PRINCIPIO FUNDAMENTAL V2:
"Pull vs Push" — El agente jala lo que necesita en lugar de recibir un corte arbitrario.

RESPONSABILIDAD:
- Recibir directivas del tema (nombre, must_include, must_exclude)
- BUSCAR contexto relevante en la base vectorial
- Aplicar directivas de contención
- Generar markdown limpio y coherente

VENTAJAS RAG:
- Independencia del formato original
- Recupera contexto incluso si está disperso
- No depende de heurísticas de corte

CONEXIONES:
- Input: source_id + topic_directives + navigation_context
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
DEFAULT_VECTOR_DB_DIR = Path("data/temp/vector_db")
DEFAULT_K = 8  # Chunks a recuperar por búsqueda


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
   - Académico pero accesible
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

### CONTEXTO RECUPERADO (información relevante del documento original)

{retrieved_context}

---

Ahora redacta la sección. Recuerda:
- Comienza con ## {topic_name}
- Incluye todos los MUST_INCLUDE
- Evita mencionar cualquier MUST_EXCLUDE
- Mantén el foco en TU tema
- Usa SOLO la información del contexto recuperado
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
            temperature=0.3,
            api_key=api_key
        )
    except Exception as e:
        print(f"Error inicializando LLM: {e}")
        return None


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
# RECUPERACIÓN DE CONTEXTO (RAG)
# =============================================================================

def retrieve_context_for_topic(
    source_id: str,
    topic_name: str,
    key_concepts: list[str],
    must_include: list[str],
    db_path: Path | str = DEFAULT_VECTOR_DB_DIR,
    k: int = DEFAULT_K,
) -> str:
    """
    Recupera contexto relevante para un tema usando RAG.
    
    Estrategia de búsqueda múltiple:
    1. Buscar por nombre del tema
    2. Buscar por conceptos clave
    3. Buscar por must_include
    4. Combinar y deduplicar
    
    Args:
        source_id: ID de la fuente
        topic_name: Nombre del tema
        key_concepts: Conceptos clave
        must_include: Términos obligatorios
        db_path: Ruta a la base vectorial
        k: Chunks a recuperar por búsqueda
        
    Returns:
        Contexto concatenado
    """
    try:
        from core.logic.phase1.context_indexer import ContextIndexer
        
        indexer = ContextIndexer(db_path)
        all_chunks = set()
        
        # Búsqueda 1: Por nombre del tema
        docs1 = indexer.search(source_id, topic_name, k=k)
        for doc in docs1:
            all_chunks.add(doc.page_content)
        
        # Búsqueda 2: Por conceptos clave (si existen)
        if key_concepts:
            query2 = " ".join(key_concepts[:5])
            docs2 = indexer.search(source_id, query2, k=k // 2)
            for doc in docs2:
                all_chunks.add(doc.page_content)
        
        # Búsqueda 3: Por must_include (si existen)
        if must_include:
            query3 = " ".join(must_include[:3])
            docs3 = indexer.search(source_id, query3, k=k // 2)
            for doc in docs3:
                all_chunks.add(doc.page_content)
        
        # Combinar con separadores
        if all_chunks:
            return "\n\n---\n\n".join(sorted(all_chunks, key=len, reverse=True))
        else:
            return "[No se encontró contexto relevante en el documento]"
            
    except Exception as e:
        return f"[Error recuperando contexto: {str(e)}]"


def read_chunk_fallback(chunk_path: str | Path) -> str:
    """Fallback: lee un chunk desde disco si existe."""
    try:
        with open(chunk_path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception:
        return ""


# =============================================================================
# REDACCIÓN HEURÍSTICA (sin LLM)
# =============================================================================

def write_section_heuristic(
    context: str,
    topic_name: str,
    navigation: NavigationContext | None,
) -> str:
    """
    Fallback heurístico para redacción sin LLM.
    """
    import re
    lines = []
    
    lines.append(f"## {topic_name}")
    lines.append("")
    
    if navigation and navigation.previous_topic:
        lines.append(f"Continuando desde {navigation.previous_topic}, ahora exploraremos este tema.")
        lines.append("")
    
    # Limpiar contexto
    content = re.sub(r'^#{1,3}\s+.*$', '', context, flags=re.MULTILINE)
    content = content.strip()
    
    if content and content != "[No se encontró contexto relevante en el documento]":
        lines.append(content[:2000])  # Limitar
    else:
        lines.append("*[Contenido pendiente de desarrollo]*")
    
    lines.append("")
    
    if navigation and navigation.next_topic:
        lines.append(f"En la siguiente sección, abordaremos {navigation.next_topic}.")
    
    return "\n".join(lines)


# =============================================================================
# REDACCIÓN CON LLM
# =============================================================================

def write_section_llm(
    retrieved_context: str,
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
    Redacta una sección usando el LLM con contexto RAG.
    """
    nav_hint = navigation.get_transition_hint() if navigation else "Sección estándar"
    
    user_prompt = WRITER_USER_TEMPLATE.format(
        topic_name=topic_name,
        sequence_id=sequence_id,
        total_sections=total_sections,
        navigation_hint=nav_hint,
        must_include=format_list(must_include),
        must_exclude=format_list(must_exclude),
        key_concepts=format_list(key_concepts),
        retrieved_context=retrieved_context[:12000],  # Límite de contexto
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
    
    V2: Ahora usa RAG para recuperar contexto en lugar de leer chunks.
    
    Args:
        task: Estado de la tarea
        
    Returns:
        WriterResult con el markdown y metadata
    """
    start_time = time.time()
    
    # Extraer datos del task
    sequence_id = task["sequence_id"]
    topic_id = task["topic_id"]
    topic_name = task["topic_name"]
    must_include = task.get("must_include", [])
    must_exclude = task.get("must_exclude", [])
    key_concepts = task.get("key_concepts", [])
    nav_dict = task.get("navigation_context", {})
    
    # V2: Nuevos campos para RAG
    source_id = task.get("source_id", "")
    db_path = task.get("db_path", str(DEFAULT_VECTOR_DB_DIR))
    
    # Fallback a chunk_path si existe (compatibilidad)
    chunk_path = task.get("chunk_path", "")
    
    # Reconstruir NavigationContext
    navigation = None
    if nav_dict:
        navigation = NavigationContext(**nav_dict)
    
    try:
        # 1. RECUPERAR CONTEXTO
        if source_id:
            # V2: Usar RAG
            context = retrieve_context_for_topic(
                source_id=source_id,
                topic_name=topic_name,
                key_concepts=key_concepts,
                must_include=must_include,
                db_path=db_path,
            )
        elif chunk_path:
            # Fallback V1: Leer chunk físico
            context = read_chunk_fallback(chunk_path)
        else:
            context = "[Sin contexto disponible]"
        
        # 2. Obtener LLM
        llm = get_llm()
        
        # 3. Redactar sección
        if llm:
            compiled_markdown = write_section_llm(
                retrieved_context=context,
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
                context=context,
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
    """
    result = run_writer_agent(state)
    return result.model_dump()