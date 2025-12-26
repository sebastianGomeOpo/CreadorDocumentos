"""
phase1_graph.py — Grafo LangGraph V3 (RAG Avanzado)

Transforma una transcripción/texto crudo en una "clase ordenada"
lista para revisión humana usando RAG jerárquico avanzado.

ARQUITECTURA V3:
1. MasterPlan: Planificación de estructura
2. Indexación Jerárquica: Bloques + Chunks + Embeddings multi-nivel
3. Redacción Paralela: TopicRetriever + Evidence Pack por sección
4. Ensamblaje: Fan-in de resultados

CAMBIOS VS V2.1:
- Indexador usa hierarchical_chunker (semántico, no ventana fija)
- Writers usan TopicRetriever con pipeline completo:
  - Facet Query Planner
  - Multi-Channel Retriever (Dense + Sparse + Parent)
  - Fusion Scorer (Relevancia + Coherencia - Redundancia)
  - Coverage Selector (no Top-K)
  - Context Assembler (Evidence Pack)

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
                              (Evidence Pack)   (Evidence Pack)   (Evidence Pack)
                                    │                  │                  │
                                    └──────────────────┼──────────────────┘
                                                       │
                                                  collector → assembler → bundle_creator → END
"""

from __future__ import annotations

import hashlib
import os
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any, Optional, TypedDict

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
from core.logic.phase1.context_indexer import ContextIndexer, cleanup_vector_db
from core.logic.phase1.writer_agent import run_writer_agent
from core.logic.phase1.assembler import run_assembler

load_dotenv()


# =============================================================================
# CONFIGURACIÓN
# =============================================================================

DATA_BASE_PATH = Path(os.getenv("DATA_PATH", "./data"))
VECTOR_DB_DIR = DATA_BASE_PATH / "temp" / "hierarchical_index"
DRAFTS_DIR = DATA_BASE_PATH / "drafts"
NOTES_DIR = DATA_BASE_PATH / "section_notes"


# =============================================================================
# ESTADO V3 CON REDUCER PARA FAN-IN
# =============================================================================

def add_writer_results(
    existing: list | None,
    new: list | dict | None,
) -> list:
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


class Phase1GraphState(TypedDict, total=False):
    """Estado del grafo Phase1 V3"""
    # Input
    source_path: str
    raw_content: str
    source_metadata: dict
    
    # MasterPlan
    master_plan: dict
    
    # Indexación
    source_id: str
    db_path: str
    index_stats: dict
    
    # Dispatch
    writer_tasks: list
    
    # Results (con reducer para fan-in)
    writer_results: Annotated[list, add_writer_results]
    
    # Assembly
    ordered_class_markdown: str
    draft_path: str
    section_notes_dir: str
    
    # Output
    warnings: list
    bundle: dict
    
    # Control
    current_node: str
    error: str


# =============================================================================
# NODOS DEL GRAFO
# =============================================================================

def master_planner_node(state: Phase1GraphState) -> dict:
    """
    Genera el MasterPlan desde el contenido raw.
    """
    raw_content = state.get("raw_content", "")
    source_path = state.get("source_path", "")
    
    print(f"\n{'='*60}")
    print("[MasterPlanner] Generando plan...")
    print(f"{'='*60}")
    
    try:
        master_plan = create_master_plan(raw_content)
        
        topics = master_plan.get("topics", [])
        print(f"[MasterPlanner] ✓ {len(topics)} temas identificados")
        for i, topic in enumerate(topics):
            print(f"  {i+1}. {topic.get('name', 'Sin nombre')}")
        
        return {
            "master_plan": master_plan,
            "current_node": "master_planner",
        }
        
    except Exception as e:
        return {
            "error": f"Error en MasterPlanner: {str(e)}",
            "current_node": "master_planner",
        }


def context_indexer_node(state: Phase1GraphState) -> dict:
    """
    Indexa el contenido usando el pipeline jerárquico.
    """
    raw_content = state.get("raw_content", "")
    source_path = state.get("source_path", "unknown")
    
    print(f"\n{'='*60}")
    print("[ContextIndexer] Indexando contenido...")
    print(f"{'='*60}")
    
    # Generar source_id
    source_id = generate_source_id(source_path)
    db_path = str(VECTOR_DB_DIR)
    
    try:
        # Crear indexer y procesar
        indexer = ContextIndexer(db_path)
        
        # Limpiar índice anterior si existe
        indexer.cleanup(source_id)
        
        # Indexar documento
        stats = indexer.index(source_id, raw_content)
        
        print(f"[ContextIndexer] ✓ {stats['chunks_count']} chunks indexados")
        print(f"[ContextIndexer] ✓ {stats['blocks_count']} bloques detectados")
        print(f"[ContextIndexer] ✓ Tiempo: {stats['elapsed_seconds']:.2f}s")
        
        return {
            "source_id": source_id,
            "db_path": db_path,
            "index_stats": stats,
            "current_node": "context_indexer",
        }
        
    except Exception as e:
        return {
            "error": f"Error en indexación: {str(e)}",
            "source_id": source_id,
            "db_path": db_path,
            "current_node": "context_indexer",
        }


