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

from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from langgraph.graph import END, StateGraph

from core.state_schema import (
    ApprovalStatus,
    AtomicNote,
    AtomicNotePlan,
    GraphRAGContext,
    LinkType,
    MOCUpdate,
    Phase2Bundle,
    Phase2State,
    ProposedLink,
    ValidationIssue,
    ValidationReport,
    generate_bundle_id,
    generate_note_id,
)


# =============================================================================
# CONSTANTES
# =============================================================================

QUALITY_THRESHOLD = 85  # Score mínimo para aprobar
MAX_REFINEMENT_ITERATIONS = 3


# =============================================================================
# NODOS DEL GRAFO
# =============================================================================

def graph_rag_context(state: Phase2State) -> dict[str, Any]:
    """
    Nodo 1: Recupera contexto del GraphRAG.
    
    Busca en los índices vectoriales y el grafo de conocimiento
    para obtener contexto relevante.
    
    INPUT:
        - ordered_class_path: Path a la clase ordenada
        
    OUTPUT:
        - graph_rag_context: Contexto recuperado (similar chunks, notes, graph neighbors)
    """
    # TODO: Implementar búsqueda real en vector stores y grafo
    # Por ahora, retornar contexto vacío
    
    context = {
        "similar_chunks": [],      # IDs de chunks similares
        "similar_notes": [],       # IDs de notas existentes similares
        "graph_neighbors": [],     # Nodos a 1-hop en el grafo
        "retrieved_at": datetime.now().isoformat(),
        "summary": "No se encontró contexto previo relevante (vault nuevo o tema nuevo)",
    }
    
    return {
        "graph_rag_context": context,
        "current_node": "graph_rag_context",
    }


def atomic_planner(state: Phase2State) -> dict[str, Any]:
    """
    Nodo 2: Decide cuántas notas atómicas generar.
    
    Analiza el contenido y el contexto RAG para determinar
    el plan óptimo de atomización.
    
    INPUT:
        - ordered_class_path: Path a la clase ordenada
        - graph_rag_context: Contexto recuperado
        - human_directives: Directivas previas (si hay rechazo)
        
    OUTPUT:
        - atomic_plan: Lista de notas planificadas con justificación
    """
    # Leer clase ordenada (en implementación real)
    # Por ahora, generar plan placeholder
    
    human_directives = state.get("human_directives")
    
    # Ajustar plan basado en directivas humanas
    if human_directives:
        # TODO: Incorporar directivas en el prompt
        pass
    
    # Placeholder: generar 3 notas por defecto
    atomic_plan = [
        {
            "id": "plan_001",
            "topic_id": "topic_001",
            "proposed_title": "Concepto Principal",
            "rationale": "Idea central del material",
            "novelty_score": 0.9,
            "estimated_connections": 2,
        },
        {
            "id": "plan_002",
            "topic_id": "topic_001",
            "proposed_title": "Ejemplo Ilustrativo",
            "rationale": "Ejemplo concreto que clarifica el concepto",
            "novelty_score": 0.8,
            "estimated_connections": 1,
        },
        {
            "id": "plan_003",
            "topic_id": "topic_001",
            "proposed_title": "Aplicación Práctica",
            "rationale": "Cómo aplicar el concepto en contexto real",
            "novelty_score": 0.85,
            "estimated_connections": 3,
        },
    ]
    
    return {
        "atomic_plan": atomic_plan,
        "current_node": "atomic_planner",
    }


