"""
phase1_graph.py — Grafo LangGraph V2.1 con RAG

Este grafo transforma una transcripción/texto crudo en una
"clase ordenada" lista para revisión humana.

ARQUITECTURA V2.1 (RAG):
- Planificación secuencial (MasterPlan)
- INDEXACIÓN VECTORIAL (reemplaza chunk_persister)
- Redacción paralela con RETRIEVAL (Pull vs Push)
- Ensamblaje (Fan-in)

CAMBIOS VS V2.0:
- context_indexer reemplaza a chunk_persister
- Writers buscan contexto via RAG en lugar de leer archivos
- Corregido InvalidUpdateError: dispatcher separado de bifurcación
- Corregido concurrencia: Phase1StateV2 con reducer apropiado

NODOS:
1. master_planner     → Genera MasterPlan con directivas
2. context_indexer    → Crea índice vectorial (ChromaDB)
3. dispatch_prepare   → Prepara tareas (no bifurca)
4. writer_agent       → [PARALELO] Redacta con RAG
5. collector          → [FAN-IN] Recolecta resultados
6. assembler          → Ensambla documento final
7. bundle_creator     → Serializa para revisión

FLUJO:
    START → master_planner → context_indexer → dispatch_prepare
                                                       │
                                              ┌────────┴────────┐
                                              │   Send() MAP    │
                                              └─────────────────┘
                                                       │
                                    ┌──────────────────┼──────────────────┐
                                    ▼                  ▼                  ▼
                              writer_agent       writer_agent       writer_agent
                                (RAG query)       (RAG query)       (RAG query)
                                    │                  │                  │
                                    └──────────────────┼──────────────────┘
                                                       │
                                              ┌────────┴────────┐
                                              │    collector    │
                                              │    (Fan-in)     │
                                              └─────────────────┘
                                                       │
                                                       ▼
                                                  assembler
                                                       │
                                                       ▼
                                               bundle_creator → END
"""

from __future__ import annotations

import hashlib
import operator
import os
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any, Literal, Sequence

from dotenv import load_dotenv
from langgraph.graph import END, StateGraph
from langgraph.types import Send

from core.state_schema import (
    MasterPlan,
    Phase1State,
    WriterResult,
    WriterTaskState,
    generate_bundle_id,
    generate_source_id,
)

from core.logic.phase1.master_planner import create_master_plan
from core.logic.phase1.context_indexer import index_content_for_rag, cleanup_vector_db
from core.logic.phase1.writer_agent import run_writer_agent
from core.logic.phase1.assembler import run_assembler

load_dotenv()


# =============================================================================
# CONFIGURACIÓN
# =============================================================================

DATA_BASE_PATH = Path(os.getenv("DATA_PATH", "./data"))
VECTOR_DB_DIR = DATA_BASE_PATH / "temp" / "vector_db"
DRAFTS_DIR = DATA_BASE_PATH / "drafts"
NOTES_DIR = DATA_BASE_PATH / "section_notes"


# =============================================================================
# ESTADO V2.1 CON REDUCER PARA FAN-IN
# =============================================================================

def add_writer_results(
    existing: list[dict] | None,
    new: list[dict] | dict | None,
) -> list[dict]:
    """
    Reducer que acumula resultados de writers.
    Permite que múltiples nodos paralelos agreguen sus resultados.
    """
    if existing is None:
        existing = []
    
    if new is None:
        return existing
    
    if isinstance(new, dict):
        return existing + [new]
    elif isinstance(new, list):
        return existing + new
    
    return existing


class Phase1StateV2(Phase1State):
    """
    Estado extendido con:
    - Reducer para writer_results (fan-in)
    - Campos para RAG (db_path, source_id accesible)
    """
    # El Annotated con reducer permite acumulación desde nodos paralelos
    writer_results: Annotated[list[dict], add_writer_results]
    
    # V2.1: Campos adicionales para RAG
    db_path: str  # Ruta a la base vectorial
    index_stats: dict  # Estadísticas del indexador


