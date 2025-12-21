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
- Escribe: data/staging/phase1_pending/
- Llamado por: watcher_phase1.py
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from langgraph.graph import END, StateGraph

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
    
    # TODO: Aquí va la llamada al LLM
    # Por ahora, stub que detecta "temas" basándose en headers markdown
    topics = []
    
    # Placeholder: detectar headers como temas
    lines = raw_content.split("\n")
    topic_count = 0
    
    for line in lines:
        if line.startswith("# ") or line.startswith("## "):
            topic_count += 1
            topic_name = line.lstrip("#").strip()
            topics.append({
                "id": f"topic_{topic_count:03d}",
                "name": topic_name,
                "description": f"Tema extraído: {topic_name}",
                "keywords": topic_name.lower().split(),
                "estimated_complexity": "intermediate",
                "prerequisites": [],
            })
    
    # Si no hay headers, crear un tema genérico
    if not topics:
        topics.append({
            "id": "topic_001",
            "name": "Contenido Principal",
            "description": "Tema único detectado en el documento",
            "keywords": [],
            "estimated_complexity": "intermediate",
            "prerequisites": [],
        })
    
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
    
    # TODO: Aquí va la llamada al LLM para ordenar
    # Por ahora, mantener el orden original con justificación genérica
    
    ordered_outline = []
    for i, topic in enumerate(topics):
        ordered_outline.append({
            "position": i + 1,
            "topic_id": topic["id"],
            "topic_name": topic["name"],
            "rationale": f"Posición {i+1}: orden de aparición en el texto original",
            "subtopics": [],
        })
    
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
    
    # TODO: Chunking semántico real con LLM
    # Por ahora, chunking simple por párrafos
    
    chunks = []
    paragraphs = raw_content.split("\n\n")
    
    current_pos = 0
    default_topic_id = ordered_outline[0]["topic_id"] if ordered_outline else "topic_001"
    
    for para in paragraphs:
        if not para.strip():
            current_pos += len(para) + 2
            continue
        
        chunk_id = generate_chunk_id(para, default_topic_id)
        
        chunks.append({
            "id": chunk_id,
            "topic_id": default_topic_id,
            "content": para.strip(),
            "start_position": current_pos,
            "end_position": current_pos + len(para),
            "anchor_text": para[:50].strip() + "...",
            "word_count": len(para.split()),
        })
        
        current_pos += len(para) + 2
    
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
    
    # TODO: Redacción con LLM
    # Por ahora, reconstruir con headers
    
    lines = ["# Clase Ordenada", ""]
    warnings = []
    
    for item in outline:
        lines.append(f"## {item['topic_name']}")
        lines.append("")
        
        # Añadir chunks del tema
        topic_chunks = [c for c in chunks if c["topic_id"] == item["topic_id"]]
        
        if not topic_chunks:
            warnings.append({
                "type": "gap",
                "description": f"No se encontró contenido para el tema: {item['topic_name']}",
                "location": item["topic_id"],
                "severity": "medium",
            })
        
        for chunk in topic_chunks:
            lines.append(chunk["content"])
            lines.append("")
    
    ordered_class_markdown = "\n".join(lines)
    
    return {
        "ordered_class_markdown": ordered_class_markdown,
        "warnings": warnings,
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