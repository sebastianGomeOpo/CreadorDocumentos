"""
epistemic_validator.py — Validador Epistémico

Este módulo aplica una rúbrica de calidad a las notas atómicas
generadas, evaluando múltiples dimensiones epistemológicas.

DIMENSIONES DE VALIDACIÓN:
1. ATOMICIDAD: ¿Una sola idea por nota?
2. EVIDENCIA: ¿Claims respaldados por fuente?
3. FORMATO: ¿Estructura Markdown correcta?
4. COHERENCIA: ¿Consistente con conocimiento existente?

SCORING:
- Cada dimensión: 0-100
- Score total: promedio ponderado
- Umbral de aprobación: 85

CONEXIONES:
- Llamado por: phase2_graph.py (nodo epistemic_validator)
- Input de: atomic_generator.py (notas)
- Output usado por: routing (refiner vs gatekeeper)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.language_models import BaseChatModel


# =============================================================================
# CONFIGURACIÓN
# =============================================================================

QUALITY_THRESHOLD = 85  # Score mínimo para aprobar

DIMENSION_WEIGHTS = {
    "atomicity": 0.30,
    "evidence": 0.30,
    "format": 0.20,
    "coherence": 0.20,
}


class IssueSeverity(str, Enum):
    """Severidad de un issue de validación."""
    ERROR = "error"      # Bloquea aprobación
    WARNING = "warning"  # Reduce score pero no bloquea
    INFO = "info"        # Informativo


class IssueType(str, Enum):
    """Tipos de issues detectables."""
    ATOMICITY = "atomicity"
    EVIDENCE = "evidence"
    FORMAT = "format"
    COHERENCE = "coherence"
    DUPLICATE = "duplicate"


# =============================================================================
# MODELOS DE DATOS
# =============================================================================

@dataclass
class ValidationIssue:
    """Un problema detectado en una nota."""
    note_id: str
    issue_type: IssueType
    description: str
    suggestion: str
    severity: IssueSeverity
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "note_id": self.note_id,
            "issue_type": self.issue_type.value,
            "description": self.description,
            "suggestion": self.suggestion,
            "severity": self.severity.value,
        }


@dataclass
class NoteValidation:
    """Resultado de validar una nota individual."""
    note_id: str
    atomicity_score: float
    evidence_score: float
    format_score: float
    coherence_score: float
    issues: list[ValidationIssue] = field(default_factory=list)
    
    @property
    def total_score(self) -> float:
        return (
            self.atomicity_score * DIMENSION_WEIGHTS["atomicity"] +
            self.evidence_score * DIMENSION_WEIGHTS["evidence"] +
            self.format_score * DIMENSION_WEIGHTS["format"] +
            self.coherence_score * DIMENSION_WEIGHTS["coherence"]
        )
    
    @property
    def has_errors(self) -> bool:
        return any(i.severity == IssueSeverity.ERROR for i in self.issues)


@dataclass
class ValidationReport:
    """Reporte completo de validación."""
    note_validations: list[NoteValidation] = field(default_factory=list)
    
    @property
    def atomicity_score(self) -> float:
        if not self.note_validations:
            return 100.0
        return sum(v.atomicity_score for v in self.note_validations) / len(self.note_validations)
    
    @property
    def evidence_score(self) -> float:
        if not self.note_validations:
            return 100.0
        return sum(v.evidence_score for v in self.note_validations) / len(self.note_validations)
    
    @property
    def format_score(self) -> float:
        if not self.note_validations:
            return 100.0
        return sum(v.format_score for v in self.note_validations) / len(self.note_validations)
    
    @property
    def coherence_score(self) -> float:
        if not self.note_validations:
            return 100.0
        return sum(v.coherence_score for v in self.note_validations) / len(self.note_validations)
    
    @property
    def total_score(self) -> float:
        return (
            self.atomicity_score * DIMENSION_WEIGHTS["atomicity"] +
            self.evidence_score * DIMENSION_WEIGHTS["evidence"] +
            self.format_score * DIMENSION_WEIGHTS["format"] +
            self.coherence_score * DIMENSION_WEIGHTS["coherence"]
        )
    
    @property
    def is_passing(self) -> bool:
        has_errors = any(
            v.has_errors for v in self.note_validations
        )
        return self.total_score >= QUALITY_THRESHOLD and not has_errors
    
    @property
    def all_issues(self) -> list[ValidationIssue]:
        issues = []
        for v in self.note_validations:
            issues.extend(v.issues)
        return issues
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "atomicity_score": self.atomicity_score,
            "evidence_score": self.evidence_score,
            "format_score": self.format_score,
            "coherence_score": self.coherence_score,
            "total_score": self.total_score,
            "is_passing": self.is_passing,
            "issues": [i.to_dict() for i in self.all_issues],
        }


# =============================================================================
# VALIDADORES INDIVIDUALES
# =============================================================================

def validate_atomicity(note: dict[str, Any]) -> tuple[float, list[ValidationIssue]]:
    """
    Valida que la nota contenga una sola idea.
    
    Heurísticas:
    - Longitud del contenido (muy largo = posible multi-idea)
    - Número de párrafos
    - Headers internos (sugieren múltiples secciones)
    - Conectores de contraste ("sin embargo", "pero", "por otro lado")
    """
    issues = []
    content = note.get("content", "")
    note_id = note.get("id", "unknown")
    
    score = 100.0
    
    # 1. Longitud del contenido
    word_count = len(content.split())
    
    if word_count > 500:
        score -= 30
        issues.append(ValidationIssue(
            note_id=note_id,
            issue_type=IssueType.ATOMICITY,
            description=f"Nota muy larga ({word_count} palabras). Posible multi-idea.",
            suggestion="Dividir en notas más pequeñas de ~200 palabras",
            severity=IssueSeverity.WARNING,
        ))
    elif word_count > 350:
        score -= 15
        issues.append(ValidationIssue(
            note_id=note_id,
            issue_type=IssueType.ATOMICITY,
            description=f"Nota relativamente larga ({word_count} palabras)",
            suggestion="Considerar dividir si hay ideas claramente separables",
            severity=IssueSeverity.INFO,
        ))
    
    # 2. Headers internos (excluyendo el título)
    internal_headers = len(re.findall(r'\n##?\s+', content))
    if internal_headers > 1:
        score -= 25
        issues.append(ValidationIssue(
            note_id=note_id,
            issue_type=IssueType.ATOMICITY,
            description=f"Nota con {internal_headers} secciones internas",
            suggestion="Una nota atómica no debería tener sub-secciones",
            severity=IssueSeverity.WARNING,
        ))
    
    # 3. Conectores de contraste (pueden indicar ideas opuestas)
    contrast_patterns = [
        r'\bsin embargo\b',
        r'\bpero\b',
        r'\bpor otro lado\b',
        r'\ben contraste\b',
        r'\bno obstante\b',
        r'\bhowever\b',
        r'\bon the other hand\b',
    ]
    
    contrast_count = sum(
        len(re.findall(pattern, content, re.IGNORECASE))
        for pattern in contrast_patterns
    )
    
    if contrast_count > 2:
        score -= 20
        issues.append(ValidationIssue(
            note_id=note_id,
            issue_type=IssueType.ATOMICITY,
            description="Múltiples conectores de contraste sugieren ideas en conflicto",
            suggestion="Considerar separar en notas: una por cada perspectiva",
            severity=IssueSeverity.WARNING,
        ))
    
    # 4. Párrafos muy distintos
    paragraphs = [p for p in content.split('\n\n') if p.strip()]
    if len(paragraphs) > 4:
        score -= 15
        issues.append(ValidationIssue(
            note_id=note_id,
            issue_type=IssueType.ATOMICITY,
            description=f"Nota con {len(paragraphs)} párrafos",
            suggestion="Reducir a 2-3 párrafos enfocados en una idea",
            severity=IssueSeverity.INFO,
        ))
    
    return max(0, score), issues


def validate_evidence(note: dict[str, Any], source_content: str = "") -> tuple[float, list[ValidationIssue]]:
    """
    Valida que los claims estén respaldados por evidencia.
    
    Heurísticas:
    - Presencia de citas (bloques >)
    - Referencias a la fuente
    - Afirmaciones sin respaldo
    """
    issues = []
    content = note.get("content", "")
    note_id = note.get("id", "unknown")
    
    score = 100.0
    
    # 1. Presencia de citas
    has_quotes = bool(re.search(r'^>\s+', content, re.MULTILINE))
    has_source_ref = bool(re.search(r'\*(?:Fuente|Source|Ref):', content))
    
    if not has_quotes and not has_source_ref:
        score -= 25
        issues.append(ValidationIssue(
            note_id=note_id,
            issue_type=IssueType.EVIDENCE,
            description="Nota sin citas ni referencias a la fuente",
            suggestion="Añadir al menos una cita textual relevante",
            severity=IssueSeverity.WARNING,
        ))
    
    # 2. Afirmaciones fuertes sin respaldo
    strong_claims = [
        r'\bsiempre\b',
        r'\bnunca\b',
        r'\btodos?\b',
        r'\bninguno\b',
        r'\bes cierto que\b',
        r'\bse ha demostrado\b',
        r'\balways\b',
        r'\bnever\b',
        r'\ball\b',
        r'\bproven\b',
    ]
    
    claim_count = sum(
        len(re.findall(pattern, content, re.IGNORECASE))
        for pattern in strong_claims
    )
    
    if claim_count > 2:
        score -= 20
        issues.append(ValidationIssue(
            note_id=note_id,
            issue_type=IssueType.EVIDENCE,
            description="Múltiples afirmaciones absolutas que requieren respaldo",
            suggestion="Matizar afirmaciones o añadir evidencia específica",
            severity=IssueSeverity.WARNING,
        ))
    
    # 3. Contenido muy corto (puede faltar desarrollo)
    word_count = len(content.split())
    if word_count < 50:
        score -= 30
        issues.append(ValidationIssue(
            note_id=note_id,
            issue_type=IssueType.EVIDENCE,
            description=f"Nota muy corta ({word_count} palabras), posible falta de evidencia",
            suggestion="Expandir con más contexto y evidencia",
            severity=IssueSeverity.WARNING,
        ))
    
    return max(0, score), issues


def validate_format(note: dict[str, Any]) -> tuple[float, list[ValidationIssue]]:
    """
    Valida el formato Markdown y estructura.
    
    Heurísticas:
    - Frontmatter presente
    - Título claro
    - Markdown válido
    - Tags apropiados
    """
    issues = []
    content = note.get("content", "")
    frontmatter = note.get("frontmatter", {})
    note_id = note.get("id", "unknown")
    title = note.get("title", "")
    
    score = 100.0
    
    # 1. Título
    if not title:
        score -= 40
        issues.append(ValidationIssue(
            note_id=note_id,
            issue_type=IssueType.FORMAT,
            description="Nota sin título",
            suggestion="Añadir un título claro y específico",
            severity=IssueSeverity.ERROR,
        ))
    elif len(title) < 5:
        score -= 20
        issues.append(ValidationIssue(
            note_id=note_id,
            issue_type=IssueType.FORMAT,
            description="Título demasiado corto",
            suggestion="El título debe describir el contenido específico",
            severity=IssueSeverity.WARNING,
        ))
    elif len(title) > 80:
        score -= 10
        issues.append(ValidationIssue(
            note_id=note_id,
            issue_type=IssueType.FORMAT,
            description="Título demasiado largo",
            suggestion="Acortar a menos de 80 caracteres",
            severity=IssueSeverity.INFO,
        ))
    
    # 2. Frontmatter
    if not frontmatter:
        score -= 30
        issues.append(ValidationIssue(
            note_id=note_id,
            issue_type=IssueType.FORMAT,
            description="Nota sin frontmatter",
            suggestion="Añadir frontmatter YAML con tags y metadata",
            severity=IssueSeverity.WARNING,
        ))
    else:
        # Verificar campos requeridos
        if not frontmatter.get("tags"):
            score -= 10
            issues.append(ValidationIssue(
                note_id=note_id,
                issue_type=IssueType.FORMAT,
                description="Frontmatter sin tags",
                suggestion="Añadir al menos 2-3 tags relevantes",
                severity=IssueSeverity.INFO,
            ))
    
    # 3. Contenido vacío
    if not content or not content.strip():
        score -= 50
        issues.append(ValidationIssue(
            note_id=note_id,
            issue_type=IssueType.FORMAT,
            description="Nota sin contenido",
            suggestion="Añadir contenido a la nota",
            severity=IssueSeverity.ERROR,
        ))
    
    # 4. Markdown malformado
    # Verificar links rotos
    broken_links = re.findall(r'\[\[([^\]]*)\]\]', content)  # Wikilinks
    markdown_links = re.findall(r'\[([^\]]*)\]\(([^)]*)\)', content)  # Standard
    
    for link in markdown_links:
        if not link[1]:  # URL vacía
            score -= 10
            issues.append(ValidationIssue(
                note_id=note_id,
                issue_type=IssueType.FORMAT,
                description=f"Link con URL vacía: [{link[0]}]()",
                suggestion="Corregir o eliminar el link",
                severity=IssueSeverity.WARNING,
            ))
    
    return max(0, score), issues


def validate_coherence(
    note: dict[str, Any],
    existing_notes: list[dict[str, Any]] | None = None,
) -> tuple[float, list[ValidationIssue]]:
    """
    Valida coherencia con el conocimiento existente.
    
    Heurísticas:
    - Duplicación con notas existentes
    - Contradicciones
    - Consistencia de terminología
    """
    issues = []
    note_id = note.get("id", "unknown")
    title = note.get("title", "").lower()
    content = note.get("content", "").lower()
    
    score = 100.0
    existing_notes = existing_notes or []
    
    # 1. Detección de duplicados
    for existing in existing_notes:
        existing_title = existing.get("title", "").lower()
        existing_content = existing.get("content", "").lower()
        
        # Similitud de título
        title_words = set(title.split()) - {'de', 'la', 'el', 'los', 'las', 'un', 'una', 'the', 'a', 'an'}
        existing_words = set(existing_title.split()) - {'de', 'la', 'el', 'los', 'las', 'un', 'una', 'the', 'a', 'an'}
        
        if title_words and existing_words:
            jaccard = len(title_words & existing_words) / len(title_words | existing_words)
            
            if jaccard > 0.8:
                score -= 40
                issues.append(ValidationIssue(
                    note_id=note_id,
                    issue_type=IssueType.DUPLICATE,
                    description=f"Título muy similar a nota existente: {existing.get('title', '')}",
                    suggestion="Fusionar con la nota existente o diferenciar claramente",
                    severity=IssueSeverity.ERROR,
                ))
            elif jaccard > 0.5:
                score -= 15
                issues.append(ValidationIssue(
                    note_id=note_id,
                    issue_type=IssueType.COHERENCE,
                    description=f"Título similar a nota existente: {existing.get('title', '')}",
                    suggestion="Verificar que no sea duplicado y considerar enlazar",
                    severity=IssueSeverity.WARNING,
                ))
    
    # 2. Consistencia interna
    # Detectar definiciones contradictorias dentro de la misma nota
    definitions = re.findall(r'(\w+)\s+(?:es|son|se define|significa)\s+([^.]+)\.', content)
    
    term_definitions: dict[str, list[str]] = {}
    for term, definition in definitions:
        if term not in term_definitions:
            term_definitions[term] = []
        term_definitions[term].append(definition)
    
    for term, defs in term_definitions.items():
        if len(defs) > 1:
            score -= 15
            issues.append(ValidationIssue(
                note_id=note_id,
                issue_type=IssueType.COHERENCE,
                description=f"Múltiples definiciones para '{term}' en la misma nota",
                suggestion="Unificar en una sola definición clara",
                severity=IssueSeverity.WARNING,
            ))
    
    return max(0, score), issues


# =============================================================================
# VALIDACIÓN COMPLETA
# =============================================================================

def validate_note(
    note: dict[str, Any],
    source_content: str = "",
    existing_notes: list[dict[str, Any]] | None = None,
) -> NoteValidation:
    """
    Valida una nota individual en todas las dimensiones.
    
    Args:
        note: Nota a validar
        source_content: Contenido fuente original
        existing_notes: Notas existentes para comparar
        
    Returns:
        Resultado de validación
    """
    # Ejecutar cada validador
    atomicity_score, atomicity_issues = validate_atomicity(note)
    evidence_score, evidence_issues = validate_evidence(note, source_content)
    format_score, format_issues = validate_format(note)
    coherence_score, coherence_issues = validate_coherence(note, existing_notes)
    
    # Combinar issues
    all_issues = atomicity_issues + evidence_issues + format_issues + coherence_issues
    
    return NoteValidation(
        note_id=note.get("id", "unknown"),
        atomicity_score=atomicity_score,
        evidence_score=evidence_score,
        format_score=format_score,
        coherence_score=coherence_score,
        issues=all_issues,
    )


def validate_all_notes(
    notes: list[dict[str, Any]],
    source_content: str = "",
    existing_notes: list[dict[str, Any]] | None = None,
) -> ValidationReport:
    """
    Valida todas las notas y genera reporte completo.
    
    Args:
        notes: Lista de notas a validar
        source_content: Contenido fuente
        existing_notes: Notas existentes
        
    Returns:
        Reporte de validación
    """
    validations = []
    
    for note in notes:
        validation = validate_note(note, source_content, existing_notes)
        validations.append(validation)
    
    return ValidationReport(note_validations=validations)


# =============================================================================
# FUNCIÓN PRINCIPAL
# =============================================================================

def run_epistemic_validation(
    atomic_proposals: list[dict[str, Any]],
    ordered_class: str = "",
    graph_rag_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Función principal para validar notas atómicas.
    
    Args:
        atomic_proposals: Notas a validar
        ordered_class: Clase ordenada original
        graph_rag_context: Contexto del grafo
        
    Returns:
        Diccionario con reporte de validación para el state
    """
    context = graph_rag_context or {}
    existing_notes = context.get("similar_notes_data", [])
    
    report = validate_all_notes(
        notes=atomic_proposals,
        source_content=ordered_class,
        existing_notes=existing_notes,
    )
    
    return {
        "validation_report": report.to_dict(),
    }