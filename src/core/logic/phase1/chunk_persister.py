"""
chunk_persister.py — El Cirujano

Persiste chunks de texto a disco y libera RAM.
Este módulo es puramente I/O, sin lógica de LLM.

RESPONSABILIDAD:
- Recibir contenido segmentado del MasterPlan
- Escribir cada chunk como archivo físico en data/temp/chunks/
- Retornar rutas a los archivos
- Limpiar referencias en memoria para liberar contexto

PRINCIPIO:
"Lo que está en disco no ocupa ventana de contexto"

CONEXIONES:
- Input: MasterPlan + raw_content
- Output: Lista de rutas a archivos chunk_XX.txt
- Llamado por: phase1_graph.py (nodo chunk_persister)
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from core.state_schema import MasterPlan, TopicDirective


# =============================================================================
# CONFIGURACIÓN
# =============================================================================

DEFAULT_CHUNKS_DIR = Path("data/temp/chunks")
CHUNK_FILE_PATTERN = "chunk_{sequence_id:03d}_{topic_id}.txt"
MANIFEST_FILE = "chunks_manifest.json"


# =============================================================================
# CLASE PRINCIPAL
# =============================================================================

class ChunkPersister:
    """
    Gestiona la persistencia de chunks a disco.
    
    Workflow:
    1. Recibe MasterPlan y contenido crudo
    2. Segmenta contenido según el plan
    3. Escribe cada segmento a disco
    4. Retorna rutas para los Workers
    """
    
    def __init__(self, base_path: Path | str = DEFAULT_CHUNKS_DIR):
        self.chunks_dir = Path(base_path)
        self.chunks_dir.mkdir(parents=True, exist_ok=True)
    
    def persist_chunks(
        self,
        raw_content: str,
        master_plan: MasterPlan,
    ) -> list[dict[str, Any]]:
        """
        Persiste chunks a disco basándose en el MasterPlan.
        
        Args:
            raw_content: Texto crudo completo
            master_plan: Plan con directivas de segmentación
            
        Returns:
            Lista de {sequence_id, topic_id, chunk_path, word_count}
        """
        # Limpiar chunks anteriores de este source
        self._clean_previous_chunks(master_plan.source_id)
        
        # Segmentar contenido
        segments = self._segment_content(raw_content, master_plan)
        
        # Persistir cada segmento
        chunk_infos = []
        manifest_entries = []
        
        for segment in segments:
            chunk_path = self._write_chunk(segment)
            
            chunk_info = {
                "sequence_id": segment["sequence_id"],
                "topic_id": segment["topic_id"],
                "topic_name": segment["topic_name"],
                "chunk_path": str(chunk_path),
                "word_count": len(segment["content"].split()),
                "content_hash": hashlib.sha256(segment["content"].encode()).hexdigest()[:12],
            }
            chunk_infos.append(chunk_info)
            manifest_entries.append(chunk_info)
        
        # Escribir manifiesto para auditoría
        self._write_manifest(master_plan.source_id, manifest_entries)
        
        return chunk_infos
    
    def _segment_content(
        self,
        raw_content: str,
        master_plan: MasterPlan,
    ) -> list[dict[str, Any]]:
        """
        Segmenta el contenido según las directivas del plan.
        
        Estrategia:
        1. Buscar marcadores naturales (headers, párrafos)
        2. Asignar secciones a topics según keywords
        3. Si no hay marcadores claros, dividir proporcionalmente
        """
        segments = []
        
        # Intentar detectar secciones por headers
        header_pattern = r'\n##?\s+([^\n]+)'
        sections_by_header = self._split_by_headers(raw_content)
        
        if len(sections_by_header) >= len(master_plan.topics):
            # Hay suficientes secciones, asignar a topics
            segments = self._assign_sections_to_topics(
                sections_by_header, 
                master_plan.topics
            )
        else:
            # Fallback: dividir por párrafos y agrupar
            segments = self._segment_by_paragraphs(
                raw_content, 
                master_plan.topics
            )
        
        return segments
    
    def _split_by_headers(self, content: str) -> list[dict[str, str]]:
        """Divide contenido por headers markdown."""
        sections = []
        
        # Patrón para detectar headers nivel 1 o 2
        pattern = r'(?:^|\n)(#{1,2}\s+[^\n]+)'
        
        parts = re.split(pattern, content)
        
        current_header = "Introducción"
        current_content = []
        
        for i, part in enumerate(parts):
            part = part.strip()
            if not part:
                continue
                
            if re.match(r'^#{1,2}\s+', part):
                # Es un header
                if current_content:
                    sections.append({
                        "header": current_header,
                        "content": "\n".join(current_content).strip()
                    })
                current_header = re.sub(r'^#{1,2}\s+', '', part)
                current_content = []
            else:
                current_content.append(part)
        
        # Última sección
        if current_content:
            sections.append({
                "header": current_header,
                "content": "\n".join(current_content).strip()
            })
        
        # Si no hay sections, todo es una sola sección
        if not sections:
            sections.append({
                "header": "Contenido Principal",
                "content": content.strip()
            })
        
        return sections
    
    def _assign_sections_to_topics(
        self,
        sections: list[dict[str, str]],
        topics: list[TopicDirective],
    ) -> list[dict[str, Any]]:
        """Asigna secciones a topics según similitud."""
        segments = []
        used_sections = set()
        
        for topic in topics:
            best_match_idx = None
            best_score = -1
            
            # Buscar la sección más relevante para este topic
            for i, section in enumerate(sections):
                if i in used_sections:
                    continue
                
                score = self._calculate_relevance(
                    section["header"] + " " + section["content"][:200],
                    topic
                )
                
                if score > best_score:
                    best_score = score
                    best_match_idx = i
            
            if best_match_idx is not None:
                used_sections.add(best_match_idx)
                section = sections[best_match_idx]
                
                # Construir contenido con header
                full_content = f"## {section['header']}\n\n{section['content']}"
                
                segments.append({
                    "sequence_id": topic.sequence_id,
                    "topic_id": topic.topic_id,
                    "topic_name": topic.topic_name,
                    "content": full_content,
                })
            else:
                # No hay sección, crear placeholder
                segments.append({
                    "sequence_id": topic.sequence_id,
                    "topic_id": topic.topic_id,
                    "topic_name": topic.topic_name,
                    "content": f"## {topic.topic_name}\n\n[Contenido no detectado automáticamente]",
                })
        
        # Ordenar por sequence_id
        segments.sort(key=lambda x: x["sequence_id"])
        
        return segments
    
    def _segment_by_paragraphs(
        self,
        content: str,
        topics: list[TopicDirective],
    ) -> list[dict[str, Any]]:
        """
        Fallback: divide por párrafos y distribuye entre topics.
        """
        paragraphs = [p.strip() for p in content.split('\n\n') if p.strip()]
        
        if not paragraphs:
            paragraphs = [content]
        
        # Distribuir párrafos entre topics
        paragraphs_per_topic = max(1, len(paragraphs) // len(topics))
        segments = []
        
        para_idx = 0
        for topic in topics:
            # Tomar párrafos para este topic
            topic_paragraphs = paragraphs[para_idx:para_idx + paragraphs_per_topic]
            para_idx += paragraphs_per_topic
            
            # Si es el último topic, tomar los restantes
            if topic.sequence_id == len(topics):
                topic_paragraphs.extend(paragraphs[para_idx:])
            
            content_text = "\n\n".join(topic_paragraphs) if topic_paragraphs else "[Sin contenido]"
            full_content = f"## {topic.topic_name}\n\n{content_text}"
            
            segments.append({
                "sequence_id": topic.sequence_id,
                "topic_id": topic.topic_id,
                "topic_name": topic.topic_name,
                "content": full_content,
            })
        
        return segments
    
    def _calculate_relevance(
        self,
        text: str,
        topic: TopicDirective,
    ) -> float:
        """Calcula relevancia de un texto para un topic."""
        text_lower = text.lower()
        score = 0.0
        
        # Coincidencia con nombre del topic
        topic_words = set(topic.topic_name.lower().split())
        for word in topic_words:
            if len(word) > 3 and word in text_lower:
                score += 2.0
        
        # Coincidencia con key_concepts
        for concept in topic.key_concepts:
            if concept.lower() in text_lower:
                score += 1.5
        
        # Coincidencia con must_include
        for term in topic.must_include:
            if term.lower() in text_lower:
                score += 1.0
        
        return score
    
    def _write_chunk(self, segment: dict[str, Any]) -> Path:
        """Escribe un chunk a disco."""
        filename = CHUNK_FILE_PATTERN.format(
            sequence_id=segment["sequence_id"],
            topic_id=segment["topic_id"]
        )
        chunk_path = self.chunks_dir / filename
        
        with open(chunk_path, "w", encoding="utf-8") as f:
            f.write(segment["content"])
        
        return chunk_path
    
    def _write_manifest(
        self,
        source_id: str,
        entries: list[dict[str, Any]],
    ) -> Path:
        """Escribe manifiesto de chunks para auditoría."""
        manifest = {
            "source_id": source_id,
            "created_at": datetime.now().isoformat(),
            "total_chunks": len(entries),
            "chunks": entries,
        }
        
        manifest_path = self.chunks_dir / f"{source_id}_{MANIFEST_FILE}"
        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2, ensure_ascii=False)
        
        return manifest_path
    
    def _clean_previous_chunks(self, source_id: str) -> None:
        """Limpia chunks anteriores del mismo source."""
        for file in self.chunks_dir.glob("chunk_*.txt"):
            file.unlink()
        
        for manifest in self.chunks_dir.glob(f"{source_id}_*.json"):
            manifest.unlink()
    
    def read_chunk(self, chunk_path: str | Path) -> str:
        """Lee un chunk desde disco."""
        with open(chunk_path, "r", encoding="utf-8") as f:
            return f.read()
    
    def cleanup_all(self) -> int:
        """Limpia todos los chunks temporales."""
        count = 0
        for file in self.chunks_dir.glob("*"):
            if file.is_file():
                file.unlink()
                count += 1
        return count
    
    def get_stats(self) -> dict[str, Any]:
        """Obtiene estadísticas del directorio de chunks."""
        chunk_files = list(self.chunks_dir.glob("chunk_*.txt"))
        total_size = sum(f.stat().st_size for f in chunk_files)
        
        return {
            "chunks_dir": str(self.chunks_dir),
            "total_chunks": len(chunk_files),
            "total_size_bytes": total_size,
            "manifest_files": len(list(self.chunks_dir.glob("*_manifest.json"))),
        }


# =============================================================================
# FUNCIÓN DE CONVENIENCIA PARA EL GRAFO
# =============================================================================

def persist_chunks_to_disk(
    raw_content: str,
    master_plan: MasterPlan,
    base_path: Path | str = DEFAULT_CHUNKS_DIR,
) -> list[dict[str, Any]]:
    """
    Función de entrada para el nodo chunk_persister del grafo.
    
    Args:
        raw_content: Texto crudo
        master_plan: Plan maestro
        base_path: Directorio de chunks
        
    Returns:
        Lista de info de chunks con rutas
    """
    persister = ChunkPersister(base_path)
    return persister.persist_chunks(raw_content, master_plan)


def read_chunk_from_disk(chunk_path: str | Path) -> str:
    """Lee un chunk individual desde disco."""
    with open(chunk_path, "r", encoding="utf-8") as f:
        return f.read()


def cleanup_temp_chunks(base_path: Path | str = DEFAULT_CHUNKS_DIR) -> int:
    """Limpia todos los chunks temporales."""
    persister = ChunkPersister(base_path)
    return persister.cleanup_all()