# =============================================================================
# NODO 1: MASTER PLANNER
# =============================================================================

def master_planner_node(state: dict) -> dict[str, Any]:
    """
    Nodo 1: Genera el MasterPlan.
    
    Input: raw_content, source_metadata
    Output: master_plan (serializado)
    """
    raw_content = state["raw_content"]
    source_meta = state.get("source_metadata", {})
    source_id = source_meta.get("source_id", generate_source_id(raw_content))
    
    plan = create_master_plan(raw_content, source_id)
    
    return {
        "master_plan": plan.model_dump(),
        "current_node": "master_planner",
    }


# =============================================================================
# NODO 2: CONTEXT INDEXER (reemplaza chunk_persister)
# =============================================================================

def context_indexer_node(state: dict) -> dict[str, Any]:
    """
    Nodo 2: Crea índice vectorial del contenido.
    
    REEMPLAZA a chunk_persister.
    En lugar de cortar archivos, crea una DB vectorial para búsqueda semántica.
    
    Input: raw_content, source_metadata
    Output: db_path, index_stats
    """
    raw_content = state["raw_content"]
    source_meta = state.get("source_metadata", {})
    source_id = source_meta.get("source_id", generate_source_id(raw_content))
    
    # Crear índice vectorial
    stats = index_content_for_rag(
        raw_content=raw_content,
        source_id=source_id,
        base_path=VECTOR_DB_DIR,
    )
    
    return {
        "db_path": stats["db_path"],
        "index_stats": stats,
        "current_node": "context_indexer",
    }


# =============================================================================
# NODO 3: DISPATCH PREPARE (prepara tareas, NO bifurca)
# =============================================================================

def dispatch_prepare_node(state: dict) -> dict[str, Any]:
    """
    Nodo 3: Prepara las tareas para los writers.
    
    IMPORTANTE: Este nodo NO bifurca. Solo prepara los datos.
    La bifurcación ocurre en dispatch_to_writers (conditional edge).
    
    Esto evita el error InvalidUpdateError.
    """
    plan_dict = state.get("master_plan", {})
    source_meta = state.get("source_metadata", {})
    source_id = source_meta.get("source_id", "")
    db_path = state.get("db_path", str(VECTOR_DB_DIR))
    
    # Preparar lista de tareas (se usará en dispatch_to_writers)
    tasks = []
    
    if plan_dict:
        plan = MasterPlan(**plan_dict)
        
        for topic in plan.topics:
            task = {
                "sequence_id": topic.sequence_id,
                "topic_id": topic.topic_id,
                "topic_name": topic.topic_name,
                "must_include": topic.must_include,
                "must_exclude": topic.must_exclude,
                "key_concepts": topic.key_concepts,
                "navigation_context": topic.navigation.model_dump() if topic.navigation else {},
                # V2.1: Campos para RAG
                "source_id": source_id,
                "db_path": db_path,
            }
            tasks.append(task)
    
    return {
        "writer_tasks": tasks,  # Se usa en dispatch_to_writers
        "current_node": "dispatch_prepare",
    }


# =============================================================================
# FUNCIÓN DE BIFURCACIÓN (conditional edge, NO es nodo)
# =============================================================================

def dispatch_to_writers(state: dict) -> list[Send]:
    """
    Función de bifurcación que genera Send() para cada tarea.
    
    Esta función se usa como conditional_edge, NO como nodo.
    Retorna lista de Send() que disparan writer_agent en paralelo.
    """
    tasks = state.get("writer_tasks", [])
    
    sends = []
    for task in tasks:
        # Construir WriterTaskState
        task_state: WriterTaskState = {
            "chunk_path": "",  # No usado en V2.1
            "sequence_id": task["sequence_id"],
            "topic_id": task["topic_id"],
            "topic_name": task["topic_name"],
            "must_include": task.get("must_include", []),
            "must_exclude": task.get("must_exclude", []),
            "key_concepts": task.get("key_concepts", []),
            "navigation_context": task.get("navigation_context", {}),
            # V2.1: RAG fields
            "source_id": task.get("source_id", ""),
            "db_path": task.get("db_path", ""),
        }
        
        sends.append(Send("writer_agent", task_state))
    
    return sends


