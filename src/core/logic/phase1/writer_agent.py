"""
writer_agent.py — Agente Redactor V3 (RAG Avanzado)

Redacta secciones del documento usando el pipeline RAG completo:
1. TopicRetriever para obtener Evidence Pack
2. Prompt estructurado con cobertura de facetas
3. Validación de directivas (must_include, must_exclude)

MIGRACIÓN desde V2.1:
- Usa TopicRetriever en lugar de búsqueda simple
- Recibe Evidence Pack con contexto estructural
- Valida cobertura de facetas required
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from dotenv import load_dotenv

load_dotenv()


# =============================================================================
# CONFIGURACIÓN
# =============================================================================

DEFAULT_VECTOR_DB_DIR = Path(os.getenv("DATA_PATH", "./data")) / "temp" / "hierarchical_index"
DEFAULT_K = 8
DEFAULT_MODEL = "gpt-4o-mini"


# =============================================================================
# ESTRUCTURAS DE DATOS
# =============================================================================

@dataclass
class WriterResult:
    """Resultado de la redacción de una sección."""
    topic_name: str
    topic_index: int
    markdown: str
    word_count: int
    
    # Validación de directivas
    must_include_followed: list[str]
    must_include_missing: list[str]
    must_exclude_violated: list[str]
    
    # Cobertura de facetas
    coverage_complete: bool
    coverage_pct: float
    facets_covered: list[str]
    facets_missing: list[str]
    
    # Métricas de retrieval
    chunks_used: int
    retrieval_metrics: dict
    
    # Warnings
    warnings: list[str]
    
    # Timestamps
    started_at: str
    completed_at: str


# =============================================================================
# UTILIDADES
# =============================================================================

def _get_llm(model: str = DEFAULT_MODEL, temperature: float = 0.3):
    """Inicializa el LLM."""
    try:
        from langchain_openai import ChatOpenAI
        
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise ValueError("OPENAI_API_KEY no configurada")
        
        return ChatOpenAI(
            model=model,
            temperature=temperature,
            api_key=api_key
        )
    except Exception as e:
        print(f"Error inicializando LLM: {e}")
        return None


def _format_list(items: list[str]) -> str:
    """Formatea lista para prompt."""
    if not items:
        return "(ninguno)"
    return "\n".join(f"- {item}" for item in items)


def _validate_output(
    markdown: str,
    must_include: list[str],
    must_exclude: list[str],
) -> tuple[list[str], list[str], list[str], list[str]]:
    """
    Valida que el output cumpla con las directivas.
    
    Returns:
        (followed, missing, violated, warnings)
    """
    markdown_lower = markdown.lower()
    
    # Check must_include
    followed = []
    missing = []
    for term in must_include:
        if term.lower() in markdown_lower:
            followed.append(term)
        else:
            missing.append(term)
    
    # Check must_exclude
    violated = []
    for term in must_exclude:
        if term.lower() in markdown_lower:
            violated.append(term)
    
    # Warnings
    warnings = []
    if missing:
        warnings.append(f"Faltan conceptos must_include: {', '.join(missing)}")
    if violated:
        warnings.append(f"Se mencionaron conceptos must_exclude: {', '.join(violated)}")
    
    word_count = len(markdown.split())
    if word_count < 100:
        warnings.append(f"Sección muy corta ({word_count} palabras)")
    
    return followed, missing, violated, warnings


# =============================================================================
# PROMPTS
# =============================================================================

WRITER_SYSTEM_PROMPT = """Eres un redactor técnico experto que transforma información dispersa en contenido educativo claro y estructurado.

Tu objetivo es crear una sección coherente y pedagógica a partir de la transcripcion proporcionada.

REGLAS:
1. Apoyate interprentando la información del contexto proporcionado
4. Mantén un tono profesional pero accesible
5. Estructura el contenido con subtítulos si es necesario
6. Incluye ejemplos cuando el contexto los proporcione

FORMATO:
- Usa Markdown para formateo
- El título principal ya está definido, no lo repitas
- Comienza directamente con el contenido"""

WRITER_USER_PROMPT = """## Tarea: Redactar Sección

**Tema:** {topic_name}

**Posición en documento:** Sección {topic_index} de {total_topics}
{navigation_context}

### Directivas

**DEBE incluir estos conceptos:**
{must_include}

**NO debe mencionar:**
{must_exclude}

### Cobertura de Evidencia

{coverage_info}

### Contexto Disponible

{context}

---

