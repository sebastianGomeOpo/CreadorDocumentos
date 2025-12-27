"""
assembler.py — El Ensamblador

Recolecta resultados de todos los Writers y los ensambla
en un documento coherente + notas individuales.

RESPONSABILIDAD:
- Esperar a que todos los Writers terminen (fan-in)
- Ordenar fragmentos por sequence_id
- Ensamblar el Draft_Clase.md completo
- Guardar notas individuales en section_notes/
- Generar reporte de ensamblaje

PRODUCTOS:
1. data/drafts/{source_id}_draft.md — Documento completo
2. data/section_notes/{source_id}/ — Notas individuales

CONEXIONES:
- Input: Lista de WriterResult (desordenados, del fan-in)
- Output: Rutas a productos finales
- Llamado por: phase1_graph.py (nodo assembler)
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from core.state_schema import WriterResult, MasterPlan


# =============================================================================
# CONFIGURACIÓN
# =============================================================================

DEFAULT_DRAFTS_DIR = Path("data/drafts")
DEFAULT_NOTES_DIR = Path("data/section_notes")


# =============================================================================
# CLASE PRINCIPAL
# =============================================================================

class Assembler:
    """
    Ensambla resultados de Writers en productos finales.
    """
    
    def __init__(
        self,
        drafts_dir: Path | str = DEFAULT_DRAFTS_DIR,
        notes_dir: Path | str = DEFAULT_NOTES_DIR,
    ):
        self.drafts_dir = Path(drafts_dir)
        self.notes_dir = Path(notes_dir)
        
        self.drafts_dir.mkdir(parents=True, exist_ok=True)
        self.notes_dir.mkdir(parents=True, exist_ok=True)
    
    def assemble(
        self,
        writer_results: list[WriterResult | dict],
        source_id: str,
        master_plan: MasterPlan | dict | None = None,
    ) -> dict[str, Any]:
        """
        Ensambla todos los resultados en productos finales.
        
        Args:
            writer_results: Lista de resultados (pueden estar desordenados)
            source_id: ID de la fuente
            master_plan: Plan maestro (para metadata)
            
        Returns:
            Diccionario con rutas y estadísticas
        """
        # Normalizar resultados a WriterResult
        results = self._normalize_results(writer_results)
        
        # Ordenar por sequence_id
        sorted_results = sorted(results, key=lambda r: r.sequence_id)
        
        # Ensamblar documento completo
        draft_path = self._assemble_draft(sorted_results, source_id, master_plan)
        
        # Guardar notas individuales
        notes_dir_path = self._save_section_notes(sorted_results, source_id)
        
        # Generar estadísticas
        stats = self._generate_stats(sorted_results)
        
        return {
            "draft_path": str(draft_path),
            "section_notes_dir": str(notes_dir_path),
            "total_sections": len(sorted_results),
            "total_words": stats["total_words"],
            "successful_sections": stats["successful"],
            "failed_sections": stats["failed"],
            "warnings": stats["all_warnings"],
        }
    
    def _normalize_results(
        self,
        results: list[WriterResult | dict],
    ) -> list[WriterResult]:
        """
        Convierte dicts a WriterResult si es necesario.
        
        NOTA: El writer_agent.py devuelve campos con nombres diferentes
        a los que espera WriterResult en state_schema.py. Este método
        hace el mapeo necesario.
        
        Mapeo de campos:
            writer_agent          →  WriterResult (state_schema)
            ─────────────────────────────────────────────────────
            topic_index           →  sequence_id
            markdown              →  compiled_markdown
            must_include_followed →  followed_must_include
            must_exclude_violated →  violated_must_exclude
            (generado)            →  topic_id
        """
        normalized = []
        for i, r in enumerate(results):
            if isinstance(r, dict):
                # Mapear campos del writer_agent al schema de WriterResult
                mapped = {
                    # Campos requeridos con mapeo de nombres
                    "sequence_id": r.get("topic_index", r.get("sequence_id", i)),
                    "topic_id": r.get("topic_id", f"topic_{r.get('topic_index', i):03d}"),
                    "topic_name": r.get("topic_name", "Sin nombre"),
                    "compiled_markdown": r.get("markdown", r.get("compiled_markdown", "")),
                    
                    # Campos opcionales
                    "word_count": r.get("word_count", 0),
                    "processing_time_ms": r.get("processing_time_ms", 0),
                    
                    # Mapeo de validación (nombres similares pero diferentes)
                    "followed_must_include": r.get("must_include_followed", r.get("followed_must_include", [])),
                    "violated_must_exclude": r.get("must_exclude_violated", r.get("violated_must_exclude", [])),
                    "warnings": r.get("warnings", []),
                    
                    # Estado
                    "success": not r.get("error") and r.get("coverage_complete", True),
                    "error_message": r.get("error", r.get("error_message")),
                }
                normalized.append(WriterResult(**mapped))
            else:
                normalized.append(r)
        return normalized
    
    def _assemble_draft(
        self,
        results: list[WriterResult],
        source_id: str,
        master_plan: MasterPlan | dict | None,
    ) -> Path:
        """
        Ensambla el documento completo.
        
        Args:
            results: Resultados ordenados
            source_id: ID de la fuente
            master_plan: Plan maestro
            
        Returns:
            Path al archivo draft
        """
        lines = []
        
        # Header del documento
        lines.append(self._generate_header(source_id, results, master_plan))
        lines.append("")
        lines.append("---")
        lines.append("")
        
        # Tabla de contenidos
        lines.append("## Contenido")
        lines.append("")
        for result in results:
            status = "✓" if result.success else "✗"
            lines.append(f"- [{status}] [{result.topic_name}](#{self._slugify(result.topic_name)})")
        lines.append("")
        lines.append("---")
        lines.append("")
        
        # Contenido de cada sección
        for i, result in enumerate(results):
            lines.append(result.compiled_markdown)
            lines.append("")
            
            # Separador entre secciones (excepto la última)
            if i < len(results) - 1:
                lines.append("---")
                lines.append("")
        
        # Footer con metadata
        lines.append("---")
        lines.append("")
        lines.append(self._generate_footer(results))
        
        # Escribir archivo
        draft_path = self.drafts_dir / f"{source_id}_draft.md"
        with open(draft_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        
        return draft_path
    
    def _save_section_notes(
        self,
        results: list[WriterResult],
        source_id: str,
    ) -> Path:
        """
        Guarda cada sección como nota individual.
        
        Args:
            results: Resultados ordenados
            source_id: ID de la fuente
            
        Returns:
            Path al directorio de notas
        """
        # Crear subdirectorio para este source
        source_notes_dir = self.notes_dir / source_id
        source_notes_dir.mkdir(parents=True, exist_ok=True)
        
        for result in results:
            note_content = self._format_section_note(result, source_id)
            
            # Nombre del archivo
            filename = f"{result.sequence_id:03d}_{self._slugify(result.topic_name)}.md"
            note_path = source_notes_dir / filename
            
            with open(note_path, "w", encoding="utf-8") as f:
                f.write(note_content)
        
        # Escribir índice
        self._write_notes_index(results, source_notes_dir, source_id)
        
        return source_notes_dir
    
    def _format_section_note(
        self,
        result: WriterResult,
        source_id: str,
    ) -> str:
        """Formatea una nota individual con frontmatter."""
        lines = []
        
        # Frontmatter YAML
        lines.append("---")
        lines.append(f"title: \"{result.topic_name}\"")
        lines.append(f"sequence: {result.sequence_id}")
        lines.append(f"source_id: \"{source_id}\"")
        lines.append(f"topic_id: \"{result.topic_id}\"")
        lines.append(f"word_count: {result.word_count}")
        lines.append(f"status: {'success' if result.success else 'error'}")
        lines.append(f"created_at: \"{datetime.now().isoformat()}\"")
        
        if result.warnings:
            lines.append("warnings:")
            for w in result.warnings:
                lines.append(f"  - \"{w}\"")
        
        lines.append("---")
        lines.append("")
        
        # Contenido
        lines.append(result.compiled_markdown)
        
        return "\n".join(lines)
    
    def _write_notes_index(
        self,
        results: list[WriterResult],
        notes_dir: Path,
        source_id: str,
    ) -> None:
        """Escribe un archivo índice para las notas."""
        lines = []
        
        lines.append(f"# Índice de Secciones: {source_id}")
        lines.append("")
        lines.append(f"*Generado: {datetime.now().strftime('%Y-%m-%d %H:%M')}*")
        lines.append("")
        lines.append("| # | Tema | Palabras | Estado |")
        lines.append("|---|------|----------|--------|")
        
        for result in results:
            status = "✅" if result.success else "❌"
            filename = f"{result.sequence_id:03d}_{self._slugify(result.topic_name)}.md"
            lines.append(
                f"| {result.sequence_id} | [{result.topic_name}]({filename}) | "
                f"{result.word_count} | {status} |"
            )
        
        lines.append("")
        
        # Estadísticas
        total_words = sum(r.word_count for r in results)
        success_count = sum(1 for r in results if r.success)
        
        lines.append("## Estadísticas")
        lines.append("")
        lines.append(f"- **Total secciones:** {len(results)}")
        lines.append(f"- **Exitosas:** {success_count}")
        lines.append(f"- **Con errores:** {len(results) - success_count}")
        lines.append(f"- **Palabras totales:** {total_words:,}")
        
        index_path = notes_dir / "_INDEX.md"
        with open(index_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
    
    def _generate_header(
        self,
        source_id: str,
        results: list[WriterResult],
        master_plan: MasterPlan | dict | None,
    ) -> str:
        """Genera el header del documento."""
        total_words = sum(r.word_count for r in results)
        
        lines = []
        lines.append(f"# Clase: {source_id}")
        lines.append("")
        lines.append(f"*Generado automáticamente el {datetime.now().strftime('%Y-%m-%d %H:%M')}*")
        lines.append("")
        lines.append(f"**Secciones:** {len(results)} | **Palabras:** {total_words:,}")
        
        return "\n".join(lines)
    
    def _generate_footer(self, results: list[WriterResult]) -> str:
        """Genera el footer con metadata."""
        lines = []
        
        lines.append("## Metadata de Generación")
        lines.append("")
        lines.append("```yaml")
        lines.append(f"generated_at: {datetime.now().isoformat()}")
        lines.append(f"total_sections: {len(results)}")
        lines.append(f"total_words: {sum(r.word_count for r in results)}")
        lines.append(f"successful_sections: {sum(1 for r in results if r.success)}")
        lines.append(f"average_processing_time_ms: {sum(r.processing_time_ms for r in results) // len(results) if results else 0}")
        lines.append("```")
        
        # Warnings si los hay
        all_warnings = []
        for r in results:
            for w in r.warnings:
                all_warnings.append(f"[{r.topic_name}] {w}")
        
        if all_warnings:
            lines.append("")
            lines.append("### Advertencias")
            lines.append("")
            for w in all_warnings:
                lines.append(f"- ⚠️ {w}")
        
        return "\n".join(lines)
    
    def _generate_stats(self, results: list[WriterResult]) -> dict[str, Any]:
        """Genera estadísticas del ensamblaje."""
        all_warnings = []
        for r in results:
            for w in r.warnings:
                all_warnings.append(f"[{r.topic_name}] {w}")
        
        return {
            "total_words": sum(r.word_count for r in results),
            "successful": sum(1 for r in results if r.success),
            "failed": sum(1 for r in results if not r.success),
            "total_processing_time_ms": sum(r.processing_time_ms for r in results),
            "all_warnings": all_warnings,
        }
    
    @staticmethod
    def _slugify(text: str) -> str:
        """Convierte texto a slug para URLs y filenames."""
        import re
        slug = text.lower()
        slug = re.sub(r'[áàäâ]', 'a', slug)
        slug = re.sub(r'[éèëê]', 'e', slug)
        slug = re.sub(r'[íìïî]', 'i', slug)
        slug = re.sub(r'[óòöô]', 'o', slug)
        slug = re.sub(r'[úùüû]', 'u', slug)
        slug = re.sub(r'[ñ]', 'n', slug)
        slug = re.sub(r'[^a-z0-9]+', '-', slug)
        slug = slug.strip('-')
        return slug[:50]  # Limitar longitud


# =============================================================================
# FUNCIÓN PARA EL NODO DEL GRAFO
# =============================================================================

def run_assembler(
    writer_results: list[dict],
    source_id: str,
    master_plan: dict | None = None,
    drafts_dir: Path | str = DEFAULT_DRAFTS_DIR,
    notes_dir: Path | str = DEFAULT_NOTES_DIR,
) -> dict[str, Any]:
    """
    Punto de entrada para el nodo assembler del grafo.
    
    Args:
        writer_results: Resultados de los writers (como dicts)
        source_id: ID de la fuente
        master_plan: Plan maestro (opcional)
        drafts_dir: Directorio de drafts
        notes_dir: Directorio de notas
        
    Returns:
        Diccionario con rutas y estadísticas
    """
    assembler = Assembler(drafts_dir, notes_dir)
    
    # Convertir MasterPlan si es dict
    plan = None
    if master_plan:
        if isinstance(master_plan, dict):
            plan = MasterPlan(**master_plan)
        else:
            plan = master_plan
    
    return assembler.assemble(writer_results, source_id, plan)