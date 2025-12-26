"""
Retrieval Module — Pipeline de Recuperación Multi-Canal

Componentes:
- facet_query_planner: Descomposición en facetas de búsqueda
- multi_channel_retriever: Retrieval dense + sparse + parent
- fusion_scorer: Scoring continuo con relevancia, coherencia, redundancia
- coverage_selector: Selección por cobertura (no Top-K)
- context_assembler: Empaquetado con contexto estructural
"""

from core.logic.phase1.retrieval.facet_query_planner import (
    FacetQueryPlanner,
    QueryPlan,
    Facet,
    FacetType,
    create_query_plan,
    get_recommended_k,
)

from core.logic.phase1.retrieval.multi_channel_retriever import (
    MultiChannelRetriever,
    SparseRetriever,
    ChannelRouter,
    RetrievalCandidate,
    RetrievalResult,
    ChannelWeight,
    create_retriever,
)

from core.logic.phase1.retrieval.fusion_scorer import (
    FusionScorer,
    ScoredCandidate,
    ScoringResult,
    score_candidates,
    get_top_candidates,
)

from core.logic.phase1.retrieval.coverage_selector import (
    CoverageSelector,
    CoverageResult,
    FacetCoverage,
    CoverageStatus,
    select_by_coverage,
    get_coverage_summary,
)

from core.logic.phase1.retrieval.context_assembler import (
    ContextAssembler,
    ContextualChunk,
    EvidencePack,
    assemble_evidence,
    format_for_prompt,
)

__all__ = [
    # Query Planner
    "FacetQueryPlanner",
    "QueryPlan",
    "Facet",
    "FacetType",
    "create_query_plan",
    "get_recommended_k",
    
    # Retriever
    "MultiChannelRetriever",
    "SparseRetriever",
    "ChannelRouter",
    "RetrievalCandidate",
    "RetrievalResult",
    "ChannelWeight",
    "create_retriever",
    
    # Scorer
    "FusionScorer",
    "ScoredCandidate",
    "ScoringResult",
    "score_candidates",
    "get_top_candidates",
    
    # Coverage Selector
    "CoverageSelector",
    "CoverageResult",
    "FacetCoverage",
    "CoverageStatus",
    "select_by_coverage",
    "get_coverage_summary",
    
    # Context Assembler
    "ContextAssembler",
    "ContextualChunk",
    "EvidencePack",
    "assemble_evidence",
    "format_for_prompt",
]