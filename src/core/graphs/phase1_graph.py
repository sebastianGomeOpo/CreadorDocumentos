"""
phase1_graph.py — Grafo LangGraph V2 con Arquitectura Paralela

Este grafo transforma una transcripción/texto crudo en una
"clase ordenada" lista para revisión humana.

ARQUITECTURA V2:
- Planificación secuencial (MasterPlan)
- Persistencia a disco (liberar RAM)
- Redacción paralela (Send/Map)
- Ensamblaje (Fan-in)

NODOS:
1. master_planner     → Genera MasterPlan con directivas
2. chunk_persister    → Guarda chunks en disco, limpia RAM
3. dispatcher         → Crea tareas y dispara Send()
4. writer_agent       → [PARALELO] Redacta una sección
5. collector          → [FAN-IN] Recolecta resultados
6. assembler          → Ensambla documento final
7. bundle_creator     → Serializa para revisión

FLUJO:
    START → master_planner → chunk_persister → dispatcher
                                                   │
                                          ┌───────┴───────┐
                                          │  Send() MAP   │
                                          └───────────────┘
                                                   │
                                    ┌──────────────┼──────────────┐
                                    ▼              ▼              ▼
                              writer_agent   writer_agent   writer_agent
                                    │              │              │
                                    └──────────────┼──────────────┘
                                                   │
                                          ┌───────┴───────┐
                                          │   collector   │
                                          │   (Fan-in)    │
                                          └───────────────┘
                                                   │
                                                   ▼
                                             assembler
                                                   │
                                                   ▼
                                           bundle_creator → END

CONEXIONES:
- Usa: core/state_schema.py (Phase1State, WriterTaskState)
- Usa: core/logic/phase1/ (módulos de lógica)
- Escribe: data/staging/phase1_pending/
"""

from __future__ import annotations

import hashlib
import operator
import os
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any, Sequence

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
from core.logic.phase1.chunk_persister import persist_chunks_to_disk, cleanup_temp_chunks
from core.logic.phase1.writer_agent import run_writer_agent
from core.logic.phase1.assembler import run_assembler

load_dotenv()


# =============================================================================
# CONFIGURACIÓN
# =============================================================================

DATA_BASE_PATH = Path(os.getenv("DATA_PATH", "./data"))
CHUNKS_DIR = DATA_BASE_PATH / "temp" / "chunks"
DRAFTS_DIR = DATA_BASE_PATH / "drafts"
NOTES_DIR = DATA_BASE_PATH / "section_notes"


# =============================================================================
# ESTADO CON REDUCER PARA FAN-IN
# =============================================================================

def add_writer_results(
    existing: list[dict],
    new: list[dict] | dict,
) -> list[dict]:
    """
    Reducer que acumula resultados de writers.
    Permite que múltiples nodos paralelos agreguen sus resultados.
    """
    if existing is None:
        existing = []
    
    if isinstance(new, dict):
        return existing + [new]
    elif isinstance(new, list):
        return existing + new
    return existing


class Phase1StateV2(Phase1State):
    """Estado extendido con reducer para writer_results."""
    writer_results: Annotated[list[dict], add_writer_results]


# =============================================================================
# NODO 1: MASTER PLANNER
# =============================================================================

def master_planner_node(state: Phase1State) -> dict[str, Any]:
    """
    Nodo 1: Genera el MasterPlan.
    
    Input: raw_content, source_metadata
    Output: master_plan (serializado)
    """
    raw_content = state["raw_content"]
    source_meta = state["source_metadata"]
    source_id = source_meta.get("source_id", generate_source_id(raw_content))
    
    # Crear plan
    plan = create_master_plan(raw_content, source_id)
    
    return {
        "master_plan": plan.model_dump(),
        "current_node": "master_planner",
    }


# =============================================================================
# NODO 2: CHUNK PERSISTER
# =============================================================================

def chunk_persister_node(state: Phase1State) -> dict[str, Any]:
    """
    Nodo 2: Persiste chunks a disco y limpia RAM.
    
    Input: raw_content, master_plan
    Output: chunk_paths
    """
    raw_content = state["raw_content"]
    plan_dict = state["master_plan"]
    
    # Reconstruir MasterPlan
    plan = MasterPlan(**plan_dict)
    
    # Persistir chunks
    chunk_infos = persist_chunks_to_disk(
        raw_content=raw_content,
        master_plan=plan,
        base_path=CHUNKS_DIR,
    )
    
    # Actualizar plan con rutas a chunks
    for info in chunk_infos:
        for topic in plan.topics:
            if topic.sequence_id == info["sequence_id"]:
                topic.chunk_path = info["chunk_path"]
                break
    
    # Extraer solo las rutas
    chunk_paths = [info["chunk_path"] for info in chunk_infos]
    
    return {
        "chunk_paths": chunk_paths,
        "master_plan": plan.model_dump(),  # Plan actualizado con rutas
        "current_node": "chunk_persister",
    }