# =============================================================================
# NODO 4: WRITER AGENT (ejecuta en paralelo con RAG)
# =============================================================================

def writer_agent_node(state: WriterTaskState) -> dict[str, Any]:
    """
    Nodo 4: Writer Agent - redacta una sección usando RAG.
    
    V2.1: Ahora BUSCA contexto en la base vectorial en lugar de leer archivo.
    Se ejecuta N veces en paralelo, una por cada Send().
    """
    result = run_writer_agent(state)
    
    # Retornar resultado para acumular en writer_results
    return {
        "writer_results": [result.model_dump()],
    }


# =============================================================================
# NODO 5: COLLECTOR (fan-in)
# =============================================================================

def collector_node(state: dict) -> dict[str, Any]:
    """
    Nodo 5: Collector - punto de sincronización.
    
    Los resultados ya están acumulados en writer_results por el reducer.
    Este nodo marca el fin del paralelismo.
    """
    writer_results = state.get("writer_results", [])
    
    return {
        "current_node": "collector",
        # writer_results ya acumulados
    }


# =============================================================================
# NODO 6: ASSEMBLER
# =============================================================================

def assembler_node(state: dict) -> dict[str, Any]:
    """
    Nodo 6: Assembler - ensambla documento final.
    """
    writer_results = state.get("writer_results", [])
    plan_dict = state.get("master_plan", {})
    source_meta = state.get("source_metadata", {})
    source_id = source_meta.get("source_id", "unknown")
    
    result = run_assembler(
        writer_results=writer_results,
        source_id=source_id,
        master_plan=plan_dict,
        drafts_dir=DRAFTS_DIR,
        notes_dir=NOTES_DIR,
    )
    
    # Leer draft para ordered_class_markdown
    draft_path = result["draft_path"]
    try:
        with open(draft_path, "r", encoding="utf-8") as f:
            ordered_class_markdown = f.read()
    except Exception:
        ordered_class_markdown = ""
    
    warnings = [
        {"type": "processing", "description": w, "severity": "medium"}
        for w in result.get("warnings", [])
    ]
    
    return {
        "draft_path": result["draft_path"],
        "section_notes_dir": result["section_notes_dir"],
        "ordered_class_markdown": ordered_class_markdown,
        "warnings": warnings,
        "current_node": "assembler",
    }


# =============================================================================
# NODO 7: BUNDLE CREATOR
# =============================================================================

def bundle_creator_node(state: dict) -> dict[str, Any]:
    """
    Nodo 7: Crea el bundle para revisión humana.
    """
    source_meta = state.get("source_metadata", {})
    source_id = source_meta.get("source_id", generate_source_id(state.get("raw_content", "")))
    bundle_id = generate_bundle_id(source_id, phase=1)
    
    plan_dict = state.get("master_plan", {})
    
    # Extraer topics para formato legacy
    topics = []
    ordered_outline = []
    
    if plan_dict:
        plan = MasterPlan(**plan_dict)
        for topic in plan.topics:
            topics.append({
                "id": topic.topic_id,
                "name": topic.topic_name,
                "description": topic.description,
                "relevance": 80,
                "type": "concept",
            })
            ordered_outline.append({
                "position": topic.sequence_id,
                "topic_id": topic.topic_id,
                "topic_name": topic.topic_name,
                "rationale": f"Directivas: include={topic.must_include}, exclude={topic.must_exclude}",
                "subtopics": topic.key_concepts,
            })
    
    bundle_dict = {
        "bundle_id": bundle_id,
        "source_metadata": source_meta,
        "raw_content_preview": state.get("raw_content", "")[:500],
        
        # V2.1: Plan maestro + RAG stats
        "master_plan": plan_dict,
        "index_stats": state.get("index_stats", {}),
        
        # Legacy compatibility
        "topics": topics,
        "ordered_outline": ordered_outline,
        "semantic_chunks": [],
        
        # Productos
        "ordered_class_markdown": state.get("ordered_class_markdown", ""),
        "draft_path": state.get("draft_path", ""),
        "section_notes_dir": state.get("section_notes_dir", ""),
        "chunk_files": [],  # No usado en V2.1
        
        # Warnings
        "warnings": state.get("warnings", []),
    }
    
    return {
        "bundle": bundle_dict,
        "current_node": "bundle_creator",
    }