def dispatch_prepare_node(state: Phase1GraphState) -> dict:
    """
    Prepara las tareas para los writers.
    NO bifurca - eso lo hace dispatch_to_writers.
    """
    master_plan = state.get("master_plan", {})
    source_id = state.get("source_id", "")
    db_path = state.get("db_path", "")
    
    topics = master_plan.get("topics", [])
    total_topics = len(topics)
    
    print(f"\n{'='*60}")
    print(f"[Dispatch] Preparando {total_topics} tareas...")
    print(f"{'='*60}")
    
    writer_tasks = []
    
    for i, topic in enumerate(topics):
        # Construir contexto de navegación
        navigation = {}
        if i > 0:
            navigation["previous_topic"] = topics[i - 1].get("name", "")
        if i < total_topics - 1:
            navigation["next_topic"] = topics[i + 1].get("name", "")
        
        task = {
            "source_id": source_id,
            "db_path": db_path,
            "topic_name": topic.get("name", f"Tema {i+1}"),
            "topic_index": i,
            "total_topics": total_topics,
            "key_concepts": topic.get("key_concepts", []),
            "must_include": topic.get("must_include", []),
            "must_exclude": topic.get("must_exclude", []),
            "navigation": navigation,
        }
        
        writer_tasks.append(task)
        print(f"  [Task {i+1}] {task['topic_name']}")
    
    return {
        "writer_tasks": writer_tasks,
        "current_node": "dispatch_prepare",
    }


def dispatch_to_writers(state: Phase1GraphState) -> list:
    """
    Función de conditional_edges que dispara Send() a cada writer.
    
    Returns:
        Lista de Send() para ejecución paralela
    """
    writer_tasks = state.get("writer_tasks", [])
    
    sends = []
    for task in writer_tasks:
        sends.append(Send("writer_agent", task))
    
    return sends


def writer_agent_node(task_state: dict) -> dict:
    """
    Ejecuta el Writer Agent para una tarea.
    Recibe task_state directamente del Send().
    """
    topic_name = task_state.get("topic_name", "Unknown")
    topic_index = task_state.get("topic_index", 0)
    
    print(f"\n  [Writer {topic_index + 1}] Redactando: {topic_name}")
    
    try:
        result = run_writer_agent(task_state)
        
        print(f"  [Writer {topic_index + 1}] ✓ {result['word_count']} palabras")
        if result.get("warnings"):
            for w in result["warnings"]:
                print(f"  [Writer {topic_index + 1}] ⚠ {w}")
        
        # Retornar para el reducer
        return {"writer_results": result}
        
    except Exception as e:
        print(f"  [Writer {topic_index + 1}] ✗ Error: {str(e)}")
        return {
            "writer_results": {
                "topic_name": topic_name,
                "topic_index": topic_index,
                "markdown": f"# {topic_name}\n\n[Error: {str(e)}]",
                "word_count": 0,
                "warnings": [f"Error: {str(e)}"],
                "error": str(e),
            }
        }


def collector_node(state: Phase1GraphState) -> dict:
    """
    Recolecta y ordena resultados de writers.
    El reducer ya acumuló todo en writer_results.
    """
    writer_results = state.get("writer_results", [])
    
    print(f"\n{'='*60}")
    print(f"[Collector] Recolectando {len(writer_results)} resultados...")
    print(f"{'='*60}")
    
    # Ordenar por topic_index
    sorted_results = sorted(
        writer_results,
        key=lambda r: r.get("topic_index", 0)
    )
    
    # Estadísticas
    total_words = sum(r.get("word_count", 0) for r in sorted_results)
    with_warnings = sum(1 for r in sorted_results if r.get("warnings"))
    
    print(f"[Collector] ✓ {total_words} palabras totales")
    print(f"[Collector] ✓ {with_warnings} secciones con warnings")
    
    return {
        "writer_results": sorted_results,
        "current_node": "collector",
    }