# =============================================================================
# NODO 3: DISPATCHER (genera Send() para paralelo)
# =============================================================================

# =============================================================================
# NODO 3: DISPATCHER (Separado en Nodo y Lógica de Borde)
# =============================================================================

def dispatcher_node(state: Phase1State) -> dict[str, Any]:
    """
    Nodo 3: Dispatcher (Passthrough).
    Solo marca el paso por este nodo. La lógica real está en el conditional_edge.
    """
    return {
        "current_node": "dispatcher"
    }

def generate_writer_tasks(state: Phase1State) -> list[Send]:
    """
    Lógica de Borde Condicional.
    Genera la lista de Send() para ejecutar writer_agent en paralelo.
    """
    plan_dict = state["master_plan"]
    plan = MasterPlan(**plan_dict)
    
    # Crear una tarea por cada topic
    sends = []
    
    for topic in plan.topics:
        # Construir estado mínimo para el writer
        task_state: WriterTaskState = {
            "chunk_path": topic.chunk_path,
            "sequence_id": topic.sequence_id,
            "topic_id": topic.topic_id,
            "topic_name": topic.topic_name,
            "must_include": topic.must_include,
            "must_exclude": topic.must_exclude,
            "key_concepts": topic.key_concepts,
            "navigation_context": topic.navigation.model_dump() if topic.navigation else {},
        }
        
        # Disparar writer_agent con este estado
        sends.append(Send("writer_agent", task_state))
    
    return sends

# =============================================================================
# NODO 4: WRITER AGENT (ejecuta en paralelo)
# =============================================================================

def writer_agent_node(state: WriterTaskState) -> dict[str, Any]:
    """
    Nodo 4: Writer Agent - redacta una sección.
    
    Se ejecuta N veces en paralelo, una por cada Send().
    Recibe WriterTaskState, retorna resultado para el collector.
    """
    result = run_writer_agent(state)
    
    # Retornar resultado para acumular en writer_results
    return {
        "writer_results": [result.model_dump()],
    }


# =============================================================================
# NODO 5: COLLECTOR (fan-in implícito)
# =============================================================================

def collector_node(state: Phase1State) -> dict[str, Any]:
    """
    Nodo 5: Collector - punto de sincronización.
    
    Este nodo existe para marcar el punto de fan-in.
    Los resultados ya están acumulados en writer_results por el reducer.
    """
    writer_results = state.get("writer_results", [])
    
    return {
        "current_node": "collector",
        # Los writer_results ya están acumulados
    }


# =============================================================================
# NODO 6: ASSEMBLER
# =============================================================================

def assembler_node(state: Phase1State) -> dict[str, Any]:
    """
    Nodo 6: Assembler - ensambla documento final.
    
    Input: writer_results, master_plan
    Output: draft_path, section_notes_dir, ordered_class_markdown
    """
    writer_results = state.get("writer_results", [])
    plan_dict = state.get("master_plan", {})
    source_meta = state["source_metadata"]
    source_id = source_meta.get("source_id", "unknown")
    
    # Ensamblar
    result = run_assembler(
        writer_results=writer_results,
        source_id=source_id,
        master_plan=plan_dict,
        drafts_dir=DRAFTS_DIR,
        notes_dir=NOTES_DIR,
    )
    
    # Leer el draft para ordered_class_markdown
    draft_path = result["draft_path"]
    with open(draft_path, "r", encoding="utf-8") as f:
        ordered_class_markdown = f.read()
    
    # Convertir warnings a formato legacy
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