# =============================================================================
# CONSTRUCCIÓN DEL GRAFO V2.1
# =============================================================================

def build_phase1_graph() -> StateGraph:
    """
    Construye el grafo de Phase 1 V2.1 con RAG.
    
    CORRECCIONES:
    - dispatch_prepare es nodo, dispatch_to_writers es conditional_edge
    - Esto evita InvalidUpdateError
    """
    # Usar TypedDict base para evitar problemas de tipado
    from typing import TypedDict, Optional
    
    class GraphState(TypedDict, total=False):
        source_path: str
        raw_content: str
        source_metadata: dict
        master_plan: dict
        db_path: str
        index_stats: dict
        writer_tasks: list
        writer_results: Annotated[list[dict], add_writer_results]
        ordered_class_markdown: str
        draft_path: str
        section_notes_dir: str
        warnings: list
        bundle: dict
        current_node: str
        error: Optional[str]
    
    graph = StateGraph(GraphState)
    
    # Agregar nodos
    graph.add_node("master_planner", master_planner_node)
    graph.add_node("context_indexer", context_indexer_node)
    graph.add_node("dispatch_prepare", dispatch_prepare_node)
    graph.add_node("writer_agent", writer_agent_node)
    graph.add_node("collector", collector_node)
    graph.add_node("assembler", assembler_node)
    graph.add_node("bundle_creator", bundle_creator_node)
    
    # Flujo secuencial inicial
    graph.set_entry_point("master_planner")
    graph.add_edge("master_planner", "context_indexer")
    graph.add_edge("context_indexer", "dispatch_prepare")
    
    # BIFURCACIÓN: dispatch_prepare -> Send() -> writer_agent (paralelo)
    # Usamos add_conditional_edges con la función que retorna Send()
    graph.add_conditional_edges(
        "dispatch_prepare",
        dispatch_to_writers,  # Función que retorna list[Send]
        ["writer_agent"],  # Nodos destino posibles
    )
    
    # Writers convergen en collector
    graph.add_edge("writer_agent", "collector")
    
    # Flujo secuencial final
    graph.add_edge("collector", "assembler")
    graph.add_edge("assembler", "bundle_creator")
    graph.add_edge("bundle_creator", END)
    
    return graph.compile()


# =============================================================================
# EJECUCIÓN
# =============================================================================

def run_phase1(source_path: Path | str, raw_content: str) -> dict[str, Any]:
    """
    Ejecuta el pipeline completo de Phase 1 V2.1 con RAG.
    
    Args:
        source_path: Ruta al archivo fuente
        raw_content: Contenido de texto crudo
        
    Returns:
        Estado final con bundle
    """
    source_path = Path(source_path)
    
    # Generar metadata
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
    initial_state = {
        "source_path": str(source_path),
        "raw_content": raw_content,
        "source_metadata": source_metadata,
        
        # V2.1 fields
        "master_plan": {},
        "db_path": "",
        "index_stats": {},
        "writer_tasks": [],
        "writer_results": [],
        
        # Output
        "ordered_class_markdown": "",
        "draft_path": "",
        "section_notes_dir": "",
        "warnings": [],
        "bundle": {},
        
        # Control
        "current_node": "start",
        "error": None,
    }
    
    # Ejecutar grafo
    graph = build_phase1_graph()
    result = graph.invoke(initial_state)
    
    # Opcional: limpiar DB vectorial temporal
    # cleanup_vector_db(source_metadata["source_id"], VECTOR_DB_DIR)
    
    return result


