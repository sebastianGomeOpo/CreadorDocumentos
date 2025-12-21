"""
atomic_generator.py — Generador de Notas Atómicas

Este módulo genera las notas atómicas siguiendo el plan creado
por atomic_planner.py, con enlaces tipados y formato Zettelkasten.

RESPONSABILIDAD:
Transformar cada item del plan en una nota atómica completa,
incluyendo:
- Contenido redactado
- Frontmatter YAML
- Enlaces propuestos a otras notas
- Referencias a chunks fuente

FORMATO ZETTELKASTEN:
- Título claro y específico
- Una idea por nota
- Auto-contenida pero conectada
- Metadata rica para navegación

CONEXIONES:
- Llamado por: phase2_graph.py (nodo atomic_generator)
- Input de: atomic_planner.py (plan)
- Output usado por: epistemic_validator.py
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.language_models import BaseChatModel

from core.state_schema import LinkType, generate_note_id


# =============================================================================
# PROMPTS
# =============================================================================

ATOMIC_GENERATOR_SYSTEM_PROMPT = """Eres un experto en crear notas Zettelkasten de alta calidad. Tu tarea es generar una nota atómica a partir del plan y contenido proporcionados.

PRINCIPIOS DE UNA BUENA NOTA ATÓMICA:
1. UNA IDEA PRINCIPAL: La nota debe expresar un solo concepto claro
2. AUTO-CONTENIDA: Debe entenderse sin necesidad de leer otras notas
3. CONCISA: Entre 100-300 palabras típicamente
4. ENLAZABLE: Debe poder conectarse con otras ideas
5. PERMANENTE: Escrita para entenderse en el futuro

ESTRUCTURA DE LA NOTA:
1. Título: Claro, específico, que capture la esencia
2. Cuerpo: Explicación directa de la idea
3. Evidencia: Citas o referencias al material fuente
4. Enlaces: Conexiones con otras ideas (tipadas)

TIPOS DE ENLACES:
- defines: Esta nota define un concepto usado en otra
- contrasts: Contrasta o contradice otra nota
- depends_on: Requiere entender otra nota primero
- exemplifies: Es un ejemplo de otra nota
- refutes: Refuta la afirmación de otra nota
- applies: Aplica el concepto de otra nota
- extends: Extiende o profundiza otra nota

FORMATO DE SALIDA (JSON):
{
  "title": "Título de la nota",
  "content": "Contenido en Markdown...",
  "tags": ["tag1", "tag2"],
  "proposed_links": [
    {
      "target_title": "Título de nota a enlazar",
      "link_type": "defines|contrasts|depends_on|exemplifies|refutes|applies|extends",
      "rationale": "Por qué este enlace"
    }
  ],
  "key_quote": "Cita textual más relevante del material fuente"
}"""

ATOMIC_GENERATOR_USER_PROMPT = """## Información del Plan:
- Título propuesto: {proposed_title}
- Tipo de nota: {note_type}
- Razón: {rationale}

## Contenido Fuente (extracto relevante):
{source_content}

## Notas Existentes Relacionadas:
{related_notes}

## Instrucciones:
Genera una nota atómica de alta calidad siguiendo el plan.
- Mantén el foco en UNA idea
- Sé conciso pero completo
- Propón enlaces significativos

