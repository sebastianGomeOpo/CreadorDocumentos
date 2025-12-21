"""
atomic_planner.py — Planificador de Notas Atómicas

Este módulo decide CUÁNTAS notas atómicas generar y POR QUÉ,
usando señales del GraphRAG para tomar decisiones inteligentes.

RESPONSABILIDAD:
Analizar el contenido de la clase ordenada junto con el contexto
del knowledge graph existente para crear un plan de atomización.

SEÑALES QUE USA:
- Novedad vs duplicado (¿ya existe algo similar?)
- Densidad conceptual por tema
- Conectabilidad al grafo (evitar notas huérfanas)
- Conflicto/contraste con conocimiento previo
- Impacto en MOCs

CONEXIONES:
- Llamado por: phase2_graph.py (nodo atomic_planner)
- Input de: graph_rag_builder.py (contexto)
- Output usado por: atomic_generator.py
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.language_models import BaseChatModel


# =============================================================================
# PROMPTS
# =============================================================================

ATOMIC_PLANNER_SYSTEM_PROMPT = """Eres un experto en metodología Zettelkasten y gestión del conocimiento. Tu tarea es crear un PLAN de atomización para convertir una clase ordenada en notas atómicas.

PRINCIPIOS ZETTELKASTEN:
1. Una nota = Una idea (atomicidad)
2. Las notas deben ser auto-contenidas
3. Las conexiones son tan importantes como el contenido
4. Evitar duplicación - preferir enlaces a notas existentes
5. Cada nota debe aportar valor único

CRITERIOS DE DECISIÓN:
- NOVEDAD: ¿El concepto es nuevo o ya existe algo similar?
- DENSIDAD: ¿Cuántas ideas distintas hay en cada sección?
- CONECTABILIDAD: ¿Se puede conectar con el conocimiento existente?
- CONFLICTO: ¿Contradice o matiza algo existente?
- IMPACTO MOC: ¿Afecta a algún Map of Content?

FORMATO DE SALIDA (JSON):
{
  "plan_summary": "Resumen del plan en 1-2 oraciones",
  "total_notes_proposed": N,
  "notes": [
    {
      "proposed_title": "Título propuesto para la nota",
      "topic_id": "ID del tema de la clase",
      "rationale": "Por qué esta nota vale la pena",
      "novelty_score": 0.0-1.0,
      "estimated_connections": N,
      "priority": "high|medium|low",
      "type": "concept|example|application|contrast|synthesis"
    }
  ],
  "skipped_content": [
    {
      "description": "Contenido que NO se convierte en nota",
      "reason": "Por qué se omite (duplicado, trivial, etc.)"
    }
  ],
  "moc_impact": ["Lista de MOCs que podrían actualizarse"]
}"""

ATOMIC_PLANNER_USER_PROMPT = """## Clase Ordenada a Atomizar:

{ordered_class}

## Contexto del Knowledge Graph:

### Notas similares existentes:
{similar_notes}

### Conceptos relacionados en el grafo:
{graph_context}

## Instrucciones:
Crea un plan de atomización considerando el contexto existente.
- Propón solo notas que aporten valor nuevo
- Indica claramente qué contenido se omite y por qué
- Asigna scores de novedad realistas