# Pre-compilar grafo para reutilización
graph = build_phase1_graph()


# =============================================================================
# DIAGRAMA DEL GRAFO V2.1
# =============================================================================

PHASE1_GRAPH_DIAGRAM = """
┌─────────────────────────────────────────────────────────────────────────────┐
│                         PHASE 1 GRAPH V2.1 (RAG)                            │
│                     "Retrieval Augmented Generation"                        │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│    ┌──────────┐                                                             │
│    │  START   │                                                             │
│    └────┬─────┘                                                             │
│         │                                                                   │
│         ▼                                                                   │
│    ┌────────────────┐                                                       │
│    │ MASTER_PLANNER │  Genera MasterPlan con directivas                     │
│    │     (LLM)      │  Output: master_plan{topics, nav_map, risks}          │
│    └───────┬────────┘                                                       │
│            │                                                                │
│            ▼                                                                │
│    ┌────────────────┐                                                       │
│    │CONTEXT_INDEXER │  Crea índice vectorial (ChromaDB)                     │
│    │   (Embeddings) │  Output: db_path para RAG queries                     │
│    └───────┬────────┘                                                       │
│            │                                                                │
│            ▼                                                                │
│    ┌────────────────┐                                                       │
│    │DISPATCH_PREPARE│  Prepara tareas (NO bifurca)                          │
│    │                │  Output: writer_tasks[]                               │
│    └───────┬────────┘                                                       │
│            │                                                                │
│            │ ════════════════════════════════════                           │
│            │   dispatch_to_writers (conditional)                            │
│            │         Send() / MAP                                           │
│            │ ════════════════════════════════════                           │
│            │                                                                │
│     ┌──────┴──────┬──────────────┬──────────────┐                           │
│     │             │              │              │                           │
│     ▼             ▼              ▼              ▼                           │
│ ┌────────┐   ┌────────┐    ┌────────┐    ┌────────┐                         │
│ │ WRITER │   │ WRITER │    │ WRITER │    │ WRITER │  ← Paralelo             │
│ │ + RAG  │   │ + RAG  │    │ + RAG  │    │ + RAG  │  ← Cada uno BUSCA       │
│ │ query  │   │ query  │    │ query  │    │ query  │  ← su contexto          │
│ └───┬────┘   └───┬────┘    └───┬────┘    └───┬────┘                         │
│     │            │             │             │                              │
│     └────────────┴─────────────┴─────────────┘                              │
│                          │                                                  │
│            │ ════════════════════════════════════                           │
│            │         Fan-in / Reduce                                        │
│            │ ════════════════════════════════════                           │
│                          │                                                  │
│                          ▼                                                  │
│                  ┌────────────────┐                                         │
│                  │   COLLECTOR    │  Sincroniza resultados                  │
│                  │                │  writer_results[] acumulados            │
│                  └───────┬────────┘                                         │
│                          │                                                  │
│                          ▼                                                  │
│                  ┌────────────────┐                                         │
│                  │   ASSEMBLER    │  Ordena y ensambla                      │
│                  │                │  Output: Draft.md + section_notes/      │
│                  └───────┬────────┘                                         │
│                          │                                                  │
│                          ▼                                                  │
│                  ┌────────────────┐                                         │
│                  │ BUNDLE_CREATOR │  Serializa para revisión                │
│                  │                │  Output: Phase1Bundle                   │
│                  └───────┬────────┘                                         │
│                          │                                                  │
│                          ▼                                                  │
│                  ┌──────────┐                                               │
│                  │   END    │  → staging/phase1_pending/                    │
│                  └──────────┘                                               │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘

MEJORAS V2.1:
- RAG: Writers buscan contexto relevante (Pull) vs recibir cortes (Push)
- Independencia de formato: No depende de headers ni párrafos
- Contexto completo: Puede recuperar info dispersa en el documento
- Sin cortes arbitrarios: El indexador hace sliding windows matemático
"""