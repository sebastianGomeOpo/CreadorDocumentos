"""
master_planner.py — El Cerebro

Genera el MasterPlan que gobierna toda la ejecución paralela.
Combina detección de temas + ordenamiento + reglas de contención.

RESPONSABILIDAD:
- Detectar temas en el contenido crudo
- Ordenarlos didácticamente
- Generar reglas must_include/must_exclude por tema
- Detectar riesgos (solapamientos, dependencias)
- Crear mapa de navegación para transiciones

OUTPUT:
MasterPlan.json con todo lo necesario para los Workers

CONEXIONES:
- Input: raw_content + source_metadata
- Output: MasterPlan (serializable)
- Llamado por: phase1_graph.py (nodo master_planner)
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime
from typing import Any, List

from dotenv import load_dotenv
from langchain_core.language_models import BaseChatModel
from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field

from core.state_schema import (
    DetectedRisk,
    MasterPlan,
    NavigationContext,
    RiskLevel,
    TopicDirective,
    generate_plan_id,
)

load_dotenv()


# =============================================================================
# SCHEMAS PARA SALIDA ESTRUCTURADA
# =============================================================================

class TopicDetection(BaseModel):
    """Un tema detectado por el LLM."""
    name: str = Field(description="Nombre conciso del tema")
    description: str = Field(description="Descripción breve del contenido")
    key_concepts: list[str] = Field(description="Conceptos clave a cubrir")
    complexity: str = Field(description="basic, intermediate o advanced")
    estimated_words: int = Field(description="Palabras estimadas para este tema")


class TopicListDetection(BaseModel):
    """Lista de temas detectados."""
    topics: list[TopicDetection]
    overall_summary: str = Field(description="Resumen general del contenido")


class OrderedTopic(BaseModel):
    """Tema con orden y directivas."""
    position: int
    name: str
    rationale: str = Field(description="Por qué va en esta posición")
    must_include: list[str] = Field(description="Conceptos que DEBEN estar en esta sección")
    must_exclude: list[str] = Field(description="Conceptos que NO deben estar (van en otra sección)")
    depends_on: list[str] = Field(description="Temas que deben ir antes")


class OrderedPlan(BaseModel):
    """Plan ordenado completo."""
    topics: list[OrderedTopic]
    detected_overlaps: list[str] = Field(description="Posibles solapamientos detectados")
    detected_gaps: list[str] = Field(description="Posibles vacíos de contenido")


# =============================================================================
# PROMPTS
# =============================================================================

TOPIC_DETECTION_PROMPT = """Eres un experto analizador de contenido educativo.

Analiza el siguiente texto y detecta TODOS los temas principales que contiene.
Para cada tema identifica:
1. Nombre conciso y descriptivo
2. Descripción de qué cubre
3. Conceptos clave específicos
4. Nivel de complejidad
5. Extensión estimada en palabras

IMPORTANTE:
- Detecta temas ESPECÍFICOS, no genéricos como "Introducción" o "Conclusión"
- Cada tema debe ser lo suficientemente sustancial para una sección
- Busca límites naturales entre ideas

TEXTO A ANALIZAR:
{content}
"""

ORDERING_PROMPT = """Eres un arquitecto de planes de estudio experto.

Tienes estos temas detectados:
{topics_json}

Tu tarea es:
1. ORDENARLOS en la mejor secuencia de aprendizaje (de lo simple a lo complejo)
2. Para CADA tema, definir:
   - must_include: conceptos que DEBEN estar en esa sección específica
   - must_exclude: conceptos que NO deben estar (pertenecen a otra sección)
   - depends_on: qué temas deben entenderse antes

REGLAS CRÍTICAS:
- Si un concepto está en must_exclude de un tema, DEBE estar en must_include de otro
- Evita solapamientos: cada concepto principal debe tener UN solo hogar
- Los temas fundamentales van primero

Responde con el plan ordenado y cualquier solapamiento o vacío que detectes.
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
            temperature=0.1,
            api_key=api_key
        )
    except Exception as e:
        print(f"Error inicializando LLM: {e}")
        return None


# =============================================================================
# DETECCIÓN DE TEMAS
# =============================================================================

