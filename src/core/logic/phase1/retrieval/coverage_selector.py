"""
coverage_selector.py — Selección por Cobertura de Facetas

En lugar de tomar los K primeros por score, selecciona un
conjunto mínimo que maximice:

1. COBERTURA: Cada faceta obligatoria debe tener evidencia fuerte
2. DIVERSIDAD: Evitar que múltiples chunks hablen de lo mismo
3. COHERENCIA: Preferir chunks del mismo bloque o trayectoria

Esto es Retrieval como "composición de evidencia", no como ranking.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional
from enum import Enum


# =============================================================================
# ESTRUCTURAS DE DATOS
# =============================================================================

class CoverageStatus(Enum):
    """Estado de cobertura de una faceta."""
    STRONG = "strong"       # Score >= 0.7
    PARTIAL = "partial"     # Score >= 0.4
    WEAK = "weak"           # Score >= 0.2
    MISSING = "missing"     # Score < 0.2


@dataclass
class FacetCoverage:
    """Cobertura de una faceta específica."""
    facet_id: str
    facet_name: str
    required: bool
    status: CoverageStatus
    best_score: float
    supporting_chunks: list[str]  # chunk_ids que la cubren
    
    @property
    def is_covered(self) -> bool:
        """True si tiene al menos cobertura parcial."""
        return self.status in (CoverageStatus.STRONG, CoverageStatus.PARTIAL)


@dataclass
class CoverageResult:
    """Resultado de la selección por cobertura."""
    selected_chunks: list  # list[ScoredCandidate]
    facet_coverage: dict[str, FacetCoverage]
    
    # Métricas
    total_selected: int = 0
    required_coverage_pct: float = 0.0
    optional_coverage_pct: float = 0.0
    diversity_score: float = 0.0
    coherence_score: float = 0.0
    
    # Facetas sin cobertura
    missing_required: list[str] = field(default_factory=list)
    missing_optional: list[str] = field(default_factory=list)
    
    @property
    def is_complete(self) -> bool:
        """True si todas las facetas required están cubiertas."""
        return len(self.missing_required) == 0


# =============================================================================
# CONFIGURACIÓN
# =============================================================================

# Umbrales de cobertura
STRONG_THRESHOLD = 0.7
PARTIAL_THRESHOLD = 0.4
WEAK_THRESHOLD = 0.2

# Límites de selección
MIN_CHUNKS = 3
MAX_CHUNKS = 15
DEFAULT_TARGET_CHUNKS = 8

# Parámetros de diversidad
DIVERSITY_THRESHOLD = 0.6  # Similitud máxima entre chunks seleccionados


# =============================================================================
# COVERAGE SELECTOR
# =============================================================================

class CoverageSelector:
    """
    Selector que optimiza cobertura de facetas en lugar de Top-K.
    
    Estrategia Greedy:
    1. Para cada faceta required sin cubrir, seleccionar mejor chunk
    2. Añadir chunks que cubren múltiples facetas
    3. Completar con chunks de alta diversidad hasta target
    4. Preferir chunks del mismo bloque para coherencia
    """
    
    def __init__(
        self,
        min_chunks: int = MIN_CHUNKS,
        max_chunks: int = MAX_CHUNKS,
        target_chunks: int = DEFAULT_TARGET_CHUNKS,
        diversity_threshold: float = DIVERSITY_THRESHOLD,
    ):
        self.min_chunks = min_chunks
        self.max_chunks = max_chunks
        self.target_chunks = target_chunks
        self.diversity_threshold = diversity_threshold
    
    def select(
        self,
        scoring_result,  # ScoringResult
        query_plan,      # QueryPlan
    ) -> CoverageResult:
        """
        Selecciona conjunto óptimo de chunks por cobertura.
        
        Args:
            scoring_result: Resultado del fusion scorer
            query_plan: Plan con facetas
            
        Returns:
            CoverageResult con chunks seleccionados
        """
        candidates = scoring_result.candidates
        if not candidates:
            return self._empty_result(query_plan)
        
        selected: list = []
        selected_ids: set[str] = set()
        
        # Inicializar tracking de cobertura
        facet_coverage = self._init_facet_coverage(query_plan)
        
        # Fase 1: Cubrir facetas required
        selected, selected_ids = self._cover_required_facets(
            candidates, query_plan, facet_coverage, selected, selected_ids
        )
        
        # Fase 2: Añadir chunks multi-faceta
        selected, selected_ids = self._add_multi_facet_chunks(
            candidates, query_plan, facet_coverage, selected, selected_ids
        )
        
        # Fase 3: Cubrir facetas optional si hay espacio
        if len(selected) < self.target_chunks:
            selected, selected_ids = self._cover_optional_facets(
                candidates, query_plan, facet_coverage, selected, selected_ids
            )
        
        # Fase 4: Completar con chunks diversos de alto score
        if len(selected) < self.min_chunks:
            selected, selected_ids = self._add_diverse_chunks(
                candidates, selected, selected_ids
            )
        
        # Fase 5: Preferir coherencia (mismo bloque)
        selected = self._reorder_for_coherence(selected)
        
        # Calcular métricas finales
        return self._build_result(selected, facet_coverage, query_plan)
    
    def _init_facet_coverage(
        self,
        query_plan,
    ) -> dict[str, FacetCoverage]:
        """Inicializa tracking de cobertura por faceta."""
        coverage = {}
        for facet in query_plan.facets:
            coverage[facet.facet_id] = FacetCoverage(
                facet_id=facet.facet_id,
                facet_name=facet.name,
                required=facet.required,
                status=CoverageStatus.MISSING,
                best_score=0.0,
                supporting_chunks=[],
            )
        return coverage
    
    def _cover_required_facets(
        self,
        candidates: list,
        query_plan,
        facet_coverage: dict[str, FacetCoverage],
        selected: list,
        selected_ids: set[str],
    ) -> tuple[list, set[str]]:
        """
        Fase 1: Asegurar que cada faceta required tenga al menos un chunk.
        
        Para cada faceta required:
        - Buscar el chunk con mejor score para esa faceta
        - Si no está seleccionado, añadirlo
        """
        for facet in query_plan.required_facets:
            if len(selected) >= self.max_chunks:
                break
            
            # Buscar mejor chunk para esta faceta
            best_chunk = None
            best_score = 0.0
            
            for candidate in candidates:
                if candidate.chunk_id in selected_ids:
                    continue
                
                facet_score = candidate.facet_scores.get(facet.facet_id, 0.0)
                if facet_score > best_score:
                    best_score = facet_score
                    best_chunk = candidate
            
            # Añadir si encontramos uno bueno
            if best_chunk and best_score >= WEAK_THRESHOLD:
                selected.append(best_chunk)
                selected_ids.add(best_chunk.chunk_id)
                
                # Actualizar cobertura
                self._update_coverage(
                    facet_coverage, best_chunk, query_plan
                )
        
        return selected, selected_ids
    
    def _add_multi_facet_chunks(
        self,
        candidates: list,
        query_plan,
        facet_coverage: dict[str, FacetCoverage],
        selected: list,
        selected_ids: set[str],
    ) -> tuple[list, set[str]]:
        """
        Fase 2: Añadir chunks que cubren múltiples facetas.
        
        Prioriza chunks que aportan cobertura a varias facetas
        simultáneamente (eficiencia de evidencia).
        """
        # Calcular cuántas facetas cubre cada chunk
        chunk_facet_counts = []
        
        for candidate in candidates:
            if candidate.chunk_id in selected_ids:
                continue
            
            # Contar facetas con score >= partial
            covered_facets = sum(
                1 for score in candidate.facet_scores.values()
                if score >= PARTIAL_THRESHOLD
            )
            
            if covered_facets >= 2:
                chunk_facet_counts.append((candidate, covered_facets))
        
        # Ordenar por cantidad de facetas cubiertas
        chunk_facet_counts.sort(key=lambda x: x[1], reverse=True)
        
        # Añadir los mejores multi-faceta
        for candidate, count in chunk_facet_counts:
            if len(selected) >= self.target_chunks:
                break
            
            # Verificar diversidad
            if self._is_too_similar(candidate, selected):
                continue
            
            selected.append(candidate)
            selected_ids.add(candidate.chunk_id)
            self._update_coverage(facet_coverage, candidate, query_plan)
        
        return selected, selected_ids
    
    def _cover_optional_facets(
        self,
        candidates: list,
        query_plan,
        facet_coverage: dict[str, FacetCoverage],
        selected: list,
        selected_ids: set[str],
    ) -> tuple[list, set[str]]:
        """
        Fase 3: Cubrir facetas optional si hay espacio.
        """
        for facet in query_plan.optional_facets:
            if len(selected) >= self.target_chunks:
                break
            
            # Saltar si ya está cubierta
            if facet_coverage[facet.facet_id].is_covered:
                continue
            
            # Buscar mejor chunk para esta faceta
            best_chunk = None
            best_score = 0.0
            
            for candidate in candidates:
                if candidate.chunk_id in selected_ids:
                    continue
                
                facet_score = candidate.facet_scores.get(facet.facet_id, 0.0)
                if facet_score > best_score and facet_score >= WEAK_THRESHOLD:
                    # Verificar diversidad
                    if not self._is_too_similar(candidate, selected):
                        best_score = facet_score
                        best_chunk = candidate
            
            if best_chunk:
                selected.append(best_chunk)
                selected_ids.add(best_chunk.chunk_id)
                self._update_coverage(facet_coverage, best_chunk, query_plan)
        
        return selected, selected_ids
    
    def _add_diverse_chunks(
        self,
        candidates: list,
        selected: list,
        selected_ids: set[str],
    ) -> tuple[list, set[str]]:
        """
        Fase 4: Completar con chunks diversos de alto score.
        """
        for candidate in candidates:
            if len(selected) >= self.min_chunks:
                break
            
            if candidate.chunk_id in selected_ids:
                continue
            
            if not self._is_too_similar(candidate, selected):
                selected.append(candidate)
                selected_ids.add(candidate.chunk_id)
        
        return selected, selected_ids
    
    def _reorder_for_coherence(
        self,
        selected: list,
    ) -> list:
        """
        Fase 5: Reordenar para maximizar coherencia narrativa.
        
        Agrupa chunks del mismo bloque juntos.
        """
        if len(selected) <= 2:
            return selected
        
        # Agrupar por block_id
        by_block: dict[str, list] = {}
        no_block = []
        
        for chunk in selected:
            block_id = chunk.metadata.get("block_id", "")
            if block_id:
                if block_id not in by_block:
                    by_block[block_id] = []
                by_block[block_id].append(chunk)
            else:
                no_block.append(chunk)
        
        # Ordenar bloques por posición del primer chunk
        sorted_blocks = sorted(
            by_block.values(),
            key=lambda chunks: chunks[0].metadata.get("position_in_block", 0)
        )
        
        # Reconstruir lista
        reordered = []
        for block_chunks in sorted_blocks:
            # Ordenar chunks dentro del bloque por posición
            block_chunks.sort(
                key=lambda c: c.metadata.get("position_in_block", 0)
            )
            reordered.extend(block_chunks)
        
        reordered.extend(no_block)
        
        return reordered
    
    def _update_coverage(
        self,
        facet_coverage: dict[str, FacetCoverage],
        chunk,
        query_plan,
    ) -> None:
        """Actualiza tracking de cobertura con un chunk."""
        for facet in query_plan.facets:
            score = chunk.facet_scores.get(facet.facet_id, 0.0)
            cov = facet_coverage[facet.facet_id]
            
            # Actualizar mejor score
            if score > cov.best_score:
                cov.best_score = score
            
            # Actualizar status
            if score >= STRONG_THRESHOLD:
                cov.status = CoverageStatus.STRONG
                cov.supporting_chunks.append(chunk.chunk_id)
            elif score >= PARTIAL_THRESHOLD:
                if cov.status != CoverageStatus.STRONG:
                    cov.status = CoverageStatus.PARTIAL
                cov.supporting_chunks.append(chunk.chunk_id)
            elif score >= WEAK_THRESHOLD:
                if cov.status == CoverageStatus.MISSING:
                    cov.status = CoverageStatus.WEAK
    
    def _is_too_similar(
        self,
        candidate,
        selected: list,
    ) -> bool:
        """Verifica si un candidato es muy similar a los seleccionados."""
        if not selected:
            return False
        
        for sel in selected:
            sim = self._text_similarity(candidate.content, sel.content)
            if sim > self.diversity_threshold:
                return True
        
        return False
    
    def _text_similarity(self, text1: str, text2: str) -> float:
        """Similitud simple por overlap de palabras."""
        words1 = set(text1.lower().split())
        words2 = set(text2.lower().split())
        
        if not words1 or not words2:
            return 0.0
        
        intersection = len(words1 & words2)
        union = len(words1 | words2)
        
        return intersection / union if union > 0 else 0.0
    
    def _build_result(
        self,
        selected: list,
        facet_coverage: dict[str, FacetCoverage],
        query_plan,
    ) -> CoverageResult:
        """Construye resultado final con métricas."""
        # Calcular cobertura de required
        required_covered = sum(
            1 for f in query_plan.required_facets
            if facet_coverage[f.facet_id].is_covered
        )
        required_total = len(query_plan.required_facets)
        required_pct = required_covered / required_total if required_total > 0 else 1.0
        
        # Calcular cobertura de optional
        optional_covered = sum(
            1 for f in query_plan.optional_facets
            if facet_coverage[f.facet_id].is_covered
        )
        optional_total = len(query_plan.optional_facets)
        optional_pct = optional_covered / optional_total if optional_total > 0 else 1.0
        
        # Identificar facetas faltantes
        missing_required = [
            f.name for f in query_plan.required_facets
            if not facet_coverage[f.facet_id].is_covered
        ]
        missing_optional = [
            f.name for f in query_plan.optional_facets
            if not facet_coverage[f.facet_id].is_covered
        ]
        
        # Calcular diversidad
        diversity = self._compute_diversity(selected)
        
        # Calcular coherencia (chunks del mismo bloque)
        coherence = self._compute_coherence(selected)
        
        return CoverageResult(
            selected_chunks=selected,
            facet_coverage=facet_coverage,
            total_selected=len(selected),
            required_coverage_pct=required_pct,
            optional_coverage_pct=optional_pct,
            diversity_score=diversity,
            coherence_score=coherence,
            missing_required=missing_required,
            missing_optional=missing_optional,
        )
    
    def _compute_diversity(self, selected: list) -> float:
        """Calcula score de diversidad del conjunto."""
        if len(selected) <= 1:
            return 1.0
        
        similarities = []
        for i in range(len(selected)):
            for j in range(i + 1, len(selected)):
                sim = self._text_similarity(
                    selected[i].content,
                    selected[j].content
                )
                similarities.append(sim)
        
        if similarities:
            avg_sim = sum(similarities) / len(similarities)
            return 1.0 - avg_sim
        
        return 1.0
    
    def _compute_coherence(self, selected: list) -> float:
        """Calcula score de coherencia (chunks del mismo bloque)."""
        if len(selected) <= 1:
            return 1.0
        
        block_ids = [c.metadata.get("block_id", "") for c in selected]
        unique_blocks = len(set(b for b in block_ids if b))
        
        # Coherencia = chunks por bloque promedio
        if unique_blocks > 0:
            return len(selected) / unique_blocks / len(selected)
        
        return 0.5
    
    def _empty_result(self, query_plan) -> CoverageResult:
        """Retorna resultado vacío."""
        facet_coverage = self._init_facet_coverage(query_plan)
        return CoverageResult(
            selected_chunks=[],
            facet_coverage=facet_coverage,
            total_selected=0,
            required_coverage_pct=0.0,
            optional_coverage_pct=0.0,
            diversity_score=0.0,
            coherence_score=0.0,
            missing_required=[f.name for f in query_plan.required_facets],
            missing_optional=[f.name for f in query_plan.optional_facets],
        )


# =============================================================================
# FUNCIONES DE CONVENIENCIA
# =============================================================================

def select_by_coverage(
    scoring_result,
    query_plan,
    target_chunks: int = DEFAULT_TARGET_CHUNKS,
) -> CoverageResult:
    """
    Función de conveniencia para selección por cobertura.
    
    Args:
        scoring_result: Resultado del scorer
        query_plan: Plan con facetas
        target_chunks: Objetivo de chunks
        
    Returns:
        CoverageResult con chunks seleccionados
    """
    selector = CoverageSelector(target_chunks=target_chunks)
    return selector.select(scoring_result, query_plan)


def get_coverage_summary(result: CoverageResult) -> str:
    """
    Genera resumen legible de la cobertura.
    
    Returns:
        String con resumen
    """
    lines = [
        f"Chunks seleccionados: {result.total_selected}",
        f"Cobertura required: {result.required_coverage_pct:.0%}",
        f"Cobertura optional: {result.optional_coverage_pct:.0%}",
        f"Diversidad: {result.diversity_score:.2f}",
        f"Coherencia: {result.coherence_score:.2f}",
    ]
    
    if result.missing_required:
        lines.append(f"⚠️ Facetas required faltantes: {', '.join(result.missing_required)}")
    
    return "\n".join(lines)