Responde SOLO con el JSON."""


# =============================================================================
# MODELOS DE DATOS
# =============================================================================

@dataclass
class NotePlan:
    """Plan para una nota atómica individual."""
    proposed_title: str
    topic_id: str
    rationale: str
    novelty_score: float
    estimated_connections: int
    priority: str = "medium"
    note_type: str = "concept"
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "proposed_title": self.proposed_title,
            "topic_id": self.topic_id,
            "rationale": self.rationale,
            "novelty_score": self.novelty_score,
            "estimated_connections": self.estimated_connections,
            "priority": self.priority,
            "type": self.note_type,
        }


@dataclass
class SkippedContent:
    """Contenido que se decidió no atomizar."""
    description: str
    reason: str


@dataclass
class AtomicPlan:
    """Plan completo de atomización."""
    summary: str
    notes: list[NotePlan] = field(default_factory=list)
    skipped: list[SkippedContent] = field(default_factory=list)
    moc_impact: list[str] = field(default_factory=list)
    
    @property
    def total_notes(self) -> int:
        return len(self.notes)
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_summary": self.summary,
            "total_notes_proposed": self.total_notes,
            "notes": [n.to_dict() for n in self.notes],
            "skipped_content": [
                {"description": s.description, "reason": s.reason}
                for s in self.skipped
            ],
            "moc_impact": self.moc_impact,
        }


# =============================================================================
# PLANIFICACIÓN HEURÍSTICA (sin LLM)
# =============================================================================

def plan_atomic_notes_heuristic(
    ordered_class: str,
    topics: list[dict[str, Any]],
    similar_notes: list[str] | None = None,
    graph_context: dict[str, Any] | None = None,
) -> AtomicPlan:
    """
    Crea un plan de atomización usando heurísticas simples.
    
    Estrategia:
    1. Una nota por cada tema principal
    2. Notas adicionales para secciones densas
    3. Notas de ejemplo si hay casos concretos
    
    Args:
        ordered_class: Markdown de la clase ordenada
        topics: Lista de temas detectados en Phase 1
        similar_notes: IDs de notas similares existentes
        graph_context: Contexto del grafo (opcional)
        
    Returns:
        Plan de atomización
    """
    similar_notes = similar_notes or []
    notes_plan: list[NotePlan] = []
    skipped: list[SkippedContent] = []
    
    # Analizar la clase por secciones (headers)
    sections = re.split(r'\n##\s+', ordered_class)
    
    for i, section in enumerate(sections):
        if not section.strip():
            continue
        
        # Extraer título de sección
        lines = section.strip().split('\n')
        section_title = lines[0].strip().lstrip('#').strip()
        section_content = '\n'.join(lines[1:]) if len(lines) > 1 else ""
        
        # Métricas de la sección
        word_count = len(section_content.split())
        has_examples = bool(re.search(r'ejemplo|por ejemplo|e\.g\.|case|caso', section_content, re.I))
        has_definition = bool(re.search(r'se define|es un|significa|consiste en', section_content, re.I))
        has_list = bool(re.search(r'^\s*[-*]\s+', section_content, re.MULTILINE))
        
        # Buscar topic correspondiente
        topic_id = f"topic_{i+1:03d}"
        for topic in topics:
            if topic["name"].lower() in section_title.lower():
                topic_id = topic["id"]
                break
        
        # Decidir si crear nota
        # Umbral: secciones con más de 50 palabras
        if word_count < 50:
            skipped.append(SkippedContent(
                description=section_title,
                reason=f"Sección muy corta ({word_count} palabras)"
            ))
            continue
        
        # Calcular novedad (simplificado)
        novelty = 0.9  # Base alta
        title_lower = section_title.lower()
        
        for existing_note in similar_notes:
            if title_lower in existing_note.lower() or existing_note.lower() in title_lower:
                novelty = 0.3  # Probable duplicado
                break
        
        # Nota principal del concepto
        notes_plan.append(NotePlan(
            proposed_title=section_title,
            topic_id=topic_id,
            rationale=f"Concepto central de la sección ({word_count} palabras)",
            novelty_score=novelty,
            estimated_connections=2 if novelty > 0.5 else 1,
            priority="high" if has_definition else "medium",
            note_type="concept" if has_definition else "synthesis",
        ))
        
        # Nota adicional para ejemplos (si los hay y la sección es densa)
        if has_examples and word_count > 150:
            notes_plan.append(NotePlan(
                proposed_title=f"Ejemplo: {section_title}",
                topic_id=topic_id,
                rationale="Ejemplo concreto que ilustra el concepto",
                novelty_score=novelty * 0.9,
                estimated_connections=1,
                priority="medium",
                note_type="example",
            ))
        
        # Nota de aplicación si hay lista de pasos/procedimientos
        if has_list and word_count > 200:
            notes_plan.append(NotePlan(
                proposed_title=f"Aplicación: {section_title}",
                topic_id=topic_id,
                rationale="Procedimiento o aplicación práctica",
                novelty_score=novelty * 0.85,
                estimated_connections=2,
                priority="low",
                note_type="application",
            ))
    
    # Si no se generaron notas, crear al menos una
    if not notes_plan and topics:
        main_topic = topics[0]
        notes_plan.append(NotePlan(
            proposed_title=main_topic["name"],
            topic_id=main_topic["id"],
            rationale="Nota principal del contenido",
            novelty_score=0.8,
            estimated_connections=1,
            priority="high",
            note_type="concept",
        ))
    
    # Detectar MOCs afectados
    moc_impact = []
    for note in notes_plan:
        # Heurística simple: topics con "introducción" o "fundamentos" sugieren MOC
        if any(kw in note.proposed_title.lower() for kw in ["introducción", "fundamentos", "básico", "overview"]):
            moc_impact.append(f"MOC_{note.topic_id}")
    
    # Generar resumen
    high_priority = sum(1 for n in notes_plan if n.priority == "high")
    summary = f"Plan: {len(notes_plan)} notas ({high_priority} prioritarias), {len(skipped)} secciones omitidas"
    
    return AtomicPlan(
        summary=summary,
        notes=notes_plan,
        skipped=skipped,
        moc_impact=list(set(moc_impact)),
    )


# =============================================================================
# PLANIFICACIÓN CON LLM
# =============================================================================

async def plan_atomic_notes_llm(
    ordered_class: str,
    similar_notes: list[dict[str, Any]],
    graph_context: dict[str, Any],
    llm: BaseChatModel,
) -> AtomicPlan:
    """
    Crea un plan de atomización usando un LLM.
    
    Args:
        ordered_class: Markdown de la clase ordenada
        similar_notes: Notas similares existentes
        graph_context: Contexto del grafo
        llm: Modelo de lenguaje
        
    Returns:
        Plan de atomización
    """
    import json
    
    # Formatear contexto
    similar_notes_str = "\n".join([
        f"- {n.get('title', n.get('id', 'Sin título'))}: {n.get('summary', '')[:100]}"
        for n in similar_notes[:10]
    ]) or "No hay notas similares existentes."
    
    graph_context_str = json.dumps(graph_context, indent=2, ensure_ascii=False)[:2000]
    
    # Construir mensajes
    messages = [
        SystemMessage(content=ATOMIC_PLANNER_SYSTEM_PROMPT),
        HumanMessage(content=ATOMIC_PLANNER_USER_PROMPT.format(
            ordered_class=ordered_class[:10000],  # Limitar tamaño
            similar_notes=similar_notes_str,
            graph_context=graph_context_str,
        )),
    ]
    
    # Invocar LLM
    response = await llm.ainvoke(messages)
    response_text = response.content
    
    # Parsear JSON
    json_match = re.search(r'```(?:json)?\s*([\s\S]*?)\s*```', response_text)
    if json_match:
        json_str = json_match.group(1)
    else:
        json_str = response_text
    
    try:
        data = json.loads(json_str)
    except json.JSONDecodeError:
        # Fallback a heurísticas
        return plan_atomic_notes_heuristic(ordered_class, [])
    
    # Convertir a AtomicPlan
    notes = []
    for note_data in data.get("notes", []):
        notes.append(NotePlan(
            proposed_title=note_data.get("proposed_title", "Sin título"),
            topic_id=note_data.get("topic_id", "topic_001"),
            rationale=note_data.get("rationale", ""),
            novelty_score=float(note_data.get("novelty_score", 0.5)),
            estimated_connections=int(note_data.get("estimated_connections", 1)),
            priority=note_data.get("priority", "medium"),
            note_type=note_data.get("type", "concept"),
        ))
    
    skipped = []
    for skip_data in data.get("skipped_content", []):
        skipped.append(SkippedContent(
            description=skip_data.get("description", ""),
            reason=skip_data.get("reason", ""),
        ))
    
    return AtomicPlan(
        summary=data.get("plan_summary", "Plan generado por LLM"),
        notes=notes,
        skipped=skipped,
        moc_impact=data.get("moc_impact", []),
    )


# =============================================================================
# FUNCIÓN PRINCIPAL
# =============================================================================

def create_atomic_plan(
    ordered_class: str,
    topics: list[dict[str, Any]],
    graph_rag_context: dict[str, Any] | None = None,
    llm: BaseChatModel | None = None,
) -> dict[str, Any]:
    """
    Función principal para crear plan de atomización.
    
    Args:
        ordered_class: Markdown de la clase ordenada
        topics: Temas de Phase 1
        graph_rag_context: Contexto del GraphRAG
        llm: Modelo de lenguaje (opcional)
        
    Returns:
        Plan como diccionario (formato para state)
    """
    context = graph_rag_context or {}
    similar_notes = context.get("similar_notes", [])
    
    # Por ahora usar heurísticas (async requiere event loop)
    plan = plan_atomic_notes_heuristic(
        ordered_class=ordered_class,
        topics=topics,
        similar_notes=similar_notes,
        graph_context=context,
    )
    
    # Convertir notas a formato esperado por el state
    atomic_plan = []
    for note in plan.notes:
        atomic_plan.append({
            "id": f"plan_{note.topic_id}_{len(atomic_plan)+1:03d}",
            "topic_id": note.topic_id,
            "proposed_title": note.proposed_title,
            "rationale": note.rationale,
            "novelty_score": note.novelty_score,
            "estimated_connections": note.estimated_connections,
        })
    
    return {
        "atomic_plan": atomic_plan,
        "plan_rationale": plan.summary,
        "_plan_meta": {
            "skipped": [{"description": s.description, "reason": s.reason} for s in plan.skipped],
            "moc_impact": plan.moc_impact,
        }
    }