def detect_topics(content: str, llm: BaseChatModel | None = None) -> list[TopicDetection]:
    """
    Detecta temas en el contenido usando LLM.
    
    Args:
        content: Texto crudo
        llm: Modelo de lenguaje
        
    Returns:
        Lista de TopicDetection
    """
    if not llm:
        return _detect_topics_heuristic(content)
    
    try:
        structured_llm = llm.with_structured_output(TopicListDetection)
        
        prompt = ChatPromptTemplate.from_messages([
            ("system", "Eres un experto en análisis de contenido educativo."),
            ("human", TOPIC_DETECTION_PROMPT)
        ])
        
        chain = prompt | structured_llm
        result = chain.invoke({"content": content[:15000]})
        
        return result.topics
        
    except Exception as e:
        print(f"Error en detección de temas: {e}")
        return _detect_topics_heuristic(content)


def _detect_topics_heuristic(content: str) -> list[TopicDetection]:
    """Fallback heurístico para detección de temas."""
    topics = []
    
    # Buscar headers
    headers = re.findall(r'^#{1,3}\s+(.+)$', content, re.MULTILINE)
    
    if headers:
        for i, header in enumerate(headers[:10]):  # Máximo 10 temas
            topics.append(TopicDetection(
                name=header.strip(),
                description=f"Sección sobre {header}",
                key_concepts=[],
                complexity="intermediate",
                estimated_words=300
            ))
    else:
        # Un solo tema genérico
        topics.append(TopicDetection(
            name="Contenido Principal",
            description="Todo el contenido del documento",
            key_concepts=[],
            complexity="intermediate",
            estimated_words=len(content.split())
        ))
    
    return topics


# =============================================================================
# ORDENAMIENTO Y DIRECTIVAS
# =============================================================================

def create_ordered_plan(
    topics: list[TopicDetection],
    llm: BaseChatModel | None = None,
) -> OrderedPlan:
    """
    Ordena temas y genera directivas de contención.
    
    Args:
        topics: Temas detectados
        llm: Modelo de lenguaje
        
    Returns:
        OrderedPlan con directivas
    """
    if not llm:
        return _order_topics_heuristic(topics)
    
    try:
        # Preparar JSON de topics para el prompt
        topics_json = json.dumps(
            [t.model_dump() for t in topics],
            indent=2,
            ensure_ascii=False
        )
        
        structured_llm = llm.with_structured_output(OrderedPlan)
        
        prompt = ChatPromptTemplate.from_messages([
            ("system", "Eres un arquitecto de planes de estudio."),
            ("human", ORDERING_PROMPT)
        ])
        
        chain = prompt | structured_llm
        result = chain.invoke({"topics_json": topics_json})
        
        return result
        
    except Exception as e:
        print(f"Error en ordenamiento: {e}")
        return _order_topics_heuristic(topics)


def _order_topics_heuristic(topics: list[TopicDetection]) -> OrderedPlan:
    """Fallback heurístico para ordenamiento."""
    ordered = []
    
    for i, topic in enumerate(topics):
        ordered.append(OrderedTopic(
            position=i + 1,
            name=topic.name,
            rationale="Orden original del documento",
            must_include=topic.key_concepts[:3] if topic.key_concepts else [],
            must_exclude=[],
            depends_on=[]
        ))
    
    return OrderedPlan(
        topics=ordered,
        detected_overlaps=[],
        detected_gaps=[]
    )


# =============================================================================
# CONSTRUCCIÓN DEL MASTER PLAN
# =============================================================================

def build_navigation_map(
    topics: list[OrderedTopic],
) -> dict[str, NavigationContext]:
    """
    Construye el mapa de navegación para transiciones.
    
    Args:
        topics: Temas ordenados
        
    Returns:
        Mapa topic_id -> NavigationContext
    """
    nav_map = {}
    total = len(topics)
    
    for i, topic in enumerate(topics):
        topic_id = f"topic_{topic.position:03d}"
        
        prev_topic = topics[i - 1] if i > 0 else None
        next_topic = topics[i + 1] if i < total - 1 else None
        
        nav_map[topic_id] = NavigationContext(
            sequence_id=topic.position,
            total_sections=total,
            previous_topic=prev_topic.name if prev_topic else None,
            previous_summary=f"Cubre: {', '.join(prev_topic.must_include[:2])}" if prev_topic and prev_topic.must_include else None,
            next_topic=next_topic.name if next_topic else None,
            next_summary=f"Introducirá: {', '.join(next_topic.must_include[:2])}" if next_topic and next_topic.must_include else None,
        )
    
    return nav_map


