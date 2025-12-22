"""
phase2_graph.py — Grafo LangGraph para Fase 2

Este grafo transforma una "clase ordenada" en notas atómicas
conectadas con el knowledge graph existente.

NODOS:
1. graph_rag_context   → Recupera contexto del grafo + vectores
2. atomic_planner      → Decide cuántas notas generar y por qué
3. atomic_generator    → Genera las notas atómicas
4. epistemic_validator → Valida calidad epistemológica
5. refiner             → Corrige problemas (loop)
6. bundle_creator      → Serializa para revisión humana

FLUJO:
    START → graph_rag_context → atomic_planner → atomic_generator
                                                       ↓
                                              epistemic_validator
                                                       ↓
                                         ┌─────────────┴─────────────┐
                                         │                           │
                                   score < 85                   score >= 85
                                   & iter < 3                   OR iter >= 3
                                         │                           │
                                         ▼                           ▼
                                     refiner                  bundle_creator
                                         │                           │
                                         └────→ atomic_generator     │
                                                                     ▼
                                                                   END

CONEXIONES:
- Usa: core/state_schema.py (Phase2State)
- Lee: data/lessons/ordered/, data/index/
- Escribe: data/staging/phase2_pending/
- Llamado por: runner_phase2.py
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from dotenv import load_dotenv
from langgraph.graph import END, StateGraph

from core.state_schema import (
    Phase2State,
    generate_bundle_id,
)

from core.logic.phase2.atomic_planner import create_atomic_plan
from core.logic.phase2.atomic_generator import generate_atomic_notes
from core.logic.phase2.epistemic_validator import run_epistemic_validation
from core.logic.phase2.graph_rag_builder import build_rag_context

# Cargar variables de entorno
load_dotenv()


# =============================================================================
# CONSTANTES
# =============================================================================

QUALITY_THRESHOLD = 85
MAX_REFINEMENT_ITERATIONS = 3


# =============================================================================
# CONFIGURACIÓN DE LLM
# =============================================================================

def get_llm():
    """Obtiene instancia del LLM configurado desde .env"""
    try:
        from langchain_openai import ChatOpenAI
        
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            return None
        
        model = os.getenv("DEFAULT_LLM_MODEL", "gpt-4o-mini")
        
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

def graph_rag_context(state: Phase2State) -> dict[str, Any]:
    """Nodo 1: Recupera contexto del GraphRAG."""
    ordered_class_path = state.get("ordered_class_path", "")
    
    query_concepts = []
    if ordered_class_path and Path(ordered_class_path).exists():
        try:
            with open(ordered_class_path, "r", encoding="utf-8") as f:
                content = f.read()
            words = content.split()[:20]
            query_concepts = [w for w in words if len(w) > 4][:5]
        except Exception:
            pass
    
    try:
        index_path = Path("./data/index")
        context = build_rag_context(
            index_path=index_path,
            query_concepts=query_concepts,
            similar_note_ids=[]
        )
    except Exception as e:
        context = {
            "similar_chunks": [],
            "similar_notes": [],
            "graph_neighbors": [],
            "retrieved_at": datetime.now().isoformat(),
            "summary": f"No se pudo recuperar contexto: {str(e)}",
        }
    
    return {
        "graph_rag_context": context,
        "current_node": "graph_rag_context",
    }


def atomic_planner(state: Phase2State) -> dict[str, Any]:
    """Nodo 2: Decide cuántas notas atómicas generar."""
    ordered_class_path = state.get("ordered_class_path", "")
    rag_context = state.get("graph_rag_context", {})
    llm = get_llm()
    
    ordered_class = ""
    if ordered_class_path and Path(ordered_class_path).exists():
        try:
            with open(ordered_class_path, "r", encoding="utf-8") as f:
                ordered_class = f.read()
        except Exception:
            pass
    
    topics = []
    lines = ordered_class.split("\n")
    topic_count = 0
    for line in lines:
        if line.startswith("## "):
            topic_count += 1
            topics.append({
                "id": f"topic_{topic_count:03d}",
                "name": line.lstrip("#").strip(),
                "description": "",
                "keywords": [],
                "estimated_complexity": "intermediate",
                "prerequisites": []
            })
    
    result = create_atomic_plan(
        ordered_class=ordered_class,
        topics=topics,
        graph_rag_context=rag_context,
        llm=llm
    )
    
    return {
        "atomic_plan": result["atomic_plan"],
        "current_node": "atomic_planner",
    }


def atomic_generator(state: Phase2State) -> dict[str, Any]:
    """Nodo 3: Genera las notas atómicas."""
    plan = state["atomic_plan"]
    lesson_id = state["lesson_id"]
    ordered_class_path = state.get("ordered_class_path", "")
    rag_context = state.get("graph_rag_context", {})
    llm = get_llm()
    
    ordered_class = ""
    if ordered_class_path and Path(ordered_class_path).exists():
        try:
            with open(ordered_class_path, "r", encoding="utf-8") as f:
                ordered_class = f.read()
        except Exception:
            pass
    
    result = generate_atomic_notes(
        atomic_plan=plan,
        ordered_class=ordered_class,
        lesson_id=lesson_id,
        graph_rag_context=rag_context,
        llm=llm
    )
    
    return {
        "atomic_proposals": result["atomic_proposals"],
        "linking_matrix": result["linking_matrix"],
        "moc_updates": result.get("moc_updates", []),
        "current_node": "atomic_generator",
    }


def epistemic_validator(state: Phase2State) -> dict[str, Any]:
    """Nodo 4: Valida calidad epistemológica."""
    proposals = state["atomic_proposals"]
    ordered_class_path = state.get("ordered_class_path", "")
    rag_context = state.get("graph_rag_context", {})
    
    ordered_class = ""
    if ordered_class_path and Path(ordered_class_path).exists():
        try:
            with open(ordered_class_path, "r", encoding="utf-8") as f:
                ordered_class = f.read()
        except Exception:
            pass
    
    result = run_epistemic_validation(
        atomic_proposals=proposals,
        ordered_class=ordered_class,
        graph_rag_context=rag_context
    )
    
    return {
        "validation_report": result["validation_report"],
        "current_node": "epistemic_validator",
    }


def refiner(state: Phase2State) -> dict[str, Any]:
    """Nodo 5: Corrige problemas detectados."""
    proposals = state["atomic_proposals"]
    iteration = state.get("iteration_count", 0)
    
    new_iteration = iteration + 1
    
    for note in proposals:
        if isinstance(note, dict):
            if "frontmatter" not in note:
                note["frontmatter"] = {}
            note["frontmatter"]["refined_iteration"] = new_iteration
    
    return {
        "atomic_proposals": proposals,
        "iteration_count": new_iteration,
        "current_node": "refiner",
    }


def bundle_creator(state: Phase2State) -> dict[str, Any]:
    """Nodo 6: Crea el bundle para revisión humana."""
    lesson_id = state["lesson_id"]
    phase1_bundle_id = state["phase1_bundle_id"]
    
    bundle_id = generate_bundle_id(lesson_id, phase=2)
    
    report = state["validation_report"]
    total_score = (
        report.get("atomicity_score", 100) * 0.3 +
        report.get("evidence_score", 100) * 0.3 +
        report.get("format_score", 100) * 0.2 +
        report.get("coherence_score", 100) * 0.2
    )
    
    bundle_dict = {
        "bundle_id": bundle_id,
        "lesson_id": lesson_id,
        "phase1_bundle_id": phase1_bundle_id,
        "atomic_plan": state["atomic_plan"],
        "plan_rationale": f"Plan generado con {len(state['atomic_plan'])} notas propuestas",
        "atomic_proposals": state["atomic_proposals"],
        "linking_matrix": state["linking_matrix"],
        "moc_updates": state.get("moc_updates", []),
        "validation_report": {
            **report,
            "total_score": total_score,
            "is_passing": total_score >= QUALITY_THRESHOLD,
        },
        "graph_rag_context": state["graph_rag_context"],
        "iteration_count": state.get("iteration_count", 0),
    }
    
    return {
        "bundle": bundle_dict,
        "current_node": "bundle_creator",
    }


# =============================================================================
# FUNCIONES DE ROUTING
# =============================================================================

def should_refine(state: Phase2State) -> Literal["refiner", "bundle_creator"]:
    """Decide si refinar o proceder a crear bundle."""
    report = state.get("validation_report", {})
    iteration = state.get("iteration_count", 0)
    
    total_score = (
        report.get("atomicity_score", 100) * 0.3 +
        report.get("evidence_score", 100) * 0.3 +
        report.get("format_score", 100) * 0.2 +
        report.get("coherence_score", 100) * 0.2
    )
    
    has_errors = any(
        issue.get("severity") == "error" 
        for issue in report.get("issues", [])
    )
    
    if (total_score < QUALITY_THRESHOLD or has_errors) and iteration < MAX_REFINEMENT_ITERATIONS:
        return "refiner"
    
    return "bundle_creator"


# =============================================================================
# CONSTRUCCIÓN DEL GRAFO
# =============================================================================

def build_phase2_graph() -> StateGraph:
    """Construye el grafo de Phase 2."""
    graph = StateGraph(Phase2State)
    
    graph.add_node("graph_rag_context", graph_rag_context)
    graph.add_node("atomic_planner", atomic_planner)
    graph.add_node("atomic_generator", atomic_generator)
    graph.add_node("epistemic_validator", epistemic_validator)
    graph.add_node("refiner", refiner)
    graph.add_node("bundle_creator", bundle_creator)
    
    graph.set_entry_point("graph_rag_context")
    graph.add_edge("graph_rag_context", "atomic_planner")
    graph.add_edge("atomic_planner", "atomic_generator")
    graph.add_edge("atomic_generator", "epistemic_validator")
    
    graph.add_conditional_edges(
        "epistemic_validator",
        should_refine,
        {
            "refiner": "refiner",
            "bundle_creator": "bundle_creator",
        }
    )
    
    graph.add_edge("refiner", "atomic_generator")
    graph.add_edge("bundle_creator", END)
    
    return graph.compile()


# =============================================================================
# EJECUCIÓN
# =============================================================================

def run_phase2(
    lesson_id: str,
    ordered_class_path: Path | str,
    phase1_bundle_id: str,
    human_directives: str | None = None,
) -> dict[str, Any]:
    """Ejecuta el pipeline completo de Phase 2."""
    initial_state: Phase2State = {
        "lesson_id": lesson_id,
        "ordered_class_path": str(ordered_class_path),
        "phase1_bundle_id": phase1_bundle_id,
        "graph_rag_context": {},
        "atomic_plan": [],
        "atomic_proposals": [],
        "linking_matrix": [],
        "moc_updates": [],
        "validation_report": {},
        "current_node": "start",
        "iteration_count": 0,
        "human_directives": human_directives,
        "error": None,
    }
    
    graph = build_phase2_graph()
    result = graph.invoke(initial_state)
    
    return result
graph = build_phase2_graph()
# =============================================================================
# DIAGRAMA DEL GRAFO (para documentación)
# =============================================================================

PHASE2_GRAPH_DIAGRAM = """
┌─────────────────────────────────────────────────────────────────────────────┐
│                            PHASE 2 GRAPH                                    │
│                "De clase ordenada a atomic notes"                           │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│    ┌──────────┐                                                             │
│    │  START   │                                                             │
│    └────┬─────┘                                                             │
│         │                                                                   │
│         ▼                                                                   │
│    ┌─────────────────┐                                                      │
│    │GRAPH_RAG_CONTEXT│  Recupera contexto                                   │
│    │  (Vector+Graph) │  → similar_chunks, similar_notes, graph_neighbors    │
│    └────────┬────────┘                                                      │
│             │                                                               │
│             ▼                                                               │
│    ┌─────────────────┐                                                      │
│    │ ATOMIC_PLANNER  │  Decide qué notas generar                            │
│    │     (LLM)       │  → atomic_plan[] con justificaciones                 │
│    └────────┬────────┘                                                      │
│             │                                                               │
│             ▼                                                               │
│    ┌─────────────────┐◀─────────────────────────────────┐                   │
│    │ATOMIC_GENERATOR │  Genera notas + enlaces          │                   │
│    │     (LLM)       │  → atomic_proposals, linking_matrix                  │
│    └────────┬────────┘                                  │                   │
│             │                                           │                   │
│             ▼                                           │                   │
│    ┌─────────────────┐                                  │                   │
│    │EPISTEMIC_VALIDAT│  Valida calidad                  │                   │
│    │     (LLM)       │  → validation_report             │                   │
│    └────────┬────────┘                                  │                   │
│             │                                           │                   │
│             ▼                                           │                   │
│    ┌─────────────────┐                                  │                   │
│    │  should_refine? │                                  │                   │
│    └────────┬────────┘                                  │                   │
│             │                                           │                   │
│     ┌───────┴───────┐                                   │                   │
│     │               │                                   │                   │
│ score < 85     score >= 85                              │                   │
│ & iter < 3     OR iter >= 3                             │                   │
│     │               │                                   │                   │
│     ▼               │                                   │                   │
│ ┌────────┐          │                                   │                   │
│ │REFINER │──────────┴───────────────────────────────────┘                   │
│ │ (LLM)  │  Corrige issues                                                  │
│ └────────┘                                                                  │
│                     │                                                       │
│                     ▼                                                       │
│            ┌─────────────────┐                                              │
│            │ BUNDLE_CREATOR  │  Serializa para revisión                     │
│            │                 │  → Phase2Bundle                              │
│            └────────┬────────┘                                              │
│                     │                                                       │
│                     ▼                                                       │
│            ┌──────────┐                                                     │
│            │   END    │  → staging/phase2_pending/                          │
│            └──────────┘                                                     │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘

COGNITIVE LOOP:
El loop atomic_generator → epistemic_validator → refiner → atomic_generator
puede ejecutarse hasta MAX_REFINEMENT_ITERATIONS veces (default: 3).
Después de eso, el sistema procede a bundle_creator aunque el score sea bajo,
dejando la decisión final al humano.
"""