def atomic_generator(state: Phase2State) -> dict[str, Any]:
    """
    Nodo 3: Genera las notas atómicas.
    
    Crea las notas siguiendo el plan, con enlaces tipados
    y actualizaciones de MOCs.
    
    INPUT:
        - atomic_plan: Plan de notas a generar
        - graph_rag_context: Contexto para enlaces
        - human_directives: Ajustes solicitados
        
    OUTPUT:
        - atomic_proposals: Notas generadas
        - linking_matrix: Enlaces propuestos
        - moc_updates: Actualizaciones de MOCs
    """
    plan = state["atomic_plan"]
    lesson_id = state["lesson_id"]
    
    # TODO: Generación real con LLM
    # Por ahora, generar notas placeholder
    
    atomic_proposals = []
    linking_matrix = []
    
    for item in plan:
        note_id = generate_note_id(item["proposed_title"], lesson_id)
        
        note = {
            "id": note_id,
            "title": item["proposed_title"],
            "content": f"# {item['proposed_title']}\n\nContenido de la nota sobre {item['proposed_title'].lower()}.\n\nEsto es un placeholder - el contenido real vendría del LLM basándose en la clase ordenada.",
            "frontmatter": {
                "tags": ["pendiente-revision"],
                "status": "draft",
            },
            "source_id": lesson_id,
            "chunk_ids": [],
            "created_at": datetime.now().isoformat(),
        }
        atomic_proposals.append(note)
    
    # Generar enlaces entre notas
    if len(atomic_proposals) >= 2:
        linking_matrix.append({
            "source_note_id": atomic_proposals[0]["id"],
            "target_note_id": atomic_proposals[1]["id"],
            "link_type": "exemplifies",
            "rationale": "El ejemplo ilustra el concepto principal",
            "confidence": 0.9,
        })
    
    if len(atomic_proposals) >= 3:
        linking_matrix.append({
            "source_note_id": atomic_proposals[2]["id"],
            "target_note_id": atomic_proposals[0]["id"],
            "link_type": "applies",
            "rationale": "La aplicación práctica usa el concepto principal",
            "confidence": 0.85,
        })
    
    # MOC updates (placeholder)
    moc_updates = []
    
    return {
        "atomic_proposals": atomic_proposals,
        "linking_matrix": linking_matrix,
        "moc_updates": moc_updates,
        "current_node": "atomic_generator",
    }


def epistemic_validator(state: Phase2State) -> dict[str, Any]:
    """
    Nodo 4: Valida calidad epistemológica.
    
    Verifica atomicidad, evidencia, formato y coherencia
    de las notas generadas.
    
    INPUT:
        - atomic_proposals: Notas a validar
        - linking_matrix: Enlaces a validar
        
    OUTPUT:
        - validation_report: Reporte con scores e issues
    """
    proposals = state["atomic_proposals"]
    links = state["linking_matrix"]
    
    # TODO: Validación real con LLM
    # Por ahora, validación simple basada en heurísticas
    
    issues = []
    
    # Verificar cada nota
    for note in proposals:
        # Atomicidad: verificar que no sea muy larga
        word_count = len(note["content"].split())
        if word_count > 500:
            issues.append({
                "note_id": note["id"],
                "issue_type": "atomicity",
                "description": f"Nota demasiado larga ({word_count} palabras)",
                "suggestion": "Dividir en notas más pequeñas",
                "severity": "warning",
            })
        
        # Formato: verificar estructura básica
        if "# " not in note["content"]:
            issues.append({
                "note_id": note["id"],
                "issue_type": "format",
                "description": "Falta título en formato Markdown",
                "suggestion": "Añadir título con # ",
                "severity": "error",
            })
        
        # Evidencia: verificar que hay contenido sustancial
        if word_count < 30:
            issues.append({
                "note_id": note["id"],
                "issue_type": "evidence",
                "description": "Nota demasiado corta, posible falta de evidencia",
                "suggestion": "Expandir con más detalle y citas",
                "severity": "warning",
            })
    
    # Calcular scores
    atomicity_score = 100 - (len([i for i in issues if i["issue_type"] == "atomicity"]) * 20)
    evidence_score = 100 - (len([i for i in issues if i["issue_type"] == "evidence"]) * 25)
    format_score = 100 - (len([i for i in issues if i["issue_type"] == "format"]) * 30)
    coherence_score = 90  # Placeholder
    
    validation_report = {
        "atomicity_score": max(0, atomicity_score),
        "evidence_score": max(0, evidence_score),
        "format_score": max(0, format_score),
        "coherence_score": coherence_score,
        "issues": issues,
    }
    
    return {
        "validation_report": validation_report,
        "current_node": "epistemic_validator",
    }


