"""
state_schema.py — Contratos de Estado para ZK Foundry Static v2

Este módulo define TODOS los tipos de datos que fluyen por el sistema.
Es el "contrato" entre componentes: si cambias algo aquí, afecta todo.

PRINCIPIOS:
- Inmutabilidad: Los estados no se mutan, se crean nuevas versiones.
- Serialización: Todo debe poder ir a JSON/disco sin pérdida.
- Versionado: Cada bundle tiene versión de schema para migraciones futuras.

V2 CAMBIOS:
- MasterPlan: Plan maestro con reglas de contención
- TopicDirective: Directivas por tema (must_include/must_exclude)
- NavigationContext: Contexto de navegación para transiciones
- WriterTaskState: Estado efímero del Writer Agent
- WriterResult: Resultado de un Writer individual
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
    DEFINES = "defines"
    CONTRASTS = "contrasts"
    DEPENDS_ON = "depends_on"
    EXEMPLIFIES = "exemplifies"
    REFUTES = "refutes"
    APPLIES = "applies"
    EXTENDS = "extends"
    RELATES = "relates"


class RiskLevel(str, Enum):
    """Niveles de riesgo detectados en planificación."""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


# =============================================================================
# MODELOS BASE (Pydantic para validación + serialización)
# =============================================================================

class SourceMetadata(BaseModel):
    """Metadatos del archivo fuente original."""
    filename: str
    file_path: str
    file_hash: str
    file_size_bytes: int
    ingested_at: datetime = Field(default_factory=datetime.now)
    content_type: Literal["transcript", "markdown", "text", "pdf"] = "text"
    
    @computed_field
    @property
    def source_id(self) -> str:
        return f"src_{self.file_hash[:16]}"


# =============================================================================
# NUEVOS MODELOS V2: ARQUITECTURA PARALELA
# =============================================================================

class NavigationContext(BaseModel):
    """Contexto de navegación para transiciones entre secciones."""
    sequence_id: int
    total_sections: int
    previous_topic: str | None = None
    previous_summary: str | None = None
    next_topic: str | None = None
    next_summary: str | None = None
    
    def get_transition_hint(self) -> str:
        """Genera hint para el writer sobre transiciones."""
        hints = []
        if self.previous_topic:
            hints.append(f"Viene después de: '{self.previous_topic}'")
        if self.next_topic:
            hints.append(f"Precede a: '{self.next_topic}'")
        if self.sequence_id == 1:
            hints.append("Es la PRIMERA sección - incluir introducción general")
        if self.sequence_id == self.total_sections:
            hints.append("Es la ÚLTIMA sección - incluir cierre/resumen")
        return " | ".join(hints) if hints else "Sección intermedia estándar"


class TopicDirective(BaseModel):
    """Directivas de contención para un tema específico."""
    sequence_id: int
    topic_id: str
    topic_name: str
    description: str = ""
    
    # Reglas de contención
    must_include: list[str] = Field(default_factory=list)
    must_exclude: list[str] = Field(default_factory=list)
    key_concepts: list[str] = Field(default_factory=list)
    
    # Metadatos
    estimated_word_count: int = 300
    complexity: Literal["basic", "intermediate", "advanced"] = "intermediate"
    
    # Ruta al chunk físico
    chunk_path: str = ""
    
    # Navegación
    navigation: NavigationContext | None = None


class DetectedRisk(BaseModel):
    """Riesgo detectado durante la planificación."""
    risk_type: Literal["overlap", "dependency", "gap", "contradiction"]
    severity: RiskLevel = RiskLevel.MEDIUM
    description: str
    affected_topics: list[str] = Field(default_factory=list)
    suggestion: str = ""


class MasterPlan(BaseModel):
    """
    Plan maestro generado por el Planner.
    Gobierna toda la ejecución paralela de writers.
    """
    plan_id: str
    source_id: str
    created_at: datetime = Field(default_factory=datetime.now)
    
    # Temas ordenados con directivas
    topics: list[TopicDirective] = Field(default_factory=list)
    
    # Mapa de navegación para acceso rápido
    navigation_map: dict[str, NavigationContext] = Field(default_factory=dict)
    
    # Riesgos detectados
    detected_risks: list[DetectedRisk] = Field(default_factory=list)
    
    # Metadatos del plan
    total_estimated_words: int = 0
    planning_rationale: str = ""
    
    @computed_field
    @property
    def topic_count(self) -> int:
        return len(self.topics)
    
    def get_topic_by_sequence(self, seq_id: int) -> TopicDirective | None:
        for topic in self.topics:
            if topic.sequence_id == seq_id:
                return topic
        return None
    
    def to_json(self) -> str:
        return self.model_dump_json(indent=2)
    
    @classmethod
    def from_json(cls, json_str: str) -> MasterPlan:
        return cls.model_validate_json(json_str)


class WriterResult(BaseModel):
    """Resultado de un Writer Agent individual."""
    sequence_id: int
    topic_id: str
    topic_name: str
    
    # Contenido generado
    compiled_markdown: str
    
    # Metadatos de ejecución
    word_count: int = 0
    processing_time_ms: int = 0
    
    # Validación básica
    followed_must_include: list[str] = Field(default_factory=list)
    violated_must_exclude: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    
    # Estado
    success: bool = True
    error_message: str | None = None


# =============================================================================
# MODELOS EXISTENTES (mantenidos para compatibilidad)
# =============================================================================

class Topic(BaseModel):
    """Un tema detectado en el texto fuente."""
    id: str
    name: str
    description: str = ""
    keywords: list[str] = Field(default_factory=list)
    estimated_complexity: Literal["basic", "intermediate", "advanced"] = "intermediate"
    prerequisites: list[str] = Field(default_factory=list)
    relevance: int = 50
    type: str = "concept"


class OrderedOutlineItem(BaseModel):
    """Item del temario ordenado."""
    position: int
    topic_id: str
    topic_name: str
    rationale: str
    subtopics: list[str] = Field(default_factory=list)


class SemanticChunk(BaseModel):
    """Un fragmento semántico del texto, alineado a un tema."""
    id: str
    topic_id: str
    content: str
    start_position: int
    end_position: int
    anchor_text: str
    word_count: int
    
    @computed_field
    @property
    def chunk_hash(self) -> str:
        return hashlib.sha256(self.content.encode()).hexdigest()[:12]


class Warning(BaseModel):
    """Advertencia detectada durante el procesamiento."""
    type: Literal["gap", "repetition", "contradiction", "unclear", "missing_context"]
    description: str
    location: str | None = None
    severity: Literal["low", "medium", "high"] = "medium"


# =============================================================================
# MODELOS DE FASE 2 (sin cambios)
# =============================================================================

class AtomicNotePlan(BaseModel):
    """Plan para una nota atómica."""
    id: str
    topic_id: str
    proposed_title: str
    rationale: str
    novelty_score: float = Field(ge=0, le=1)
    estimated_connections: int
    priority: Literal["high", "medium", "low"] = "medium"
    type: Literal["concept", "example", "application", "contrast", "synthesis"] = "concept"


class AtomicNote(BaseModel):
    """Una nota atómica completa (Zettelkasten)."""
    id: str
    title: str
    content: str
    frontmatter: dict[str, Any]
    source_id: str
    chunk_ids: list[str]
    created_at: datetime = Field(default_factory=datetime.now)
    
    @computed_field
    @property
    def note_hash(self) -> str:
        return hashlib.sha256(f"{self.title}:{self.content}".encode()).hexdigest()[:12]


class ProposedLink(BaseModel):
    """Un enlace propuesto entre notas."""
    source_note_id: str
    target_note_id: str
    link_type: LinkType
    rationale: str
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
        return self.total_score >= 85 and not any(
            i.severity == "error" for i in self.issues
        )


class GraphRAGContext(BaseModel):
    """Contexto recuperado del GraphRAG."""
    similar_chunks: list[str]
    similar_notes: list[str]
    graph_neighbors: list[str]
    retrieved_at: datetime = Field(default_factory=datetime.now)
    summary: str


# =============================================================================
# BUNDLES
# =============================================================================

class Phase1Bundle(BaseModel):
    """
    Bundle de Fase 1: resultado de procesar una clase cruda.
    V2: Incluye MasterPlan y rutas a productos.
    """
    schema_version: str = "2.0.0"
    bundle_id: str
    created_at: datetime = Field(default_factory=datetime.now)
    
    # Fuente
    source_metadata: SourceMetadata
    raw_content_preview: str
    
    # V2: Plan maestro
    master_plan: MasterPlan | None = None
    
    # Resultados del procesamiento
    topics: list[Topic] = Field(default_factory=list)
    ordered_outline: list[OrderedOutlineItem] = Field(default_factory=list)
    semantic_chunks: list[SemanticChunk] = Field(default_factory=list)
    ordered_class_markdown: str = ""
    
    # V2: Rutas a productos
    draft_path: str = ""
    section_notes_dir: str = ""
    chunk_files: list[str] = Field(default_factory=list)
    
    # Advertencias
    warnings: list[Warning] = Field(default_factory=list)
    
    # Estado de revisión
    approval_status: ApprovalStatus = ApprovalStatus.PENDING
    human_directives: str | None = None
    reviewed_at: datetime | None = None
    
    def to_json(self) -> str:
        return self.model_dump_json(indent=2)
    
    @classmethod
    def from_json(cls, json_str: str) -> Phase1Bundle:
        return cls.model_validate_json(json_str)


class Phase2Bundle(BaseModel):
    """Bundle de Fase 2: resultado de generar atomic notes."""
    schema_version: str = "1.0.0"
    bundle_id: str
    created_at: datetime = Field(default_factory=datetime.now)
    
    lesson_id: str
    phase1_bundle_id: str
    
    atomic_plan: list[AtomicNotePlan]
    plan_rationale: str
    
    atomic_proposals: list[AtomicNote]
    linking_matrix: list[ProposedLink]
    moc_updates: list[MOCUpdate] = Field(default_factory=list)
    
    validation_report: ValidationReport
    graph_rag_context: GraphRAGContext
    
    approval_status: ApprovalStatus = ApprovalStatus.PENDING
    human_directives: str | None = None
    reviewed_at: datetime | None = None
    iteration_count: int = 0
    
    def to_json(self) -> str:
        return self.model_dump_json(indent=2)
    
    @classmethod
    def from_json(cls, json_str: str) -> Phase2Bundle:
        return cls.model_validate_json(json_str)


# =============================================================================
# LANGGRAPH STATES (TypedDict para el grafo)
# =============================================================================

class Phase1State(TypedDict, total=False):
    """
    Estado que fluye por Phase1Graph V2.
    Soporta arquitectura paralela con Send().
    """
    # Input
    source_path: str
    raw_content: str
    source_metadata: dict
    
    # V2: Plan maestro
    master_plan: dict
    
    # V2: Rutas a chunks en disco
    chunk_paths: list[str]
    
    # V2: Resultados de writers (acumulados por fan-in)
    writer_results: list[dict]
    
    # Legacy (compatibilidad)
    topics: list[dict]
    ordered_outline: list[dict]
    semantic_chunks: list[dict]
    
    # Output
    ordered_class_markdown: str
    draft_path: str
    section_notes_dir: str
    
    warnings: list[dict]
    bundle: dict
    
    # Control
    current_node: str
    error: str | None


class WriterTaskState(TypedDict, total=False):
    """
    Estado efímero para un Writer Agent individual.
    Se crea por cada Send() y se destruye al terminar.
    """
    # Input mínimo
    chunk_path: str
    sequence_id: int
    topic_id: str
    topic_name: str
    
    # Directivas de contención
    must_include: list[str]
    must_exclude: list[str]
    key_concepts: list[str]
    
    # Contexto de navegación
    navigation_context: dict
    
    # Output
    compiled_markdown: str
    word_count: int
    warnings: list[str]
    success: bool
    error: str | None


class Phase2State(TypedDict, total=False):
    """Estado que fluye por Phase2Graph."""
    lesson_id: str
    ordered_class_path: str
    phase1_bundle_id: str
    
    graph_rag_context: dict
    atomic_plan: list[dict]
    atomic_proposals: list[dict]
    linking_matrix: list[dict]
    moc_updates: list[dict]
    
    validation_report: dict
    
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


def generate_plan_id(source_id: str) -> str:
    """Genera ID para un MasterPlan."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"plan_{source_id}_{timestamp}"


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