Responde SOLO con el JSON."""


# =============================================================================
# GENERACIÓN HEURÍSTICA (sin LLM)
# =============================================================================

def generate_note_heuristic(
    plan_item: dict[str, Any],
    source_content: str,
    lesson_id: str,
    related_notes: list[str] | None = None,
) -> dict[str, Any]:
    """
    Genera una nota atómica usando heurísticas (sin LLM).
    
    Estrategia:
    1. Extraer contenido relevante de la fuente
    2. Estructurar en formato Zettelkasten
    3. Generar frontmatter
    4. Proponer enlaces básicos
    
    Args:
        plan_item: Item del plan de atomización
        source_content: Contenido de la clase ordenada
        lesson_id: ID de la lección fuente
        related_notes: IDs de notas relacionadas
        
    Returns:
        Nota atómica como diccionario
    """
    related_notes = related_notes or []
    
    title = plan_item.get("proposed_title", "Sin título")
    topic_id = plan_item.get("topic_id", "topic_001")
    note_type = plan_item.get("type", "concept")
    
    # Generar ID determinístico
    note_id = generate_note_id(title, lesson_id)
    
    # Extraer contenido relevante
    # Buscar sección que coincida con el título
    content_extracted = extract_relevant_content(source_content, title)
    
    # Si no encontramos contenido específico, usar el inicio
    if not content_extracted:
        paragraphs = source_content.split('\n\n')
        content_extracted = paragraphs[0] if paragraphs else source_content[:500]
    
    # Construir cuerpo de la nota
    body_parts = []
    
    # Introducción basada en tipo
    if note_type == "concept":
        body_parts.append(f"**{title}** es un concepto fundamental que se refiere a:\n")
    elif note_type == "example":
        body_parts.append(f"Este ejemplo ilustra {title.replace('Ejemplo: ', '')}:\n")
    elif note_type == "application":
        body_parts.append(f"La aplicación práctica de este concepto incluye:\n")
    elif note_type == "contrast":
        body_parts.append(f"En contraste con otras perspectivas:\n")
    else:
        body_parts.append("")
    
    # Contenido principal (resumido)
    content_summary = summarize_content(content_extracted, max_words=200)
    body_parts.append(content_summary)
    
    # Cita si hay algo destacable
    quote = extract_key_quote(content_extracted)
    if quote:
        body_parts.append(f"\n> {quote}")
    
    # Referencia a la fuente
    body_parts.append(f"\n\n---\n*Fuente: {lesson_id}*")
    
    content = "\n".join(body_parts)
    
    # Frontmatter
    frontmatter = {
        "tags": generate_tags(title, note_type),
        "status": "draft",
        "type": note_type,
        "source_lesson": lesson_id,
        "topic": topic_id,
    }
    
    # Extraer chunks relevantes (IDs simulados)
    chunk_ids = [f"chunk_{topic_id}_{i}" for i in range(1, 3)]
    
    return {
        "id": note_id,
        "title": title,
        "content": content,
        "frontmatter": frontmatter,
        "source_id": lesson_id,
        "chunk_ids": chunk_ids,
        "created_at": datetime.now().isoformat(),
    }


def extract_relevant_content(source: str, title: str) -> str:
    """Extrae contenido relevante basado en el título."""
    # Buscar sección con el título
    title_lower = title.lower()
    
    # Buscar headers que coincidan
    sections = re.split(r'\n##?\s+', source)
    
    for section in sections:
        lines = section.strip().split('\n')
        if lines:
            header = lines[0].lower()
            # Coincidencia parcial
            if any(word in header for word in title_lower.split() if len(word) > 3):
                return section
    
    # Buscar párrafos que mencionen el concepto
    paragraphs = source.split('\n\n')
    relevant = []
    
    for para in paragraphs:
        if any(word in para.lower() for word in title_lower.split() if len(word) > 3):
            relevant.append(para)
            if len(relevant) >= 2:
                break
    
    return '\n\n'.join(relevant) if relevant else ""


def summarize_content(content: str, max_words: int = 200) -> str:
    """Resume contenido a un máximo de palabras."""
    words = content.split()
    
    if len(words) <= max_words:
        return content
    
    # Tomar primeras oraciones hasta el límite
    sentences = re.split(r'(?<=[.!?])\s+', content)
    result = []
    word_count = 0
    
    for sentence in sentences:
        sentence_words = len(sentence.split())
        if word_count + sentence_words > max_words:
            break
        result.append(sentence)
        word_count += sentence_words
    
    return ' '.join(result) + ('...' if len(result) < len(sentences) else '')


def extract_key_quote(content: str) -> str | None:
    """Extrae una cita clave del contenido."""
    # Buscar texto entre comillas
    quotes = re.findall(r'"([^"]{20,100})"', content)
    if quotes:
        return quotes[0]
    
    # Buscar definiciones
    definitions = re.findall(r'([A-Z][^.]*(?:es|son|se define|significa)[^.]{10,80}\.)', content)
    if definitions:
        return definitions[0]
    
    return None


def generate_tags(title: str, note_type: str) -> list[str]:
    """Genera tags basados en título y tipo."""
    tags = [note_type]
    
    # Tags del título
    words = re.findall(r'\b[a-záéíóú]{4,}\b', title.lower())
    # Filtrar palabras comunes
    stopwords = {'ejemplo', 'aplicación', 'introducción', 'sobre', 'para', 'como'}
    tags.extend([w for w in words if w not in stopwords][:3])
    
    return list(set(tags))


# =============================================================================
# GENERACIÓN DE ENLACES
# =============================================================================

def generate_links(
    notes: list[dict[str, Any]],
    existing_notes: list[str] | None = None,
) -> list[dict[str, Any]]:
    """
    Genera enlaces entre las notas propuestas.
    
    Estrategia:
    1. Enlaces entre notas del mismo topic (depends_on, exemplifies)
    2. Enlaces a notas existentes similares
    3. Enlaces de contraste si hay conceptos opuestos
    
    Args:
        notes: Notas generadas
        existing_notes: IDs de notas existentes
        
    Returns:
        Lista de enlaces propuestos
    """
    existing_notes = existing_notes or []
    links = []
    
    # Agrupar notas por topic
    by_topic: dict[str, list[dict]] = {}
    for note in notes:
        topic = note.get("frontmatter", {}).get("topic", "unknown")
        if topic not in by_topic:
            by_topic[topic] = []
        by_topic[topic].append(note)
    
    # Generar enlaces dentro de cada topic
    for topic, topic_notes in by_topic.items():
        if len(topic_notes) < 2:
            continue
        
        # Ordenar por tipo: concept -> example -> application
        type_order = {"concept": 0, "synthesis": 1, "example": 2, "application": 3, "contrast": 4}
        sorted_notes = sorted(
            topic_notes,
            key=lambda n: type_order.get(n.get("frontmatter", {}).get("type", ""), 99)
        )
        
        # El concepto principal
        main_note = sorted_notes[0]
        
        for other_note in sorted_notes[1:]:
            other_type = other_note.get("frontmatter", {}).get("type", "")
            
            # Determinar tipo de enlace
            if other_type == "example":
                link_type = LinkType.EXEMPLIFIES
                rationale = "Este ejemplo ilustra el concepto principal"
            elif other_type == "application":
                link_type = LinkType.APPLIES
                rationale = "Esta aplicación usa el concepto principal"
            elif other_type == "contrast":
                link_type = LinkType.CONTRASTS
                rationale = "Ofrece una perspectiva diferente"
            else:
                link_type = LinkType.DEPENDS_ON
                rationale = "Se relaciona con el concepto principal"
            
            links.append({
                "source_note_id": other_note["id"],
                "target_note_id": main_note["id"],
                "link_type": link_type.value,
                "rationale": rationale,
                "confidence": 0.8,
            })
    
    # Enlaces entre topics diferentes (más especulativos)
    all_notes = list(notes)
    for i, note_a in enumerate(all_notes):
        for note_b in all_notes[i+1:]:
            topic_a = note_a.get("frontmatter", {}).get("topic", "")
            topic_b = note_b.get("frontmatter", {}).get("topic", "")
            
            # Solo si son de topics diferentes
            if topic_a == topic_b:
                continue
            
            # Buscar palabras en común en títulos
            words_a = set(note_a["title"].lower().split())
            words_b = set(note_b["title"].lower().split())
            common = words_a & words_b - {'de', 'la', 'el', 'los', 'las', 'un', 'una'}
            
            if len(common) >= 1:
                links.append({
                    "source_note_id": note_a["id"],
                    "target_note_id": note_b["id"],
                    "link_type": LinkType.RELATES.value,
                    "rationale": f"Comparten concepto: {', '.join(common)}",
                    "confidence": 0.5,
                })
    
    return links


# =============================================================================
# GENERACIÓN CON LLM
# =============================================================================

async def generate_note_llm(
    plan_item: dict[str, Any],
    source_content: str,
    lesson_id: str,
    related_notes: list[dict[str, Any]],
    llm: BaseChatModel,
) -> dict[str, Any]:
    """
    Genera una nota atómica usando un LLM.
    
    Args:
        plan_item: Item del plan
        source_content: Contenido fuente
        lesson_id: ID de la lección
        related_notes: Notas relacionadas existentes
        llm: Modelo de lenguaje
        
    Returns:
        Nota generada
    """
    import json
    
    # Formatear notas relacionadas
    related_str = "\n".join([
        f"- {n.get('title', 'Sin título')}"
        for n in related_notes[:5]
    ]) or "No hay notas relacionadas."
    
    # Extraer contenido relevante
    relevant_content = extract_relevant_content(
        source_content, 
        plan_item.get("proposed_title", "")
    )
    
    messages = [
        SystemMessage(content=ATOMIC_GENERATOR_SYSTEM_PROMPT),
        HumanMessage(content=ATOMIC_GENERATOR_USER_PROMPT.format(
            proposed_title=plan_item.get("proposed_title", ""),
            note_type=plan_item.get("type", "concept"),
            rationale=plan_item.get("rationale", ""),
            source_content=relevant_content[:3000],
            related_notes=related_str,
        )),
    ]
    
    response = await llm.ainvoke(messages)
    response_text = response.content
    
    # Parsear JSON
    json_match = re.search(r'```(?:json)?\s*([\s\S]*?)\s*```', response_text)
    if json_match:
        json_str = json_match.group(1)
    else:
        json_str = response_text
    
    try:
        data = json.loads(json_str)
    except json.JSONDecodeError:
        # Fallback
        return generate_note_heuristic(plan_item, source_content, lesson_id)
    
    # Construir nota
    title = data.get("title", plan_item.get("proposed_title", "Sin título"))
    note_id = generate_note_id(title, lesson_id)
    
    return {
        "id": note_id,
        "title": title,
        "content": data.get("content", ""),
        "frontmatter": {
            "tags": data.get("tags", []),
            "status": "draft",
            "type": plan_item.get("type", "concept"),
            "source_lesson": lesson_id,
        },
        "source_id": lesson_id,
        "chunk_ids": [],
        "created_at": datetime.now().isoformat(),
        "_proposed_links": data.get("proposed_links", []),
        "_key_quote": data.get("key_quote"),
    }


# =============================================================================
# FUNCIÓN PRINCIPAL
# =============================================================================

def generate_atomic_notes(
    atomic_plan: list[dict[str, Any]],
    ordered_class: str,
    lesson_id: str,
    graph_rag_context: dict[str, Any] | None = None,
    llm: BaseChatModel | None = None,
) -> dict[str, Any]:
    """
    Función principal para generar todas las notas atómicas.
    
    Args:
        atomic_plan: Plan de atomización
        ordered_class: Contenido de la clase ordenada
        lesson_id: ID de la lección
        graph_rag_context: Contexto del GraphRAG
        llm: Modelo de lenguaje (opcional)
        
    Returns:
        Diccionario con notas y enlaces para el state
    """
    context = graph_rag_context or {}
    similar_notes = context.get("similar_notes", [])
    
    notes = []
    
    # Generar cada nota
    for plan_item in atomic_plan:
        note = generate_note_heuristic(
            plan_item=plan_item,
            source_content=ordered_class,
            lesson_id=lesson_id,
            related_notes=similar_notes,
        )
        notes.append(note)
    
    # Generar enlaces
    links = generate_links(notes, similar_notes)
    
    # Detectar MOC updates
    moc_updates = []
    for note in notes:
        if note.get("frontmatter", {}).get("type") == "concept":
            topic = note.get("frontmatter", {}).get("topic", "")
            moc_updates.append({
                "moc_id": f"MOC_{topic}",
                "moc_path": f"mocs/MOC_{topic}.md",
                "action": "add_link",
                "details": {
                    "note_id": note["id"],
                    "section": "Conceptos",
                }
            })
    
    return {
        "atomic_proposals": notes,
        "linking_matrix": links,
        "moc_updates": moc_updates[:5],  # Limitar MOC updates
    }