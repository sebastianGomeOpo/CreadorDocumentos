"""
state_schema.py — Contratos de Estado para ZK Foundry Static v2

Este módulo define TODOS los tipos de datos que fluyen por el sistema.
Es el "contrato" entre componentes: si cambias algo aquí, afecta todo.

PRINCIPIOS:
- Inmutabilidad: Los estados no se mutan, se crean nuevas versiones.
- Serialización: Todo debe poder ir a JSON/disco sin pérdida.
- Versionado: Cada bundle tiene versión de schema para migraciones futuras.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Annotated, Any, Literal, TypedDict

from pydantic import BaseModel, Field, computed_field


# =============================================================================
# ENUMS Y LITERALES
# =============================================================================

class ApprovalStatus(str, Enum):
    """Estados posibles de aprobación humana."""
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    NEEDS_MANUAL_REVIEW = "needs_manual_review"


class LinkType(str, Enum):
    """Tipos de enlaces entre notas atómicas."""
    DEFINES = "defines"           # A define un concepto usado en B
    CONTRASTS = "contrasts"       # A contrasta/contradice con B
    DEPENDS_ON = "depends_on"     # A requiere entender B primero
    EXEMPLIFIES = "exemplifies"   # A es un ejemplo de B
    REFUTES = "refutes"           # A refuta la afirmación de B
    APPLIES = "applies"           # A aplica el concepto de B
    EXTENDS = "extends"           # A extiende/profundiza B
    RELATES = "relates"           # Relación genérica


# =============================================================================
# MODELOS BASE (Pydantic para validación + serialización)
# =============================================================================

class SourceMetadata(BaseModel):
    """Metadatos del archivo fuente original."""
    filename: str
    file_path: str
    file_hash: str                          # SHA256 del contenido
    file_size_bytes: int
    ingested_at: datetime = Field(default_factory=datetime.now)
    content_type: Literal["transcript", "markdown", "text", "pdf"] = "text"
    
    @computed_field
    @property
    def source_id(self) -> str:
        """ID determinístico basado en hash del contenido."""
        return f"src_{self.file_hash[:16]}"


# Busca la clase Topic y reemplázala con esto:
class Topic(BaseModel):
    """Un tema detectado en el texto fuente."""
    id: str
    name: str
    description: str = ""   # <--- Ahora es opcional y tiene default
    keywords: list[str] = Field(default_factory=list)
    estimated_complexity: Literal["basic", "intermediate", "advanced"] = "intermediate"
    prerequisites: list[str] = Field(default_factory=list)
    
    # <--- AGREGADOS PARA EVITAR WARNINGS ---
    relevance: int = 50       
    type: str = "concept"


class OrderedOutlineItem(BaseModel):
    """Item del temario ordenado."""
    position: int
    topic_id: str
    topic_name: str
    rationale: str                          # Por qué va en esta posición
    subtopics: list[str] = Field(default_factory=list)


class SemanticChunk(BaseModel):
    """Un fragmento semántico del texto, alineado a un tema."""
    id: str
    topic_id: str
    content: str
    start_position: int                     # Posición en texto original
    end_position: int
    anchor_text: str                        # Texto de referencia para citas
    word_count: int
    
    @computed_field
    @property
    def chunk_hash(self) -> str:
        """Hash del contenido para deduplicación."""
        return hashlib.sha256(self.content.encode()).hexdigest()[:12]


class Warning(BaseModel):
    """Advertencia detectada durante el procesamiento."""
    type: Literal["gap", "repetition", "contradiction", "unclear", "missing_context"]
    description: str
    location: str | None = None             # Dónde se detectó
    severity: Literal["low", "medium", "high"] = "medium"


# =============================================================================
# MODELOS DE FASE 2 (Atomic Notes)
# =============================================================================

class AtomicNotePlan(BaseModel):
    """Plan para una nota atómica (antes de generarla)."""
    id: str
    topic_id: str
    proposed_title: str
    rationale: str                          # Por qué vale la pena esta nota
    novelty_score: float = Field(ge=0, le=1)  # 0=duplicado, 1=totalmente nuevo
    estimated_connections: int              # Cuántos enlaces se esperan
    # Campos agregados para compatibilidad
    priority: Literal["high", "medium", "low"] = "medium"
    type: Literal["concept", "example", "application", "contrast", "synthesis"] = "concept"


class AtomicNote(BaseModel):
    """Una nota atómica completa (Zettelkasten)."""
    id: str
    title: str
    content: str                            # Markdown body
    frontmatter: dict[str, Any]             # YAML frontmatter
    source_id: str                          # De qué fuente viene
    chunk_ids: list[str]                    # Chunks que la respaldan
    created_at: datetime = Field(default_factory=datetime.now)
    
    @computed_field
    @property
    def note_hash(self) -> str:
        """Hash para deduplicación."""
        return hashlib.sha256(f"{self.title}:{self.content}".encode()).hexdigest()[:12]


class ProposedLink(BaseModel):
    """Un enlace propuesto entre notas."""
    source_note_id: str
    target_note_id: str
    link_type: LinkType
    rationale: str                          # Por qué este enlace tiene sentido
    confidence: float = Field(ge=0, le=1)


class MOCUpdate(BaseModel):
    """Actualización propuesta a un Map of Content."""
    moc_id: str
    moc_path: str
    action: Literal["add_link", "create_section", "reorder"]
    details: dict[str, Any]


class ValidationIssue(BaseModel):
    """Un problema detectado por el validador epistémico."""
    note_id: str
    issue_type: Literal["atomicity", "evidence", "format", "coherence", "duplicate"]
    description: str
    suggestion: str
    severity: Literal["error", "warning", "info"]


class ValidationReport(BaseModel):
    """Reporte completo de validación epistémica."""
    atomicity_score: float = Field(ge=0, le=100)
    evidence_score: float = Field(ge=0, le=100)
    format_score: float = Field(ge=0, le=100)
    coherence_score: float = Field(ge=0, le=100)
    issues: list[ValidationIssue] = Field(default_factory=list)
    
    @computed_field
    @property
    def total_score(self) -> float:
        """Score total ponderado."""
        weights = {"atomicity": 0.3, "evidence": 0.3, "format": 0.2, "coherence": 0.2}
        return (
            self.atomicity_score * weights["atomicity"] +
            self.evidence_score * weights["evidence"] +
            self.format_score * weights["format"] +
            self.coherence_score * weights["coherence"]
        )
    
    @computed_field
    @property
    def is_passing(self) -> bool:
        """¿Pasa el umbral mínimo de calidad?"""
        return self.total_score >= 85 and not any(
            i.severity == "error" for i in self.issues
        )


class GraphRAGContext(BaseModel):
    """Contexto recuperado del GraphRAG."""
    similar_chunks: list[str]               # IDs de chunks similares
    similar_notes: list[str]                # IDs de notas similares
    graph_neighbors: list[str]              # Nodos a 1-hop en el grafo
    retrieved_at: datetime = Field(default_factory=datetime.now)
    summary: str                            # Resumen del contexto para el LLM


# =============================================================================
# BUNDLES (Estados serializados a disco)
# =============================================================================

class Phase1Bundle(BaseModel):
    """
    Bundle de Fase 1: resultado de procesar una clase cruda.
    Se guarda en staging/phase1_pending/ para revisión humana.
    """
    schema_version: str = "1.0.0"
    bundle_id: str
    created_at: datetime = Field(default_factory=datetime.now)
    
    # Fuente
    source_metadata: SourceMetadata
    raw_content_preview: str                # Primeros N caracteres
    
    # Resultados del procesamiento
    topics: list[Topic]
    ordered_outline: list[OrderedOutlineItem]
    semantic_chunks: list[SemanticChunk]
    ordered_class_markdown: str             # Clase redactada y ordenada
    
    # Advertencias
    warnings: list[Warning] = Field(default_factory=list)
    
    # Estado de revisión
    approval_status: ApprovalStatus = ApprovalStatus.PENDING
    human_directives: str | None = None
    reviewed_at: datetime | None = None
    
    def to_json(self) -> str:
        """Serializa a JSON con formato legible."""
        return self.model_dump_json(indent=2)
    
    @classmethod
    def from_json(cls, json_str: str) -> Phase1Bundle:
        """Deserializa desde JSON."""
        return cls.model_validate_json(json_str)


class Phase2Bundle(BaseModel):
    """
    Bundle de Fase 2: resultado de generar atomic notes.
    Se guarda en staging/phase2_pending/ para revisión humana.
    """
    schema_version: str = "1.0.0"
    bundle_id: str
    created_at: datetime = Field(default_factory=datetime.now)
    
    # Referencia a Fase 1
    lesson_id: str                          # ID de la clase ordenada aprobada
    phase1_bundle_id: str
    
    # Plan de atomización
    atomic_plan: list[AtomicNotePlan]
    plan_rationale: str                     # Explicación general del plan
    
    # Propuestas generadas
    atomic_proposals: list[AtomicNote]
    linking_matrix: list[ProposedLink]
    moc_updates: list[MOCUpdate] = Field(default_factory=list)
    
    # Validación
    validation_report: ValidationReport
    
    # Contexto RAG (para auditoría)
    graph_rag_context: GraphRAGContext
    
    # Estado de revisión
    approval_status: ApprovalStatus = ApprovalStatus.PENDING
    human_directives: str | None = None
    reviewed_at: datetime | None = None
    iteration_count: int = 0                # Cuántas veces se refinó
    
    def to_json(self) -> str:
        return self.model_dump_json(indent=2)
    
    @classmethod
    def from_json(cls, json_str: str) -> Phase2Bundle:
        return cls.model_validate_json(json_str)


# =============================================================================
# LANGGRAPH STATE (TypedDict para el grafo)
# =============================================================================

class Phase1State(TypedDict, total=False):
    """
    Estado que fluye por Phase1Graph.
    
    NOTA: Usamos TypedDict (no Pydantic) porque LangGraph lo requiere
    para su sistema de reducers y checkpoints.
    """
    # Input
    source_path: str
    raw_content: str
    source_metadata: dict                   # SourceMetadata serializado
    
    # Working memory
    topics: list[dict]                      # List[Topic] serializado
    ordered_outline: list[dict]
    semantic_chunks: list[dict]
    
    # Output
    ordered_class_markdown: str
    warnings: list[dict]
    bundle: dict  # <--- AGREGA ESTA LÍNEA (CRÍTICO)
    # Control
    current_node: str
    error: str | None
    

class Phase2State(TypedDict, total=False):
    """
    Estado que fluye por Phase2Graph.
    """
    # Input
    lesson_id: str
    ordered_class_path: str
    phase1_bundle_id: str
    
    # Working memory
    graph_rag_context: dict                 # GraphRAGContext serializado
    atomic_plan: list[dict]
    atomic_proposals: list[dict]
    linking_matrix: list[dict]
    moc_updates: list[dict]
    
    # Validation
    validation_report: dict
    
    # Control
    current_node: str
    iteration_count: int
    human_directives: str | None
    error: str | None


# =============================================================================
# UTILIDADES
# =============================================================================

def generate_source_id(content: str) -> str:
    """Genera ID determinístico para una fuente."""
    content_hash = hashlib.sha256(content.encode()).hexdigest()
    return f"src_{content_hash[:16]}"


def generate_bundle_id(source_id: str, phase: int) -> str:
    """Genera ID único para un bundle."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"bundle_p{phase}_{source_id}_{timestamp}"


def generate_note_id(title: str, source_id: str) -> str:
    """Genera ID determinístico para una nota atómica."""
    combined = f"{title}:{source_id}"
    note_hash = hashlib.sha256(combined.encode()).hexdigest()[:12]
    return f"note_{note_hash}"


def generate_chunk_id(content: str, topic_id: str) -> str:
    """Genera ID determinístico para un chunk."""
    combined = f"{topic_id}:{content[:100]}"
    chunk_hash = hashlib.sha256(combined.encode()).hexdigest()[:10]
    return f"chunk_{chunk_hash}"