def bundle_creator_node(state: Phase1State) -> dict[str, Any]:
    """
    Nodo 7: Crea el bundle para revisión humana.
    
    Input: Todos los resultados acumulados
    Output: bundle (serializado)
    """
    source_meta = state["source_metadata"]
    source_id = source_meta.get("source_id", generate_source_id(state.get("raw_content", "")))
    bundle_id = generate_bundle_id(source_id, phase=1)
    
    plan_dict = state.get("master_plan", {})
    
    # Extraer topics del plan para formato legacy
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
    
    # Construir bundle
    bundle_dict = {
        "bundle_id": bundle_id,
        "source_metadata": source_meta,
        "raw_content_preview": state.get("raw_content", "")[:500],
        
        # V2: Plan maestro
        "master_plan": plan_dict,
        
        # Legacy compatibility
        "topics": topics,
        "ordered_outline": ordered_outline,
        "semantic_chunks": [],  # Ya no usamos chunks en memoria
        
        # Productos
        "ordered_class_markdown": state.get("ordered_class_markdown", ""),
        "draft_path": state.get("draft_path", ""),
        "section_notes_dir": state.get("section_notes_dir", ""),
        "chunk_files": state.get("chunk_paths", []),
        
        # Warnings
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
    Construye el grafo de Phase 1 V2 con arquitectura paralela.
    
    Returns:
        Grafo compilado listo para ejecutar
    """
    # Usar estado con reducer para fan-in
    graph = StateGraph(Phase1StateV2)
    
    # Agregar nodos
    graph.add_node("master_planner", master_planner_node)
    graph.add_node("chunk_persister", chunk_persister_node)
    graph.add_node("dispatcher", dispatcher_node)
    graph.add_node("writer_agent", writer_agent_node)
    graph.add_node("collector", collector_node)
    graph.add_node("assembler", assembler_node)
    graph.add_node("bundle_creator", bundle_creator_node)
    
    # Flujo secuencial inicial
    graph.set_entry_point("master_planner")
    graph.add_edge("master_planner", "chunk_persister")
    graph.add_edge("chunk_persister", "dispatcher")
    
    # Dispatcher genera Send() → writer_agent (paralelo)
    # CORRECCIÓN: Usar 'generate_writer_tasks' como la función lógica del borde
    graph.add_conditional_edges(
        "dispatcher",
        generate_writer_tasks,  # <--- USAR LA NUEVA FUNCIÓN AQUÍ
        ["writer_agent"],       # Nodos destino posibles
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
    Ejecuta el pipeline completo de Phase 1 V2.
    
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
    initial_state: Phase1State = {
        "source_path": str(source_path),
        "raw_content": raw_content,
        "source_metadata": source_metadata,
        
        # V2 fields
        "master_plan": {},
        "chunk_paths": [],
        "writer_results": [],
        
        # Legacy fields
        "topics": [],
        "ordered_outline": [],
        "semantic_chunks": [],
        
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
    
    # Limpiar chunks temporales (opcional)
    # cleanup_temp_chunks(CHUNKS_DIR)
    
    return result


# Pre-compilar grafo para reutilización
graph = build_phase1_graph()


# =============================================================================
# DIAGRAMA DEL GRAFO V2
# =============================================================================

PHASE1_GRAPH_DIAGRAM = """
┌─────────────────────────────────────────────────────────────────────────────┐
│                         PHASE 1 GRAPH V2                                    │
│                  "Arquitectura Paralela con Send()"                         │
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
│    │CHUNK_PERSISTER │  Guarda chunks en disco                               │
│    │   (I/O only)   │  Output: chunk_paths[], libera RAM                    │
│    └───────┬────────┘                                                       │
│            │                                                                │
│            ▼                                                                │
│    ┌────────────────┐                                                       │
│    │   DISPATCHER   │  Crea tareas paralelas                                │
│    │                │  Output: list[Send("writer_agent", task)]             │
│    └───────┬────────┘                                                       │
│            │                                                                │
│            │ ════════════════════════════════════                           │
│            │         Send() / MAP                                           │
│            │ ════════════════════════════════════                           │
│            │                                                                │
│     ┌──────┴──────┬──────────────┬──────────────┐                           │
│     │             │              │              │                           │
│     ▼             ▼              ▼              ▼                           │
│ ┌────────┐   ┌────────┐    ┌────────┐    ┌────────┐                         │
│ │ WRITER │   │ WRITER │    │ WRITER │    │ WRITER │  ← Paralelo             │
│ │ sec_01 │   │ sec_02 │    │ sec_03 │    │ sec_N  │  ← Contexto mínimo      │
│ └───┬────┘   └───┬────┘    └───┬────┘    └───┬────┘  ← Solo SU chunk        │
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

BENEFICIOS:
- Writers no comparten contexto → Sin contaminación cruzada
- Chunks en disco → RAM liberada para cada writer
- Paralelismo real → Tiempo total reducido
- Fan-in ordenado → Documento coherente al final
"""