"""
context_assembler.py — Ensamblador de Contexto Estructural

Empaqueta los chunks seleccionados con su contexto jerárquico:

1. PARENT CONTEXT: Añade bloque padre cuando aporta continuidad
2. TRANSITIONS: Añade encabezados y transiciones
3. NARRATIVE ORDER: Ordena por flujo lógico del documento

El Writer recibe un "Evidence Pack" coherente, no fragmentos sueltos.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Any


# =============================================================================
# ESTRUCTURAS DE DATOS
# =============================================================================

@dataclass
class ContextualChunk:
    """Chunk con su contexto estructural adjunto."""
    chunk_id: str
    content: str
    
    # Contexto del padre
    parent_heading: Optional[str] = None
    parent_summary: Optional[str] = None
    include_parent: bool = False
    
    # Transiciones
    transition_in: Optional[str] = None   # Texto de entrada
    transition_out: Optional[str] = None  # Texto de salida
    
    # Posición
    position_in_narrative: int = 0
    block_id: Optional[str] = None
    is_block_start: bool = False
    is_block_end: bool = False
    
    # Metadata original
    metadata: dict = field(default_factory=dict)
    
    # Scores originales
    relevance_score: float = 0.0
    facet_name: Optional[str] = None


@dataclass
class EvidencePack:
    """Paquete completo de evidencia para el Writer."""
    topic_name: str
    chunks: list[ContextualChunk]
    
    # Resumen de cobertura
    facets_covered: list[str]
    facets_missing: list[str]
    
    # Estadísticas
    total_chunks: int = 0
    total_tokens_estimate: int = 0
    has_full_coverage: bool = True
    
    # Texto formateado listo para prompt
    formatted_context: str = ""
    
    def __post_init__(self):
        self.total_chunks = len(self.chunks)
        # Estimación aproximada de tokens
        total_chars = sum(
            len(c.content) + 
            (len(c.parent_summary or "") if c.include_parent else 0)
            for c in self.chunks
        )
        self.total_tokens_estimate = total_chars // 4


# =============================================================================
# CONFIGURACIÓN
# =============================================================================

# Cuándo incluir contexto del padre
INCLUDE_PARENT_IF_FIRST = True      # Si es primer chunk del bloque
INCLUDE_PARENT_IF_COMPLEX = True    # Si el bloque tiene 3+ chunks
MIN_PARENT_SUMMARY_LENGTH = 50      # Mínimo chars para incluir

# Formato de transiciones
TRANSITION_TEMPLATE = "[{heading}]\n"
CHUNK_SEPARATOR = "\n\n---\n\n"
PARENT_MARKER = "[Contexto: {heading}]\n{summary}\n\n"


# =============================================================================
# CONTEXT ASSEMBLER
# =============================================================================

class ContextAssembler:
    """
    Ensambla chunks con su contexto estructural.
    
    Funciones:
    1. Adjuntar contexto de padres cuando aporta valor
    2. Añadir transiciones entre bloques
    3. Ordenar por flujo narrativo
    4. Formatear para prompt del Writer
    """
    
    def __init__(
        self,
        hierarchical_index = None,
        include_parent_for_first: bool = INCLUDE_PARENT_IF_FIRST,
        include_parent_for_complex: bool = INCLUDE_PARENT_IF_COMPLEX,
    ):
        self.index = hierarchical_index
        self.include_parent_for_first = include_parent_for_first
        self.include_parent_for_complex = include_parent_for_complex
    
    def assemble(
        self,
        coverage_result,  # CoverageResult
        query_plan,       # QueryPlan
    ) -> EvidencePack:
        """
        Ensambla Evidence Pack desde resultado de cobertura.
        
        Args:
            coverage_result: Chunks seleccionados por coverage
            query_plan: Plan con facetas
            
        Returns:
            EvidencePack listo para el Writer
        """
        selected_chunks = coverage_result.selected_chunks
        
        if not selected_chunks:
            return self._empty_pack(query_plan)
        
        # 1. Convertir a ContextualChunk y enriquecer
        contextual_chunks = []
        for i, chunk in enumerate(selected_chunks):
            ctx_chunk = self._enrich_chunk(chunk, i)
            contextual_chunks.append(ctx_chunk)
        
        # 2. Adjuntar contexto de padres
        contextual_chunks = self._attach_parent_contexts(contextual_chunks)
        
        # 3. Añadir transiciones entre bloques
        contextual_chunks = self._add_transitions(contextual_chunks)
        
        # 4. Ordenar por narrativa
        contextual_chunks = self._order_by_narrative(contextual_chunks)
        
        # 5. Formatear contexto
        formatted = self._format_context(contextual_chunks)
        
        # 6. Construir pack
        facets_covered = [
            fc.facet_name 
            for fc in coverage_result.facet_coverage.values()
            if fc.is_covered
        ]
        facets_missing = coverage_result.missing_required + coverage_result.missing_optional
        
        return EvidencePack(
            topic_name=query_plan.topic_name,
            chunks=contextual_chunks,
            facets_covered=facets_covered,
            facets_missing=facets_missing,
            has_full_coverage=coverage_result.is_complete,
            formatted_context=formatted,
        )
    
    def assemble_simple(
        self,
        chunks: list,
        topic_name: str,
    ) -> EvidencePack:
        """
        Ensamblaje simplificado sin coverage_result.
        
        Args:
            chunks: Lista de chunks (ScoredCandidate o similar)
            topic_name: Nombre del tema
            
        Returns:
            EvidencePack básico
        """
        contextual_chunks = []
        for i, chunk in enumerate(chunks):
            ctx_chunk = self._enrich_chunk(chunk, i)
            contextual_chunks.append(ctx_chunk)
        
        contextual_chunks = self._attach_parent_contexts(contextual_chunks)
        contextual_chunks = self._order_by_narrative(contextual_chunks)
        formatted = self._format_context(contextual_chunks)
        
        return EvidencePack(
            topic_name=topic_name,
            chunks=contextual_chunks,
            facets_covered=[],
            facets_missing=[],
            has_full_coverage=True,
            formatted_context=formatted,
        )
    
    def _enrich_chunk(
        self,
        chunk,
        position: int,
    ) -> ContextualChunk:
        """Convierte chunk a ContextualChunk con metadata."""
        metadata = getattr(chunk, 'metadata', {})
        
        return ContextualChunk(
            chunk_id=chunk.chunk_id,
            content=chunk.content,
            position_in_narrative=position,
            block_id=metadata.get("block_id"),
            is_block_start=metadata.get("position_in_block", 0) == 0,
            is_block_end=(
                metadata.get("position_in_block", 0) == 
                metadata.get("total_in_block", 1) - 1
            ),
            metadata=metadata,
            relevance_score=getattr(chunk, 'relevance_score', 0.0),
            facet_name=getattr(chunk, 'primary_facet_name', None),
        )
    
    def _attach_parent_contexts(
        self,
        chunks: list[ContextualChunk],
    ) -> list[ContextualChunk]:
        """
        Decide si adjuntar contexto del padre a cada chunk.
        
        Reglas:
        - Primer chunk de un bloque → incluir heading
        - Bloque con muchos chunks → incluir summary
        - Único chunk del bloque → no incluir (ya tiene contexto)
        """
        if not self.index:
            return chunks
        
        # Agrupar por bloque para contar
        by_block: dict[str, list[ContextualChunk]] = {}
        for chunk in chunks:
            if chunk.block_id:
                if chunk.block_id not in by_block:
                    by_block[chunk.block_id] = []
                by_block[chunk.block_id].append(chunk)
        
        for chunk in chunks:
            if not chunk.block_id:
                continue
            
            try:
                parent = self.index.get_block_by_id(chunk.block_id)
                if not parent:
                    continue
                
                parent_heading = parent.metadata.get("heading", "")
                
                # Decidir si incluir
                should_include = False
                
                # Primer chunk del bloque
                if self.include_parent_for_first and chunk.is_block_start:
                    should_include = True
                
                # Bloque complejo (3+ chunks seleccionados)
                block_chunks_count = len(by_block.get(chunk.block_id, []))
                if self.include_parent_for_complex and block_chunks_count >= 3:
                    should_include = True
                
                if should_include and parent_heading:
                    chunk.parent_heading = parent_heading
                    # Summary = primeros N chars del contenido
                    parent_content = parent.content[:300] if parent.content else ""
                    if len(parent_content) >= MIN_PARENT_SUMMARY_LENGTH:
                        chunk.parent_summary = parent_content
                    chunk.include_parent = True
                    
            except Exception:
                pass
        
        return chunks
    
    def _add_transitions(
        self,
        chunks: list[ContextualChunk],
    ) -> list[ContextualChunk]:
        """
        Añade transiciones entre chunks de diferentes bloques.
        """
        if len(chunks) <= 1:
            return chunks
        
        prev_block = None
        for chunk in chunks:
            current_block = chunk.block_id
            
            # Si cambia de bloque y tiene heading, añadir transición
            if current_block != prev_block and chunk.parent_heading:
                chunk.transition_in = TRANSITION_TEMPLATE.format(
                    heading=chunk.parent_heading
                )
            
            prev_block = current_block
        
        return chunks
    
    def _order_by_narrative(
        self,
        chunks: list[ContextualChunk],
    ) -> list[ContextualChunk]:
        """
        Ordena chunks por flujo narrativo.
        
        Prioridad:
        1. Posición del bloque en el documento
        2. Posición del chunk dentro del bloque
        """
        def sort_key(chunk: ContextualChunk):
            block_pos = chunk.metadata.get("position_in_doc", 999)
            chunk_pos = chunk.metadata.get("position_in_block", 0)
            return (block_pos, chunk_pos)
        
        return sorted(chunks, key=sort_key)
    
    def _format_context(
        self,
        chunks: list[ContextualChunk],
    ) -> str:
        """
        Formatea chunks en texto listo para prompt.
        """
        parts = []
        
        for i, chunk in enumerate(chunks):
            chunk_text = []
            
            # Transición de entrada
            if chunk.transition_in:
                chunk_text.append(chunk.transition_in)
            
            # Contexto del padre
            if chunk.include_parent and chunk.parent_summary:
                chunk_text.append(PARENT_MARKER.format(
                    heading=chunk.parent_heading or "Sección",
                    summary=chunk.parent_summary[:200] + "..."
                ))
            
            # Contenido del chunk
            chunk_text.append(chunk.content)
            
            # Indicador de faceta (útil para debug)
            if chunk.facet_name:
                chunk_text.append(f"\n[Relevante para: {chunk.facet_name}]")
            
            parts.append("\n".join(chunk_text))
        
        return CHUNK_SEPARATOR.join(parts)
    
    def _empty_pack(self, query_plan) -> EvidencePack:
        """Retorna pack vacío."""
        return EvidencePack(
            topic_name=query_plan.topic_name,
            chunks=[],
            facets_covered=[],
            facets_missing=[f.name for f in query_plan.facets],
            has_full_coverage=False,
            formatted_context="[No se encontró evidencia relevante]",
        )


# =============================================================================
# FUNCIONES DE CONVENIENCIA
# =============================================================================

def assemble_evidence(
    coverage_result,
    query_plan,
    hierarchical_index = None,
) -> EvidencePack:
    """
    Función de conveniencia para ensamblar evidencia.
    
    Args:
        coverage_result: Resultado del selector
        query_plan: Plan con facetas
        hierarchical_index: Índice para obtener padres
        
    Returns:
        EvidencePack listo para Writer
    """
    assembler = ContextAssembler(hierarchical_index)
    return assembler.assemble(coverage_result, query_plan)


def format_for_prompt(
    evidence_pack: EvidencePack,
    include_coverage_info: bool = True,
) -> str:
    """
    Formatea Evidence Pack para incluir en prompt del Writer.
    
    Args:
        evidence_pack: Pack de evidencia
        include_coverage_info: Si incluir info de cobertura
        
    Returns:
        Texto formateado
    """
    parts = []
    
    # Header
    parts.append(f"=== CONTEXTO PARA: {evidence_pack.topic_name} ===")
    parts.append(f"({evidence_pack.total_chunks} fragmentos, ~{evidence_pack.total_tokens_estimate} tokens)")
    parts.append("")
    
    # Cobertura
    if include_coverage_info:
        if evidence_pack.facets_covered:
            parts.append(f"✓ Cubre: {', '.join(evidence_pack.facets_covered)}")
        if evidence_pack.facets_missing:
            parts.append(f"⚠ Falta: {', '.join(evidence_pack.facets_missing)}")
        parts.append("")
    
    # Contenido
    parts.append(evidence_pack.formatted_context)
    
    parts.append("")
    parts.append("=== FIN CONTEXTO ===")
    
    return "\n".join(parts)