"""
facet_query_planner.py — Planificador de Queries por Facetas

En lugar de una query monolítica ("Variables en Python"),
descompone la búsqueda en FACETAS:

- Una faceta por cada MUST_INCLUDE
- Una faceta para el tema general
- Una faceta para el contexto de navegación

Cada faceta tiene:
- Nombre descriptivo
- Intención (qué buscamos)
- Query embedding optimizado

El retriever luego busca evidencia por faceta,
garantizando cobertura de todos los requisitos.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional
from enum import Enum

from dotenv import load_dotenv

load_dotenv()


# =============================================================================
# ESTRUCTURAS DE DATOS
# =============================================================================

class FacetType(Enum):
    """Tipos de facetas."""
    TOPIC = "topic"              # Tema principal
    MUST_INCLUDE = "must_include"  # Concepto obligatorio
    KEY_CONCEPT = "key_concept"    # Concepto clave
    NAVIGATION = "navigation"      # Contexto de navegación
    EXPANSION = "expansion"        # Expansión generada por LLM


@dataclass
class Facet:
    """Una faceta de búsqueda."""
    facet_id: str
    name: str
    facet_type: FacetType
    intent: str                    # Descripción de qué buscamos
    query_text: str               # Texto para embedding
    query_embedding: Optional[list[float]] = None
    weight: float = 1.0           # Peso relativo
    required: bool = False        # Si es obligatorio encontrar evidencia
    
    def __hash__(self):
        return hash(self.facet_id)


@dataclass
class QueryPlan:
    """Plan de queries con todas las facetas."""
    topic_name: str
    facets: list[Facet]
    topic_embedding: Optional[list[float]] = None
    navigation_context: Optional[dict] = None
    estimated_complexity: str = "medium"  # low, medium, high
    
    @property
    def required_facets(self) -> list[Facet]:
        """Facetas que deben tener cobertura."""
        return [f for f in self.facets if f.required]
    
    @property
    def optional_facets(self) -> list[Facet]:
        """Facetas opcionales."""
        return [f for f in self.facets if not f.required]
    
    def get_facet(self, facet_id: str) -> Optional[Facet]:
        """Obtiene faceta por ID."""
        for f in self.facets:
            if f.facet_id == facet_id:
                return f
        return None


# =============================================================================
# CONFIGURACIÓN
# =============================================================================

# Pesos por tipo de faceta
FACET_WEIGHTS = {
    FacetType.TOPIC: 1.0,
    FacetType.MUST_INCLUDE: 1.5,      # Mayor peso para obligatorios
    FacetType.KEY_CONCEPT: 0.8,
    FacetType.NAVIGATION: 0.5,
    FacetType.EXPANSION: 0.7,
}

# Plantillas de intención por tipo
INTENT_TEMPLATES = {
    FacetType.TOPIC: "Información principal sobre {name}",
    FacetType.MUST_INCLUDE: "Evidencia específica de {name} en el contexto de {topic}",
    FacetType.KEY_CONCEPT: "Explicación o mención de {name}",
    FacetType.NAVIGATION: "Conexión con {name} para transición",
    FacetType.EXPANSION: "Información relacionada con {name}",
}


# =============================================================================
# PLANIFICADOR DE FACETAS
# =============================================================================

class FacetQueryPlanner:
    """
    Planificador que descompone un tema en facetas de búsqueda.
    
    Estrategia:
    1. Crear faceta para el tema principal
    2. Crear faceta para cada MUST_INCLUDE (required=True)
    3. Crear facetas para key_concepts más relevantes
    4. Opcionalmente: usar LLM para expandir queries
    """
    
    def __init__(
        self,
        use_llm_expansion: bool = True,
        max_key_concept_facets: int = 3,
    ):
        self.use_llm_expansion = use_llm_expansion
        self.max_key_concept_facets = max_key_concept_facets
        self._embedder = None
        self._llm = None
    
    @property
    def embedder(self):
        """Lazy loading del embedder."""
        if self._embedder is None:
            from langchain_openai import OpenAIEmbeddings
            self._embedder = OpenAIEmbeddings(
                model="text-embedding-3-small",
                api_key=os.getenv("OPENAI_API_KEY"),
            )
        return self._embedder
    
    @property
    def llm(self):
        """Lazy loading del LLM."""
        if self._llm is None and self.use_llm_expansion:
            try:
                from langchain_openai import ChatOpenAI
                self._llm = ChatOpenAI(
                    model=os.getenv("DEFAULT_LLM_MODEL", "gpt-4o-mini"),
                    temperature=0.3,
                    api_key=os.getenv("OPENAI_API_KEY"),
                )
            except Exception:
                self._llm = None
        return self._llm
    
    def create_plan(
        self,
        topic_name: str,
        must_include: list[str],
        key_concepts: list[str],
        navigation_context: Optional[dict] = None,
        must_exclude: Optional[list[str]] = None,
    ) -> QueryPlan:
        """
        Crea un plan de queries con facetas.
        
        Args:
            topic_name: Nombre del tema
            must_include: Conceptos obligatorios
            key_concepts: Conceptos clave
            navigation_context: Contexto de navegación
            must_exclude: Conceptos a evitar (no se crean facetas, pero informan)
            
        Returns:
            QueryPlan con facetas y embeddings
        """
        facets = []
        facet_counter = 0
        
        # 1. Faceta del tema principal
        topic_facet = self._create_facet(
            facet_id=f"facet_{facet_counter:03d}",
            name=topic_name,
            facet_type=FacetType.TOPIC,
            topic=topic_name,
            required=True,
        )
        facets.append(topic_facet)
        facet_counter += 1
        
        # 2. Facetas para MUST_INCLUDE (required=True)
        for concept in must_include:
            facet = self._create_facet(
                facet_id=f"facet_{facet_counter:03d}",
                name=concept,
                facet_type=FacetType.MUST_INCLUDE,
                topic=topic_name,
                required=True,
            )
            facets.append(facet)
            facet_counter += 1
        
        # 3. Facetas para key_concepts más relevantes
        for concept in key_concepts[:self.max_key_concept_facets]:
            # Evitar duplicados con must_include
            if concept.lower() in [m.lower() for m in must_include]:
                continue
            
            facet = self._create_facet(
                facet_id=f"facet_{facet_counter:03d}",
                name=concept,
                facet_type=FacetType.KEY_CONCEPT,
                topic=topic_name,
                required=False,
            )
            facets.append(facet)
            facet_counter += 1
        
        # 4. Faceta de navegación (si hay contexto)
        if navigation_context:
            nav_facet = self._create_navigation_facet(
                facet_id=f"facet_{facet_counter:03d}",
                navigation_context=navigation_context,
                topic=topic_name,
            )
            if nav_facet:
                facets.append(nav_facet)
                facet_counter += 1
        
        # 5. Expansión con LLM (opcional)
        if self.use_llm_expansion and self.llm:
            expansion_facets = self._generate_expansion_facets(
                topic_name=topic_name,
                must_include=must_include,
                existing_facets=facets,
                start_id=facet_counter,
            )
            facets.extend(expansion_facets)
        
        # 6. Generar embeddings para todas las facetas
        self._embed_facets(facets)
        
        # 7. Estimar complejidad
        complexity = self._estimate_complexity(
            topic_name, must_include, key_concepts
        )
        
        # 8. Crear plan
        plan = QueryPlan(
            topic_name=topic_name,
            facets=facets,
            navigation_context=navigation_context,
            estimated_complexity=complexity,
        )
        
        # Embedding del tema completo
        plan.topic_embedding = self.embedder.embed_query(topic_name)
        
        return plan
    
    def _create_facet(
        self,
        facet_id: str,
        name: str,
        facet_type: FacetType,
        topic: str,
        required: bool = False,
    ) -> Facet:
        """Crea una faceta con su query optimizada."""
        intent = INTENT_TEMPLATES[facet_type].format(name=name, topic=topic)
        
        # Construir query text optimizado
        if facet_type == FacetType.TOPIC:
            query_text = f"{name} explicación definición concepto"
        elif facet_type == FacetType.MUST_INCLUDE:
            query_text = f"{name} {topic} detalle ejemplo"
        elif facet_type == FacetType.KEY_CONCEPT:
            query_text = f"{name} significado uso"
        else:
            query_text = name
        
        return Facet(
            facet_id=facet_id,
            name=name,
            facet_type=facet_type,
            intent=intent,
            query_text=query_text,
            weight=FACET_WEIGHTS[facet_type],
            required=required,
        )
    
    def _create_navigation_facet(
        self,
        facet_id: str,
        navigation_context: dict,
        topic: str,
    ) -> Optional[Facet]:
        """Crea faceta de navegación si hay contexto relevante."""
        prev_topic = navigation_context.get("previous_topic")
        next_topic = navigation_context.get("next_topic")
        
        if not prev_topic and not next_topic:
            return None
        
        # Construir query para transición
        if prev_topic and next_topic:
            name = f"transición {prev_topic} a {next_topic}"
            query_text = f"{prev_topic} {topic} {next_topic} relación conexión"
        elif prev_topic:
            name = f"continuación de {prev_topic}"
            query_text = f"{prev_topic} {topic} continuación siguiente"
        else:
            name = f"introducción a {next_topic}"
            query_text = f"{topic} {next_topic} introducción preparación"
        
        return Facet(
            facet_id=facet_id,
            name=name,
            facet_type=FacetType.NAVIGATION,
            intent=f"Contexto de transición para {topic}",
            query_text=query_text,
            weight=FACET_WEIGHTS[FacetType.NAVIGATION],
            required=False,
        )
    
    def _generate_expansion_facets(
        self,
        topic_name: str,
        must_include: list[str],
        existing_facets: list[Facet],
        start_id: int,
        max_expansions: int = 2,
    ) -> list[Facet]:
        """
        Usa LLM para generar facetas de expansión inteligentes.
        
        El LLM identifica términos de búsqueda adicionales que
        podrían ayudar a encontrar información relevante.
        """
        if not self.llm:
            return []
        
        try:
            from langchain_core.messages import HumanMessage, SystemMessage
            
            system_prompt = """Eres un experto en búsqueda de información.