def refiner(state: Phase2State) -> dict[str, Any]:
    """
    Nodo 5: Corrige problemas detectados.
    
    Toma el reporte de validación y ajusta las notas
    para resolver los issues.
    
    INPUT:
        - atomic_proposals: Notas actuales
        - validation_report: Issues a corregir
        - iteration_count: Número de iteración actual
        
    OUTPUT:
        - atomic_proposals: Notas corregidas
        - iteration_count: Incrementado
    """
    proposals = state["atomic_proposals"]
    report = state["validation_report"]
    iteration = state.get("iteration_count", 0)
    
    # TODO: Refinamiento real con LLM
    # Por ahora, marcamos que se intentó refinar
    
    # Incrementar contador
    new_iteration = iteration + 1
    
    # Placeholder: añadir nota de que fue refinado
    for note in proposals:
        note["frontmatter"]["refined_iteration"] = new_iteration
    
    return {
        "atomic_proposals": proposals,
        "iteration_count": new_iteration,
        "current_node": "refiner",
    }


def bundle_creator(state: Phase2State) -> dict[str, Any]:
    """
    Nodo 6: Crea el bundle para revisión humana.
    
    Serializa todo el estado procesado en un Phase2Bundle.
    
    INPUT:
        - Todo el estado de Phase 2
        
    OUTPUT:
        - bundle serializado (como dict)
    """
    lesson_id = state["lesson_id"]
    phase1_bundle_id = state["phase1_bundle_id"]
    
    bundle_id = generate_bundle_id(lesson_id, phase=2)
    
    # Convertir validation_report a formato esperado
    report = state["validation_report"]
    total_score = (
        report["atomicity_score"] * 0.3 +
        report["evidence_score"] * 0.3 +
        report["format_score"] * 0.2 +
        report["coherence_score"] * 0.2
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
    """
    Decide si refinar o proceder a crear bundle.
    
    Criterios:
    - Score < 85 Y iteraciones < 3 → refiner
    - De lo contrario → bundle_creator
    """
    report = state.get("validation_report", {})
    iteration = state.get("iteration_count", 0)
    
    # Calcular score total
    total_score = (
        report.get("atomicity_score", 100) * 0.3 +
        report.get("evidence_score", 100) * 0.3 +
        report.get("format_score", 100) * 0.2 +
        report.get("coherence_score", 100) * 0.2
    )
    
    # Verificar si hay errores críticos
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
    """
    Construye el grafo de Phase 2 con el Cognitive Loop.
    
    Returns:
        StateGraph compilado listo para ejecutar
    """
    graph = StateGraph(Phase2State)
    
    # Añadir nodos
    graph.add_node("graph_rag_context", graph_rag_context)
    graph.add_node("atomic_planner", atomic_planner)
    graph.add_node("atomic_generator", atomic_generator)
    graph.add_node("epistemic_validator", epistemic_validator)
    graph.add_node("refiner", refiner)
    graph.add_node("bundle_creator", bundle_creator)
    
    # Flujo principal
    graph.set_entry_point("graph_rag_context")
    graph.add_edge("graph_rag_context", "atomic_planner")
    graph.add_edge("atomic_planner", "atomic_generator")
    graph.add_edge("atomic_generator", "epistemic_validator")
    
    # Branching condicional: refinar o finalizar
    graph.add_conditional_edges(
        "epistemic_validator",
        should_refine,
        {
            "refiner": "refiner",
            "bundle_creator": "bundle_creator",
        }
    )
    
    # Loop de refinamiento vuelve al generador
    graph.add_edge("refiner", "atomic_generator")
    
    # Fin
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
    """
    Ejecuta el pipeline completo de Phase 2.
    
    Args:
        lesson_id: ID de la lección (de Phase 1)
        ordered_class_path: Path a la clase ordenada
        phase1_bundle_id: ID del bundle de Phase 1
        human_directives: Directivas humanas (si viene de rechazo)
        
    Returns:
        Resultado final incluyendo el bundle
    """
    # Estado inicial
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
    
    # Ejecutar grafo
    graph = build_phase2_graph()
    result = graph.invoke(initial_state)
    
    return result


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