def assembler_node(state: Phase1GraphState) -> dict:
    """
    Ensambla el documento final desde los resultados.
    """
    writer_results = state.get("writer_results", [])
    master_plan = state.get("master_plan", {})
    source_path = state.get("source_path", "")
    
    print(f"\n{'='*60}")
    print("[Assembler] Ensamblando documento final...")
    print(f"{'='*60}")
    
    try:
        result = run_assembler(writer_results, master_plan, source_path)
        
        print(f"[Assembler] ✓ Documento ensamblado")
        print(f"[Assembler] ✓ Draft: {result.get('draft_path', 'N/A')}")
        
        return {
            "ordered_class_markdown": result.get("markdown", ""),
            "draft_path": result.get("draft_path", ""),
            "section_notes_dir": result.get("notes_dir", ""),
            "warnings": result.get("warnings", []),
            "current_node": "assembler",
        }
        
    except Exception as e:
        return {
            "error": f"Error en ensamblaje: {str(e)}",
            "current_node": "assembler",
        }


def bundle_creator_node(state: Phase1GraphState) -> dict:
    """
    Crea el bundle final para revisión.
    """
    print(f"\n{'='*60}")
    print("[BundleCreator] Creando bundle...")
    print(f"{'='*60}")
    
    source_path = state.get("source_path", "")
    bundle_id = generate_bundle_id(source_path)
    
    bundle = {
        "bundle_id": bundle_id,
        "source_path": source_path,
        "draft_path": state.get("draft_path", ""),
        "notes_dir": state.get("section_notes_dir", ""),
        "master_plan": state.get("master_plan", {}),
        "index_stats": state.get("index_stats", {}),
        "warnings": state.get("warnings", []),
        "created_at": datetime.now().isoformat(),
        "status": "pending_review",
    }
    
    print(f"[BundleCreator] ✓ Bundle ID: {bundle_id}")
    
    return {
        "bundle": bundle,
        "current_node": "bundle_creator",
    }


# =============================================================================
# CONSTRUCCIÓN DEL GRAFO V3
# =============================================================================

def build_phase1_graph() -> StateGraph:
    """
    Construye el grafo de Phase 1 V3 con RAG avanzado.
    """
    graph = StateGraph(Phase1GraphState)
    
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
    
    # Bifurcación paralela
    graph.add_conditional_edges(
        "dispatch_prepare",
        dispatch_to_writers,
        ["writer_agent"],
    )
    
    # Fan-in
    graph.add_edge("writer_agent", "collector")
    
    # Flujo final
    graph.add_edge("collector", "assembler")
    graph.add_edge("assembler", "bundle_creator")
    graph.add_edge("bundle_creator", END)
    
    return graph.compile()


# =============================================================================
# EJECUCIÓN
# =============================================================================

def run_phase1(source_path: Path | str, raw_content: str) -> dict[str, Any]:
    """
    Ejecuta el pipeline completo de Phase 1 V3.
    
    Args:
        source_path: Ruta al archivo fuente
        raw_content: Contenido de texto crudo
        
    Returns:
        Estado final del grafo
    """
    print(f"\n{'='*60}")
    print("PHASE 1 V3 — RAG AVANZADO")
    print(f"{'='*60}")
    print(f"Fuente: {source_path}")
    print(f"Tamaño: {len(raw_content):,} caracteres")
    print(f"{'='*60}\n")
    
    # Construir y ejecutar grafo
    graph = build_phase1_graph()
    
    initial_state = {
        "source_path": str(source_path),
        "raw_content": raw_content,
        "source_metadata": {
            "path": str(source_path),
            "size": len(raw_content),
            "processed_at": datetime.now().isoformat(),
        },
    }
    
    # Ejecutar
    final_state = graph.invoke(initial_state)
    
    # Resumen
    print(f"\n{'='*60}")
    print("RESUMEN")
    print(f"{'='*60}")
    
    if final_state.get("error"):
        print(f"✗ Error: {final_state['error']}")
    else:
        bundle = final_state.get("bundle", {})
        print(f"✓ Bundle: {bundle.get('bundle_id', 'N/A')}")
        print(f"✓ Draft: {bundle.get('draft_path', 'N/A')}")
        
        index_stats = final_state.get("index_stats", {})
        print(f"✓ Chunks: {index_stats.get('chunks_count', 'N/A')}")
        print(f"✓ Bloques: {index_stats.get('blocks_count', 'N/A')}")
    
    print(f"{'='*60}\n")
    
    return final_state


# Compilar grafo al importar
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