Dado un tema y conceptos obligatorios, genera 2-3 términos de búsqueda adicionales
que ayudarían a encontrar información relevante que podría no encontrarse
con los términos originales.

Responde SOLO con los términos, uno por línea, sin explicaciones."""

            user_prompt = f"""Tema: {topic_name}
Conceptos obligatorios: {', '.join(must_include)}

Genera términos de búsqueda complementarios:"""

            messages = [
                SystemMessage(content=system_prompt),
                HumanMessage(content=user_prompt),
            ]
            
            response = self.llm.invoke(messages)
            
            # Parsear respuesta
            lines = response.content.strip().split("\n")
            expansion_terms = [
                line.strip().strip("-•").strip()
                for line in lines
                if line.strip() and len(line.strip()) > 2
            ][:max_expansions]
            
            # Crear facetas de expansión
            facets = []
            for i, term in enumerate(expansion_terms):
                # Evitar duplicados
                existing_names = [f.name.lower() for f in existing_facets]
                if term.lower() in existing_names:
                    continue
                
                facet = Facet(
                    facet_id=f"facet_{start_id + i:03d}",
                    name=term,
                    facet_type=FacetType.EXPANSION,
                    intent=f"Información complementaria sobre {term}",
                    query_text=f"{term} {topic_name}",
                    weight=FACET_WEIGHTS[FacetType.EXPANSION],
                    required=False,
                )
                facets.append(facet)
            
            return facets
            
        except Exception as e:
            # Si falla el LLM, continuar sin expansión
            print(f"Warning: LLM expansion failed: {e}")
            return []
    
    def _embed_facets(self, facets: list[Facet]) -> None:
        """Genera embeddings para todas las facetas."""
        if not facets:
            return
        
        texts = [f.query_text for f in facets]
        embeddings = self.embedder.embed_documents(texts)
        
        for i, facet in enumerate(facets):
            if i < len(embeddings):
                facet.query_embedding = embeddings[i]
    
    def _estimate_complexity(
        self,
        topic_name: str,
        must_include: list[str],
        key_concepts: list[str],
    ) -> str:
        """
        Estima la complejidad del tema para ajustar k.
        
        Returns:
            "low", "medium", o "high"
        """
        # Factores de complejidad
        num_requirements = len(must_include)
        num_concepts = len(key_concepts)
        topic_length = len(topic_name.split())
        
        score = 0
        
        # Más requisitos = más complejo
        if num_requirements >= 5:
            score += 2
        elif num_requirements >= 3:
            score += 1
        
        # Más conceptos = más complejo
        if num_concepts >= 6:
            score += 2
        elif num_concepts >= 3:
            score += 1
        
        # Tema largo = más complejo
        if topic_length >= 5:
            score += 1
        
        if score >= 4:
            return "high"
        elif score >= 2:
            return "medium"
        else:
            return "low"


# =============================================================================
# FUNCIONES DE CONVENIENCIA
# =============================================================================

def create_query_plan(
    topic_name: str,
    must_include: list[str],
    key_concepts: list[str] = None,
    navigation_context: dict = None,
    use_llm_expansion: bool = True,
) -> QueryPlan:
    """
    Función de conveniencia para crear un plan de queries.
    
    Args:
        topic_name: Nombre del tema
        must_include: Conceptos obligatorios
        key_concepts: Conceptos clave
        navigation_context: Contexto de navegación
        use_llm_expansion: Si usar LLM para expandir
        
    Returns:
        QueryPlan con facetas embedidas
    """
    planner = FacetQueryPlanner(use_llm_expansion=use_llm_expansion)
    return planner.create_plan(
        topic_name=topic_name,
        must_include=must_include,
        key_concepts=key_concepts or [],
        navigation_context=navigation_context,
    )


def get_recommended_k(complexity: str) -> dict[str, int]:
    """
    Retorna k recomendado por nivel de búsqueda según complejidad.
    
    Returns:
        Dict con k para chunks y blocks
    """
    k_map = {
        "low": {"chunks": 8, "blocks": 2},
        "medium": {"chunks": 15, "blocks": 4},
        "high": {"chunks": 25, "blocks": 6},
    }
    return k_map.get(complexity, k_map["medium"])