Redacta la sección "{topic_name}" siguiendo las directivas anteriores.
Asegúrate de cubrir todos los conceptos de DEBE incluir.
"""


# =============================================================================
# WRITER AGENT
# =============================================================================

class WriterAgent:
    """
    Agente que redacta secciones usando RAG avanzado.
    
    Pipeline:
    1. Obtiene Evidence Pack via TopicRetriever
    2. Construye prompt con contexto y directivas
    3. Genera contenido con LLM
    4. Valida contra must_include/must_exclude
    """
    
    def __init__(
        self,
        source_id: str,
        db_path: Path | str = DEFAULT_VECTOR_DB_DIR,
        model: str = DEFAULT_MODEL,
    ):
        self.source_id = source_id
        self.db_path = Path(db_path)
        self.model = model
        
        self._retriever = None
        self._llm = None
    
    @property
    def retriever(self):
        """Lazy loading del TopicRetriever."""
        if self._retriever is None:
            from core.logic.phase1.context_indexer import (
                ContextIndexer,
                TopicRetriever,
            )
            indexer = ContextIndexer(self.db_path)
            self._retriever = TopicRetriever(indexer, self.source_id)
        return self._retriever
    
    @property
    def llm(self):
        """Lazy loading del LLM."""
        if self._llm is None:
            self._llm = _get_llm(self.model)
        return self._llm
    
    def write_section(
        self,
        topic_name: str,
        topic_index: int,
        total_topics: int,
        key_concepts: list[str],
        must_include: list[str],
        must_exclude: list[str],
        navigation_context: Optional[dict] = None,
        target_chunks: int = DEFAULT_K,
    ) -> WriterResult:
        """
        Redacta una sección completa.
        
        Args:
            topic_name: Nombre del tema
            topic_index: Índice (0-based)
            total_topics: Total de temas
            key_concepts: Conceptos clave
            must_include: Conceptos obligatorios
            must_exclude: Conceptos prohibidos
            navigation_context: Contexto prev/next
            target_chunks: Chunks objetivo
            
        Returns:
            WriterResult con contenido y métricas
        """
        started_at = datetime.now().isoformat()
        warnings = []
        
        # 1. Obtener Evidence Pack
        try:
            retrieval_result = self.retriever.retrieve_for_topic(
                topic_name=topic_name,
                must_include=must_include,
                key_concepts=key_concepts,
                navigation_context=navigation_context,
                target_chunks=target_chunks,
            )
            
            evidence_pack = retrieval_result["evidence_pack"]
            formatted_context = retrieval_result["formatted_context"]
            coverage = retrieval_result["coverage"]
            metrics = retrieval_result["metrics"]
            
        except Exception as e:
            warnings.append(f"Error en retrieval: {str(e)}")
            formatted_context = "[Error recuperando contexto]"
            coverage = {
                "total_chunks": 0,
                "required_coverage": 0,
                "optional_coverage": 0,
                "missing_required": must_include,
                "is_complete": False,
            }
            metrics = {}
            evidence_pack = None
        
        # 2. Construir prompt
        nav_text = self._format_navigation(navigation_context)
        coverage_info = self._format_coverage(coverage)
        
        user_prompt = WRITER_USER_PROMPT.format(
            topic_name=topic_name,
            topic_index=topic_index + 1,
            total_topics=total_topics,
            navigation_context=nav_text,
            must_include=_format_list(must_include),
            must_exclude=_format_list(must_exclude),
            coverage_info=coverage_info,
            context=formatted_context,
        )
        
        # 3. Generar contenido
        try:
            from langchain_core.messages import SystemMessage, HumanMessage
            
            messages = [
                SystemMessage(content=WRITER_SYSTEM_PROMPT),
                HumanMessage(content=user_prompt),
            ]
            
            response = self.llm.invoke(messages)
            markdown = response.content.strip()
            
        except Exception as e:
            warnings.append(f"Error generando contenido: {str(e)}")
            markdown = f"# {topic_name}\n\n[Error: No se pudo generar contenido]"
        
        # 4. Validar output
        followed, missing, violated, validation_warnings = _validate_output(
            markdown, must_include, must_exclude
        )
        warnings.extend(validation_warnings)
        
        # 5. Construir resultado
        completed_at = datetime.now().isoformat()
        
        return WriterResult(
            topic_name=topic_name,
            topic_index=topic_index,
            markdown=markdown,
            word_count=len(markdown.split()),
            must_include_followed=followed,
            must_include_missing=missing,
            must_exclude_violated=violated,
            coverage_complete=coverage.get("is_complete", False),
            coverage_pct=coverage.get("required_coverage", 0),
            facets_covered=evidence_pack.facets_covered if evidence_pack else [],
            facets_missing=evidence_pack.facets_missing if evidence_pack else [],
            chunks_used=coverage.get("total_chunks", 0),
            retrieval_metrics=metrics,
            warnings=warnings,
            started_at=started_at,
            completed_at=completed_at,
        )
    
    def _format_navigation(self, nav_context: Optional[dict]) -> str:
        """Formatea contexto de navegación."""
        if not nav_context:
            return ""
        
        parts = []
        if nav_context.get("previous_topic"):
            parts.append(f"**Anterior:** {nav_context['previous_topic']}")
        if nav_context.get("next_topic"):
            parts.append(f"**Siguiente:** {nav_context['next_topic']}")
        
        if parts:
            return "\n".join(parts)
        return ""
    
    def _format_coverage(self, coverage: dict) -> str:
        """Formatea info de cobertura."""
        lines = []
        
        pct = coverage.get("required_coverage", 0)
        if pct >= 0.8:
            lines.append(f"✓ Cobertura alta ({pct:.0%} de conceptos obligatorios)")
        elif pct >= 0.5:
            lines.append(f"⚠ Cobertura parcial ({pct:.0%} de conceptos obligatorios)")
        else:
            lines.append(f"⚠ Cobertura baja ({pct:.0%} de conceptos obligatorios)")
        
        missing = coverage.get("missing_required", [])
        if missing:
            lines.append(f"⚠ Sin evidencia para: {', '.join(missing)}")
        
        lines.append(f"Fragmentos disponibles: {coverage.get('total_chunks', 0)}")
        
        return "\n".join(lines)


# =============================================================================
# FUNCIÓN PARA EL GRAFO
# =============================================================================

def run_writer_agent(task_state: dict) -> dict:
    """
    Ejecuta el Writer Agent para una tarea.
    
    Esta función es llamada por el grafo LangGraph.
    
    Args:
        task_state: Estado de la tarea con:
            - source_id: ID de la fuente
            - db_path: Ruta del índice
            - topic_name: Nombre del tema
            - topic_index: Índice del tema
            - total_topics: Total de temas
            - key_concepts: Conceptos clave
            - must_include: Conceptos obligatorios
            - must_exclude: Conceptos prohibidos
            - navigation: Contexto de navegación
            
    Returns:
        Dict con resultado para el estado del grafo
    """
    # Extraer parámetros
    source_id = task_state.get("source_id", "")
    db_path = task_state.get("db_path", DEFAULT_VECTOR_DB_DIR)
    
    topic_name = task_state.get("topic_name", "")
    topic_index = task_state.get("topic_index", 0)
    total_topics = task_state.get("total_topics", 1)
    
    key_concepts = task_state.get("key_concepts", [])
    must_include = task_state.get("must_include", [])
    must_exclude = task_state.get("must_exclude", [])
    
    navigation = task_state.get("navigation", {})
    
    # Crear y ejecutar agente
    agent = WriterAgent(
        source_id=source_id,
        db_path=db_path,
    )
    
    result = agent.write_section(
        topic_name=topic_name,
        topic_index=topic_index,
        total_topics=total_topics,
        key_concepts=key_concepts,
        must_include=must_include,
        must_exclude=must_exclude,
        navigation_context=navigation,
    )
    
    # Convertir a dict para el estado
    return {
        "topic_name": result.topic_name,
        "topic_index": result.topic_index,
        "markdown": result.markdown,
        "word_count": result.word_count,
        "must_include_followed": result.must_include_followed,
        "must_include_missing": result.must_include_missing,
        "must_exclude_violated": result.must_exclude_violated,
        "coverage_complete": result.coverage_complete,
        "coverage_pct": result.coverage_pct,
        "facets_covered": result.facets_covered,
        "facets_missing": result.facets_missing,
        "chunks_used": result.chunks_used,
        "retrieval_metrics": result.retrieval_metrics,
        "warnings": result.warnings,
        "started_at": result.started_at,
        "completed_at": result.completed_at,
    }


# =============================================================================
# FUNCIONES DE CONVENIENCIA
# =============================================================================

def create_writer(
    source_id: str,
    db_path: Path | str = DEFAULT_VECTOR_DB_DIR,
    model: str = DEFAULT_MODEL,
) -> WriterAgent:
    """Crea instancia del WriterAgent."""
    return WriterAgent(source_id, db_path, model)


def write_single_section(
    source_id: str,
    topic_name: str,
    must_include: list[str],
    key_concepts: list[str] = None,
    db_path: Path | str = DEFAULT_VECTOR_DB_DIR,
) -> WriterResult:
    """
    Función de conveniencia para redactar una sección.
    
    Args:
        source_id: ID de la fuente
        topic_name: Nombre del tema
        must_include: Conceptos obligatorios
        key_concepts: Conceptos clave
        db_path: Ruta del índice
        
    Returns:
        WriterResult
    """
    agent = WriterAgent(source_id, db_path)
    return agent.write_section(
        topic_name=topic_name,
        topic_index=0,
        total_topics=1,
        key_concepts=key_concepts or [],
        must_include=must_include,
        must_exclude=[],
    )