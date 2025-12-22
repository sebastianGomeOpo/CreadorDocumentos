"""
phase1_graph.py — Grafo LangGraph para Fase 1

Este grafo transforma una transcripción/texto crudo en una
"clase ordenada" lista para revisión humana.

NODOS:
1. topic_scout    → Detecta temas en el texto
2. topic_sorter   → Ordena temas didácticamente
3. semantic_chunker → Corta texto en chunks por tema
4. class_redactor → Reescribe la clase ordenadamente
5. bundle_creator → Serializa resultado para revisión

FLUJO:
    START → topic_scout → topic_sorter → semantic_chunker 
                                              ↓
                                       class_redactor 
                                              ↓
                                       bundle_creator → END

CONEXIONES:
- Usa: core/state_schema.py (Phase1State)
- Usa: core/logic/phase1/ (módulos de lógica)
- Escribe: data/staging/phase1_pending/
- Llamado por: watcher_phase1.py
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from langgraph.graph import END, StateGraph
import os
from dotenv import load_dotenv

from core.state_schema import (
    OrderedOutlineItem,
    Phase1Bundle,
    Phase1State,
    SemanticChunk,
    SourceMetadata,
    Topic,
    Warning,
    generate_bundle_id,
    generate_chunk_id,
    generate_source_id,
)

# Importar módulos de lógica
from core.logic.phase1.topic_scout import scan_for_topics
from core.logic.phase1.topic_sorter import create_ordered_outline
from core.logic.phase1.semantic_chunker import semantic_segmentation
from core.logic.phase1.class_redactor import generate_ordered_class


# =============================================================================
# CONFIGURACIÓN DE LLM (opcional)
# =============================================================================

# Cargar variables de entorno
load_dotenv()

def get_llm():
    """
    Obtiene instancia del LLM configurado desde .env
    
    Variables esperadas:
        - OPENAI_API_KEY: API key de OpenAI
        - DEFAULT_LLM_MODEL: Modelo a usar (default: gpt-4o-mini)
    """
    try:
        from langchain_openai import ChatOpenAI
        
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            print("⚠️ OPENAI_API_KEY no configurada, usando heurísticas")
            return None
        
        model = os.getenv("DEFAULT_LLM_MODEL", "gpt-4o-mini")
        
        return ChatOpenAI(
            model=model,
            temperature=0,
            api_key=api_key
        )
    except ImportError:
        print("⚠️ langchain_openai no instalado, usando heurísticas")
        return None
    except Exception as e:
        print(f"⚠️ Error inicializando LLM: {e}")
        return None


def get_fast_llm():
    """
    Obtiene instancia del LLM rápido para tareas simples.
    
    Variables esperadas:
        - FAST_LLM_MODEL: Modelo rápido (default: gpt-4o-mini)
    """
    try:
        from langchain_openai import ChatOpenAI
        
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            return None
        
        model = os.getenv("FAST_LLM_MODEL", "gpt-4o-mini")
        
        return ChatOpenAI(
            model=model,
            temperature=0,
            api_key=api_key
        )
    except Exception:
        return None


# =============================================================================
# NODOS DEL GRAFO
# =============================================================================

def topic_scout(state: Phase1State) -> dict[str, Any]:
    """
    Nodo 1: Detecta temas en el texto crudo.
    
    Este nodo analiza el contenido y extrae los temas principales
    sin ordenarlos todavía.
    
    INPUT:
        - raw_content: El texto crudo de la clase
        
    OUTPUT:
        - topics: Lista de temas detectados
    """
    raw_content = state["raw_content"]
    llm = get_llm()
    
    # Usar módulo de lógica
    topics = scan_for_topics(raw_content, llm=llm)
    
    return {
        "topics": topics,
        "current_node": "topic_scout",
    }


def topic_sorter(state: Phase1State) -> dict[str, Any]:
    """
    Nodo 2: Ordena temas didácticamente.
    
    Analiza dependencias entre temas y propone un orden
    de presentación óptimo.
    
    INPUT:
        - topics: Lista de temas detectados
        
    OUTPUT:
        - ordered_outline: Temario ordenado con justificaciones
    """
    topics = state["topics"]
    llm = get_llm()
    
    # Usar módulo de lógica
    ordered_outline = create_ordered_outline(topics, llm=llm)
    
    return {
        "ordered_outline": ordered_outline,
        "current_node": "topic_sorter",
    }


def semantic_chunker(state: Phase1State) -> dict[str, Any]:
    """
    Nodo 3: Corta el texto en chunks semánticos.
    
    Divide el contenido en fragmentos alineados a temas,
    preservando posiciones para citas.
    
    INPUT:
        - raw_content: Texto crudo
        - ordered_outline: Temario ordenado
        
    OUTPUT:
        - semantic_chunks: Lista de chunks con metadatos
    """
    raw_content = state["raw_content"]
    ordered_outline = state["ordered_outline"]
    llm = get_llm()
    
    # Usar módulo de lógica
    chunks = semantic_segmentation(raw_content, ordered_outline, llm=llm)
    
    return {
        "semantic_chunks": chunks,
        "current_node": "semantic_chunker",
    }


def class_redactor(state: Phase1State) -> dict[str, Any]:
    """
    Nodo 4: Reescribe la clase ordenadamente.
    
    Genera una versión limpia y ordenada de la clase,
    siguiendo el temario propuesto.
    
    INPUT:
        - semantic_chunks: Chunks del contenido
        - ordered_outline: Temario ordenado
        
    OUTPUT:
        - ordered_class_markdown: Clase redactada
        - warnings: Advertencias detectadas
    """
    chunks = state["semantic_chunks"]
    outline = state["ordered_outline"]
    llm = get_llm()
    
    # Usar módulo de lógica
    result = generate_ordered_class(outline, chunks, llm=llm)
    
    return {
        "ordered_class_markdown": result["ordered_class_markdown"],
        "warnings": result.get("warnings", []),
        "current_node": "class_redactor",
    }


def bundle_creator(state: Phase1State) -> dict[str, Any]:
    """
    Nodo 5: Crea el bundle para revisión humana.
    
    Serializa todo el estado procesado en un Phase1Bundle
    listo para guardar en staging.
    
    INPUT:
        - Todo el estado procesado
        
    OUTPUT:
        - bundle serializado (como dict)
    """
    source_meta = state["source_metadata"]
    
    # Crear bundle
    source_id = source_meta.get("source_id", generate_source_id(state["raw_content"]))
    bundle_id = generate_bundle_id(source_id, phase=1)
    
    # El bundle se retorna como dict para que el caller lo guarde
    bundle_dict = {
        "bundle_id": bundle_id,
        "source_metadata": source_meta,
        "raw_content_preview": state["raw_content"][:500],
        "topics": state["topics"],
        "ordered_outline": state["ordered_outline"],
        "semantic_chunks": state["semantic_chunks"],
        "ordered_class_markdown": state["ordered_class_markdown"],
        "warnings": state.get("warnings", []),
    }
    
    return {
        "bundle": bundle_dict,
        "current_node": "bundle_creator",
    }


# =============================================================================
# CONSTRUCCIÓN DEL GRAFO
# =============================================================================

def build_phase1_graph() -> StateGraph:
    """
    Construye el grafo de Phase 1.
    
    Returns:
        StateGraph compilado listo para ejecutar
    """
    # Crear grafo
    graph = StateGraph(Phase1State)
    
    # Añadir nodos
    graph.add_node("topic_scout", topic_scout)
    graph.add_node("topic_sorter", topic_sorter)
    graph.add_node("semantic_chunker", semantic_chunker)
    graph.add_node("class_redactor", class_redactor)
    graph.add_node("bundle_creator", bundle_creator)
    
    # Definir flujo (lineal en Phase 1)
    graph.set_entry_point("topic_scout")
    graph.add_edge("topic_scout", "topic_sorter")
    graph.add_edge("topic_sorter", "semantic_chunker")
    graph.add_edge("semantic_chunker", "class_redactor")
    graph.add_edge("class_redactor", "bundle_creator")
    graph.add_edge("bundle_creator", END)
    
    return graph.compile()


# =============================================================================
# EJECUCIÓN
# =============================================================================

def run_phase1(
    source_path: Path | str,
    raw_content: str,
) -> dict[str, Any]:
    """
    Ejecuta el pipeline completo de Phase 1.
    
    Args:
        source_path: Ruta al archivo fuente
        raw_content: Contenido crudo del archivo
        
    Returns:
        Resultado final incluyendo el bundle
    """
    source_path = Path(source_path)
    
    # Calcular metadatos de la fuente
    content_hash = hashlib.sha256(raw_content.encode()).hexdigest()
    source_metadata = {
        "filename": source_path.name,
        "file_path": str(source_path),
        "file_hash": content_hash,
        "file_size_bytes": len(raw_content.encode()),
        "ingested_at": datetime.now().isoformat(),
        "content_type": "text",
        "source_id": f"src_{content_hash[:16]}",
    }
    
    # Estado inicial
    initial_state: Phase1State = {
        "source_path": str(source_path),
        "raw_content": raw_content,
        "source_metadata": source_metadata,
        "topics": [],
        "ordered_outline": [],
        "semantic_chunks": [],
        "ordered_class_markdown": "",
        "warnings": [],
        "current_node": "start",
        "error": None,
    }
    
    # Ejecutar grafo
    graph = build_phase1_graph()
    result = graph.invoke(initial_state)
    
    return result


# =============================================================================
# DIAGRAMA DEL GRAFO (para documentación)
# =============================================================================

PHASE1_GRAPH_DIAGRAM = """
┌─────────────────────────────────────────────────────────────┐
│                      PHASE 1 GRAPH                          │
│              "De texto crudo a clase ordenada"              │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│    ┌──────────┐                                             │
│    │  START   │                                             │
│    └────┬─────┘                                             │
│         │                                                   │
│         ▼                                                   │
│    ┌──────────────┐                                         │
│    │ TOPIC_SCOUT  │  Detecta temas sin ordenar              │
│    │   (LLM)      │  Output: topics[]                       │
│    └──────┬───────┘                                         │
│           │                                                 │
│           ▼                                                 │
│    ┌──────────────┐                                         │
│    │ TOPIC_SORTER │  Ordena didácticamente                  │
│    │   (LLM)      │  Output: ordered_outline[]              │
│    └──────┬───────┘                                         │
│           │                                                 │
│           ▼                                                 │
│    ┌────────────────┐                                       │
│    │SEMANTIC_CHUNKER│  Corta por temas                      │
│    │   (LLM)        │  Output: semantic_chunks[]            │
│    └──────┬─────────┘                                       │
│           │                                                 │
│           ▼                                                 │
│    ┌──────────────┐                                         │
│    │CLASS_REDACTOR│  Reescribe ordenado                     │
│    │   (LLM)      │  Output: ordered_class_markdown         │
│    └──────┬───────┘                                         │
│           │                                                 │
│           ▼                                                 │
│    ┌──────────────┐                                         │
│    │BUNDLE_CREATOR│  Serializa para revisión                │
│    │              │  Output: Phase1Bundle                   │
│    └──────┬───────┘                                         │
│           │                                                 │
│           ▼                                                 │
│    ┌──────────┐                                             │
│    │   END    │  → staging/phase1_pending/                  │
│    └──────────┘                                             │
│                                                             │
└─────────────────────────────────────────────────────────────┘
"""

