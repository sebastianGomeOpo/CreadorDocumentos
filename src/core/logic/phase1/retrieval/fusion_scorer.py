"""
fusion_scorer.py — Scoring Continuo Centrado en Utilidad

Combina candidatos y asigna un score único que refleja:

1. RELEVANCIA A FACETAS: ¿Qué tan bien responde a obligaciones?
2. COHERENCIA DE SECCIÓN: ¿Está alineado con el scope?
3. REDUNDANCIA: Penaliza duplicados para diversidad

Score(chunk) = Relevancia + Coherencia - Redundancia

Todo es suave, vectorial y continuo.
No hay listas negras ni filtros binarios.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional
import numpy as np

from dotenv import load_dotenv

load_dotenv()


# =============================================================================
# ESTRUCTURAS DE DATOS
# =============================================================================

@dataclass
class ScoredCandidate:
    """Candidato con scores detallados."""
    chunk_id: str
    content: str
    metadata: dict
    
    # Scores por componente
    relevance_score: float = 0.0        # Relevancia a facetas
    coherence_score: float = 0.0        # Coherencia con sección
    redundancy_penalty: float = 0.0     # Penalización por duplicados
    
    # Score final unificado
    final_score: float = 0.0
    
    # Detalle de relevancia por faceta
    facet_scores: dict[str, float] = field(default_factory=dict)
    
    # Faceta principal que cubre
    primary_facet_id: Optional[str] = None
    primary_facet_name: Optional[str] = None
    
    # Scores heredados del retriever
    dense_score: float = 0.0
    sparse_score: float = 0.0
    parent_score: float = 0.0
    combined_retrieval_score: float = 0.0
    
    def __hash__(self):
        return hash(self.chunk_id)


@dataclass 
class ScoringResult:
    """Resultado del scoring con estadísticas."""
    candidates: list[ScoredCandidate]
    facet_coverage: dict[str, float]  # facet_id → max_score
    diversity_score: float
    avg_relevance: float
    avg_coherence: float


# =============================================================================
# CONFIGURACIÓN
# =============================================================================

# Pesos para score final
WEIGHT_RELEVANCE = 0.5
WEIGHT_COHERENCE = 0.3
WEIGHT_RETRIEVAL = 0.2  # Score original del retriever

# Parámetros de redundancia (MMR-style)
REDUNDANCY_LAMBDA = 0.7  # Balance entre relevancia y diversidad
SIMILARITY_THRESHOLD = 0.85  # Umbral para considerar redundante


# =============================================================================
# FUNCIONES DE UTILIDAD
# =============================================================================

def cosine_similarity(vec1: list[float], vec2: list[float]) -> float:
    """Calcula similitud coseno entre dos vectores."""
    a = np.array(vec1)
    b = np.array(vec2)
    
    norm_a = np.linalg.norm(a)
    norm_b = np.linalg.norm(b)
    
    if norm_a == 0 or norm_b == 0:
        return 0.0
    
    return float(np.dot(a, b) / (norm_a * norm_b))


def text_similarity(text1: str, text2: str) -> float:
    """
    Similitud simple entre textos basada en overlap de palabras.
    Para redundancia aproximada sin embeddings.
    """
    words1 = set(text1.lower().split())
    words2 = set(text2.lower().split())
    
    if not words1 or not words2:
        return 0.0
    
    intersection = len(words1 & words2)
    union = len(words1 | words2)
    
    return intersection / union if union > 0 else 0.0


# =============================================================================
# FUSION SCORER
# =============================================================================

class FusionScorer:
    """
    Scorer que combina múltiples señales en un score unificado.
    
    Componentes:
    1. Relevancia: Similitud con facetas obligatorias
    2. Coherencia: Alineación con scope de la sección
    3. Redundancia: Penalización MMR-style
    
    El score final balancea utilidad y diversidad.
    """
    
    def __init__(
        self,
        weight_relevance: float = WEIGHT_RELEVANCE,
        weight_coherence: float = WEIGHT_COHERENCE,
        weight_retrieval: float = WEIGHT_RETRIEVAL,
        redundancy_lambda: float = REDUNDANCY_LAMBDA,
    ):
        self.weight_relevance = weight_relevance
        self.weight_coherence = weight_coherence
        self.weight_retrieval = weight_retrieval
        self.redundancy_lambda = redundancy_lambda
        
        self._embedder = None
    
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
    
    def score_candidates(
        self,
        candidates: list,  # list[RetrievalCandidate]
        query_plan,        # QueryPlan
        section_context: Optional[dict] = None,
    ) -> ScoringResult:
        """
        Calcula scores para todos los candidatos.
        
        Args:
            candidates: Candidatos del retriever
            query_plan: Plan con facetas
            section_context: Contexto de la sección (tema, navegación)
            
        Returns:
            ScoringResult con candidatos ordenados
        """
        if not candidates:
            return ScoringResult(
                candidates=[],
                facet_coverage={},
                diversity_score=0.0,
                avg_relevance=0.0,
                avg_coherence=0.0,
            )
        
        # 1. Convertir a ScoredCandidate y heredar scores del retriever
        scored = self._convert_candidates(candidates)
        
        # 2. Calcular relevancia a facetas
        self._compute_relevance_scores(scored, query_plan)
        
        # 3. Calcular coherencia con sección
        self._compute_coherence_scores(scored, query_plan, section_context)
        
        # 4. Calcular redundancia (MMR-style)
        self._compute_redundancy_penalties(scored)
        
        # 5. Calcular score final
        self._compute_final_scores(scored)
        
        # 6. Ordenar por score final
        scored.sort(key=lambda c: c.final_score, reverse=True)
        
        # 7. Calcular estadísticas
        facet_coverage = self._compute_facet_coverage(scored, query_plan)
        diversity = self._compute_diversity_score(scored)
        avg_relevance = np.mean([c.relevance_score for c in scored])
        avg_coherence = np.mean([c.coherence_score for c in scored])
        
        return ScoringResult(
            candidates=scored,
            facet_coverage=facet_coverage,
            diversity_score=diversity,
            avg_relevance=float(avg_relevance),
            avg_coherence=float(avg_coherence),
        )
    
    def _convert_candidates(
        self,
        candidates: list,
    ) -> list[ScoredCandidate]:
        """Convierte RetrievalCandidate a ScoredCandidate."""
        scored = []
        for c in candidates:
            sc = ScoredCandidate(
                chunk_id=c.chunk_id,
                content=c.content,
                metadata=c.metadata,
                dense_score=c.dense_score,
                sparse_score=c.sparse_score,
                parent_score=c.parent_score,
                combined_retrieval_score=c.combined_score,
                primary_facet_id=c.facet_id,
                primary_facet_name=c.facet_name,
            )
            scored.append(sc)
        return scored
    
    def _compute_relevance_scores(
        self,
        candidates: list[ScoredCandidate],
        query_plan,
    ) -> None:
        """
        Calcula relevancia de cada candidato a las facetas.
        
        Para cada candidato, calcula similitud con cada faceta
        y usa el máximo como score de relevancia.
        """
        if not candidates or not query_plan.facets:
            return
        
        # Generar embeddings de contenido de candidatos
        contents = [c.content for c in candidates]
        try:
            content_embeddings = self.embedder.embed_documents(contents)
        except Exception as e:
            print(f"Warning: Could not compute relevance embeddings: {e}")
            # Fallback: usar scores del retriever
            for c in candidates:
                c.relevance_score = c.combined_retrieval_score
            return
        
        # Calcular similitud con cada faceta
        for i, candidate in enumerate(candidates):
            if i >= len(content_embeddings):
                continue
                
            content_emb = content_embeddings[i]
            facet_scores = {}
            
            for facet in query_plan.facets:
                if facet.query_embedding:
                    sim = cosine_similarity(content_emb, facet.query_embedding)
                    # Aplicar peso de la faceta
                    weighted_sim = sim * facet.weight
                    facet_scores[facet.facet_id] = weighted_sim
            
            candidate.facet_scores = facet_scores
            
            # Relevancia = máximo score ponderado por required
            if facet_scores:
                required_scores = [
                    facet_scores.get(f.facet_id, 0)
                    for f in query_plan.required_facets
                    if f.facet_id in facet_scores
                ]
                optional_scores = [
                    facet_scores.get(f.facet_id, 0)
                    for f in query_plan.optional_facets
                    if f.facet_id in facet_scores
                ]
                
                # Priorizar facetas required
                if required_scores:
                    candidate.relevance_score = max(required_scores)
                elif optional_scores:
                    candidate.relevance_score = max(optional_scores) * 0.8
                else:
                    candidate.relevance_score = 0.0
            
            # Identificar faceta principal
            if facet_scores:
                best_facet_id = max(facet_scores, key=facet_scores.get)
                candidate.primary_facet_id = best_facet_id
                best_facet = query_plan.get_facet(best_facet_id)
                if best_facet:
                    candidate.primary_facet_name = best_facet.name
    
    def _compute_coherence_scores(
        self,
        candidates: list[ScoredCandidate],
        query_plan,
        section_context: Optional[dict],
    ) -> None:
        """
        Calcula coherencia de cada candidato con el scope de la sección.
        
        Considera:
        - Alineación con el tema principal
        - Compatibilidad con navegación (prev/next)
        - Tipo de bloque compatible
        """
        if not candidates:
            return
        
        # Si no hay contexto, usar similitud con tema
        if not section_context:
            topic_embedding = query_plan.topic_embedding
            if not topic_embedding:
                for c in candidates:
                    c.coherence_score = 0.5  # Neutral
                return
            
            contents = [c.content for c in candidates]
            try:
                content_embeddings = self.embedder.embed_documents(contents)
            except Exception:
                for c in candidates:
                    c.coherence_score = 0.5
                return
            
            for i, candidate in enumerate(candidates):
                if i < len(content_embeddings):
                    candidate.coherence_score = cosine_similarity(
                        content_embeddings[i],
                        topic_embedding
                    )
            return
        
        # Con contexto de sección
        topic_name = section_context.get("topic_name", "")
        position = section_context.get("position", 0)
        total_sections = section_context.get("total_sections", 1)
        prev_topic = section_context.get("prev_topic")
        next_topic = section_context.get("next_topic")
        
        # Construir query de coherencia
        coherence_query = topic_name
        if position == 0 and next_topic:
            coherence_query += f" introducción {next_topic}"
        elif position == total_sections - 1 and prev_topic:
            coherence_query += f" conclusión {prev_topic}"
        elif prev_topic and next_topic:
            coherence_query += f" {prev_topic} {next_topic}"
        
        try:
            coherence_embedding = self.embedder.embed_query(coherence_query)
            contents = [c.content for c in candidates]
            content_embeddings = self.embedder.embed_documents(contents)
            
            for i, candidate in enumerate(candidates):
                if i < len(content_embeddings):
                    candidate.coherence_score = cosine_similarity(
                        content_embeddings[i],
                        coherence_embedding
                    )
        except Exception:
            for c in candidates:
                c.coherence_score = 0.5
    
    def _compute_redundancy_penalties(
        self,
        candidates: list[ScoredCandidate],
    ) -> None:
        """
        Calcula penalización por redundancia usando MMR-style.
        
        Para cada candidato, penaliza si es muy similar a uno
        ya seleccionado con mejor score.
        """
        if len(candidates) <= 1:
            return
        
        # Ordenar temporalmente por score combinado
        sorted_candidates = sorted(
            candidates,
            key=lambda c: c.relevance_score + c.coherence_score,
            reverse=True
        )
        
        # El primero no tiene penalización
        sorted_candidates[0].redundancy_penalty = 0.0
        
        # Para cada candidato, calcular máxima similitud con anteriores
        for i in range(1, len(sorted_candidates)):
            current = sorted_candidates[i]
            max_similarity = 0.0
            
            for j in range(i):
                previous = sorted_candidates[j]
                sim = text_similarity(current.content, previous.content)
                max_similarity = max(max_similarity, sim)
            
            # Penalización proporcional a la similitud
            if max_similarity > SIMILARITY_THRESHOLD:
                current.redundancy_penalty = (max_similarity - SIMILARITY_THRESHOLD) / (1 - SIMILARITY_THRESHOLD)
            else:
                current.redundancy_penalty = 0.0
    
    def _compute_final_scores(
        self,
        candidates: list[ScoredCandidate],
    ) -> None:
        """
        Calcula score final unificado.
        
        Score = (w_rel * relevance + w_coh * coherence + w_ret * retrieval) * (1 - penalty)
        """
        for c in candidates:
            base_score = (
                self.weight_relevance * c.relevance_score +
                self.weight_coherence * c.coherence_score +
                self.weight_retrieval * c.combined_retrieval_score
            )
            
            # Aplicar penalización por redundancia
            penalty_factor = 1.0 - (self.redundancy_lambda * c.redundancy_penalty)
            
            c.final_score = base_score * penalty_factor
    
    def _compute_facet_coverage(
        self,
        candidates: list[ScoredCandidate],
        query_plan,
    ) -> dict[str, float]:
        """Calcula cobertura máxima por faceta."""
        coverage = {}
        
        for facet in query_plan.facets:
            max_score = 0.0
            for c in candidates:
                facet_score = c.facet_scores.get(facet.facet_id, 0.0)
                max_score = max(max_score, facet_score)
            coverage[facet.facet_id] = max_score
        
        return coverage
    
    def _compute_diversity_score(
        self,
        candidates: list[ScoredCandidate],
    ) -> float:
        """
        Calcula score de diversidad del conjunto.
        
        Diversidad = 1 - (promedio de similitudes entre pares)
        """
        if len(candidates) <= 1:
            return 1.0
        
        similarities = []
        for i in range(len(candidates)):
            for j in range(i + 1, len(candidates)):
                sim = text_similarity(
                    candidates[i].content,
                    candidates[j].content
                )
                similarities.append(sim)
        
        if similarities:
            avg_similarity = np.mean(similarities)
            return 1.0 - avg_similarity
        
        return 1.0


# =============================================================================
# FUNCIONES DE CONVENIENCIA
# =============================================================================

def score_candidates(
    candidates: list,
    query_plan,
    section_context: Optional[dict] = None,
) -> ScoringResult:
    """
    Función de conveniencia para scoring.
    
    Args:
        candidates: Candidatos del retriever
        query_plan: Plan con facetas
        section_context: Contexto opcional
        
    Returns:
        ScoringResult ordenado
    """
    scorer = FusionScorer()
    return scorer.score_candidates(candidates, query_plan, section_context)


def get_top_candidates(
    scoring_result: ScoringResult,
    k: int = 10,
    min_score: float = 0.1,
) -> list[ScoredCandidate]:
    """
    Obtiene los mejores K candidatos.
    
    Args:
        scoring_result: Resultado del scoring
        k: Número máximo
        min_score: Score mínimo para incluir
        
    Returns:
        Lista de candidatos filtrados
    """
    return [
        c for c in scoring_result.candidates[:k]
        if c.final_score >= min_score
    ]