def detect_risks(ordered_plan: OrderedPlan) -> list[DetectedRisk]:
    """
    Detecta riesgos en el plan.
    
    Args:
        ordered_plan: Plan ordenado
        
    Returns:
        Lista de riesgos detectados
    """
    risks = []
    
    # Riesgos de solapamiento
    for overlap in ordered_plan.detected_overlaps:
        risks.append(DetectedRisk(
            risk_type="overlap",
            severity=RiskLevel.MEDIUM,
            description=overlap,
            affected_topics=[],
            suggestion="Revisar límites entre secciones"
        ))
    
    # Riesgos de vacío
    for gap in ordered_plan.detected_gaps:
        risks.append(DetectedRisk(
            risk_type="gap",
            severity=RiskLevel.LOW,
            description=gap,
            affected_topics=[],
            suggestion="Considerar añadir contenido"
        ))
    
    # Detectar dependencias circulares
    dep_graph = {}
    for topic in ordered_plan.topics:
        topic_id = f"topic_{topic.position:03d}"
        dep_graph[topic_id] = topic.depends_on
    
    # Simple check for circular deps
    for topic_id, deps in dep_graph.items():
        for dep in deps:
            if topic_id in dep_graph.get(dep, []):
                risks.append(DetectedRisk(
                    risk_type="dependency",
                    severity=RiskLevel.HIGH,
                    description=f"Dependencia circular entre {topic_id} y {dep}",
                    affected_topics=[topic_id, dep],
                    suggestion="Revisar orden de temas"
                ))
    
    return risks


# =============================================================================
# FUNCIÓN PRINCIPAL
# =============================================================================

def create_master_plan(
    content: str,
    source_id: str,
    llm: BaseChatModel | None = None,
) -> MasterPlan:
    """
    Función principal: crea el MasterPlan completo.
    
    Args:
        content: Texto crudo
        source_id: ID de la fuente
        llm: Modelo de lenguaje (opcional)
        
    Returns:
        MasterPlan listo para usar
    """
    # Obtener LLM si no se proporciona
    if llm is None:
        llm = get_llm()
    
    # 1. Detectar temas
    detected_topics = detect_topics(content, llm)
    
    # 2. Ordenar y generar directivas
    ordered_plan = create_ordered_plan(detected_topics, llm)
    
    # 3. Construir mapa de navegación
    nav_map = build_navigation_map(ordered_plan.topics)
    
    # 4. Detectar riesgos
    risks = detect_risks(ordered_plan)
    
    # 5. Construir TopicDirectives
    topic_directives = []
    total_words = 0
    
    for i, ot in enumerate(ordered_plan.topics):
        topic_id = f"topic_{ot.position:03d}"
        
        # Encontrar topic original para metadata
        original = detected_topics[i] if i < len(detected_topics) else None
        complexity = original.complexity if original else "intermediate"
        est_words = original.estimated_words if original else 300
        
        directive = TopicDirective(
            sequence_id=ot.position,
            topic_id=topic_id,
            topic_name=ot.name,
            description=original.description if original else "",
            must_include=ot.must_include,
            must_exclude=ot.must_exclude,
            key_concepts=original.key_concepts if original else [],
            estimated_word_count=est_words,
            complexity=complexity if complexity in ["basic", "intermediate", "advanced"] else "intermediate",
            chunk_path="",  # Se llenará después por chunk_persister
            navigation=nav_map.get(topic_id),
        )
        topic_directives.append(directive)
        total_words += est_words
    
    # 6. Construir MasterPlan
    plan = MasterPlan(
        plan_id=generate_plan_id(source_id),
        source_id=source_id,
        topics=topic_directives,
        navigation_map=nav_map,
        detected_risks=risks,
        total_estimated_words=total_words,
        planning_rationale=f"Plan con {len(topic_directives)} temas ordenados didácticamente. "
                          f"Detectados {len(risks)} riesgos potenciales."
    )
    
    return plan


# =============================================================================
# FUNCIÓN PARA EL NODO DEL GRAFO
# =============================================================================

def run_master_planner(
    raw_content: str,
    source_id: str,
) -> dict[str, Any]:
    """
    Punto de entrada para el nodo master_planner del grafo.
    
    Args:
        raw_content: Texto crudo
        source_id: ID de la fuente
        
    Returns:
        Diccionario con el plan serializado
    """
    plan = create_master_plan(raw_content, source_id)
    
    return {
        "master_plan": plan.model_dump(),
        "topic_count": plan.topic_count,
        "detected_risks_count": len(plan.detected_risks),
    }