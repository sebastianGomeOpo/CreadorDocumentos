"""
vault_io.py — Escritura Atómica al Vault de Obsidian

Este módulo gestiona todas las escrituras al vault de Obsidian,
garantizando atomicidad mediante el WAL.

PRINCIPIO FUNDAMENTAL:
O se escriben TODAS las notas de un bundle, o no se escribe ninguna.
Esto evita estados corruptos donde una nota referencia a otra que no existe.

CONEXIONES:
- Usa: wal.py para transacciones
- Lee/escribe: data/vault/
- Llamado por: Phase2Graph (nodo Scribe)
"""

from __future__ import annotations

import hashlib
import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from core.state_schema import AtomicNote, MOCUpdate, Phase2Bundle, ProposedLink
from core.storage.wal import FileOperation, WriteAheadLog


class VaultWriter:
    """
    Gestor de escrituras al vault de Obsidian.
    
    Todas las escrituras son transaccionales:
    1. Se registran en el WAL
    2. Se escriben a un directorio temporal
    3. Se renombran atómicamente al vault
    4. Se marca la transacción como completada
    
    Si algo falla, el WAL permite hacer rollback.
    """
    
    def __init__(self, base_path: Path | str, vault_subdir: str = "vault"):
        self.base_path = Path(base_path)
        self.vault_path = self.base_path / vault_subdir
        self.wal = WriteAheadLog(self.base_path)
        
        # Estructura del vault
        self.notes_path = self.vault_path / "notes"
        self.literature_path = self.vault_path / "literature"
        self.mocs_path = self.vault_path / "mocs"
        
        # Crear estructura si no existe
        for p in [self.notes_path, self.literature_path, self.mocs_path]:
            p.mkdir(parents=True, exist_ok=True)
    
    # =========================================================================
    # COMMIT TRANSACCIONAL
    # =========================================================================
    
    def commit_bundle(self, bundle: Phase2Bundle) -> dict[str, Any]:
        """
        Escribe todas las notas de un bundle al vault de forma transaccional.
        
        Args:
            bundle: Bundle aprobado a escribir
            
        Returns:
            Diccionario con resultados:
            {
                "success": bool,
                "transaction_id": str,
                "files_written": list[str],
                "error": str | None
            }
        """
        # 1. Iniciar transacción
        tx = self.wal.begin_transaction(bundle.bundle_id)
        temp_dir = self.wal.get_temp_path_for_transaction(tx.transaction_id)
        
        try:
            # 2. Preparar operaciones (registrar en WAL)
            operations = self._prepare_operations(bundle, temp_dir)
            for op in operations:
                self.wal.add_operation(tx.transaction_id, op)
            
            # 3. Escribir a temporales
            self.wal.mark_executing(tx.transaction_id)
            
            for i, op in enumerate(operations):
                self._write_temp_file(op)
                self.wal.mark_operation_completed(tx.transaction_id, i)
            
            # 4. Commit: renombres atómicos
            self.wal.mark_committing(tx.transaction_id)
            
            files_written = []
            for op in operations:
                if op.operation == "create":
                    # Crear directorios padre si no existen
                    Path(op.target_path).parent.mkdir(parents=True, exist_ok=True)
                    # Renombre atómico (CAMBIO: replace)
                    os.replace(op.temp_path, op.target_path)
                    files_written.append(op.target_path)
                elif op.operation == "update":
                    # Backup + renombre
                    backup_path = f"{op.target_path}.backup"
                    if Path(op.target_path).exists():
                        shutil.copy2(op.target_path, backup_path)
                    
                    # Renombre atómico (CAMBIO: replace)
                    os.replace(op.temp_path, op.target_path)
                    
                    # Limpiar backup si todo bien
                    Path(backup_path).unlink(missing_ok=True)
                    files_written.append(op.target_path)
                    
            # 5. Marcar completado
            self.wal.mark_committed(tx.transaction_id)
            
            return {
                "success": True,
                "transaction_id": tx.transaction_id,
                "files_written": files_written,
                "error": None,
            }
            
        except Exception as e:
            # Rollback
            self.wal.rollback(tx.transaction_id, str(e))
            return {
                "success": False,
                "transaction_id": tx.transaction_id,
                "files_written": [],
                "error": str(e),
            }
    
    def _prepare_operations(
        self, 
        bundle: Phase2Bundle, 
        temp_dir: Path
    ) -> list[FileOperation]:
        """Prepara la lista de operaciones a realizar."""
        operations = []
        
        # 1. Notas atómicas
        for note in bundle.atomic_proposals:
            target_path = self.notes_path / f"{note.id}.md"
            temp_path = temp_dir / f"{note.id}.md"
            
            content = self._render_atomic_note(note, bundle.linking_matrix)
            content_hash = hashlib.sha256(content.encode()).hexdigest()
            
            # Determinar si es create o update
            operation = "update" if target_path.exists() else "create"
            
            operations.append(FileOperation(
                operation=operation,
                target_path=str(target_path),
                temp_path=str(temp_path),
                content_hash=content_hash,
            ))
        
        # 2. Actualizaciones de MOCs
        for moc_update in bundle.moc_updates:
            target_path = Path(moc_update.moc_path)
            if not target_path.is_absolute():
                target_path = self.mocs_path / target_path
            
            temp_path = temp_dir / f"moc_{moc_update.moc_id}.md"
            
            operations.append(FileOperation(
                operation="update",
                target_path=str(target_path),
                temp_path=str(temp_path),
                content_hash=None,  # Se calculará al escribir
            ))
        
        return operations
    
    def _write_temp_file(self, operation: FileOperation) -> None:
        """Escribe un archivo temporal."""
        Path(operation.temp_path).parent.mkdir(parents=True, exist_ok=True)
        
        # El contenido ya debe estar preparado
        # Aquí simplemente verificamos que el temp existe
        if not Path(operation.temp_path).exists():
            raise RuntimeError(f"Temp file not prepared: {operation.temp_path}")
    
    # =========================================================================
    # RENDERIZADO DE NOTAS
    # =========================================================================
    
    def _render_atomic_note(
        self, 
        note: AtomicNote, 
        links: list[ProposedLink]
    ) -> str:
        """
        Renderiza una nota atómica a Markdown con frontmatter YAML.
        
        Formato:
            ---
            id: note_xxx
            title: Mi Nota
            created: 2024-01-15
            source: src_xxx
            tags: [tag1, tag2]
            ---
            
            # Mi Nota
            
            Contenido de la nota...
            
            ## Enlaces
            - [[otra_nota]] - tipo: defines
        """
        # Frontmatter
        frontmatter = {
            "id": note.id,
            "title": note.title,
            "created": note.created_at.strftime("%Y-%m-%d"),
            "source": note.source_id,
            **note.frontmatter,  # Campos adicionales
        }
        
        # Construir markdown
        lines = [
            "---",
            yaml.dump(frontmatter, default_flow_style=False, allow_unicode=True).strip(),
            "---",
            "",
            f"# {note.title}",
            "",
            note.content,
        ]
        
        # Añadir sección de enlaces
        note_links = [l for l in links if l.source_note_id == note.id]
        if note_links:
            lines.extend([
                "",
                "## Enlaces",
                "",
            ])
            for link in note_links:
                lines.append(
                    f"- [[{link.target_note_id}]] — *{link.link_type.value}*: {link.rationale}"
                )
        
        return "\n".join(lines)
    
    def _apply_moc_update(
        self, 
        moc_update: MOCUpdate, 
        temp_path: Path
    ) -> None:
        """Aplica una actualización a un MOC existente."""
        moc_path = Path(moc_update.moc_path)
        if not moc_path.is_absolute():
            moc_path = self.mocs_path / moc_path
        
        # Leer MOC existente o crear nuevo
        if moc_path.exists():
            with open(moc_path, "r") as f:
                content = f.read()
        else:
            content = f"# {moc_update.moc_id}\n\n"
        
        # Aplicar acción
        if moc_update.action == "add_link":
            # Añadir enlace al final
            note_id = moc_update.details.get("note_id", "")
            section = moc_update.details.get("section", "")
            
            if section and f"## {section}" not in content:
                content += f"\n## {section}\n"
            
            content += f"\n- [[{note_id}]]"
        
        elif moc_update.action == "create_section":
            section_name = moc_update.details.get("section_name", "")
            content += f"\n\n## {section_name}\n"
        
        # Escribir a temporal
        with open(temp_path, "w") as f:
            f.write(content)
    
    # =========================================================================
    # UTILIDADES
    # =========================================================================
    
    def note_exists(self, note_id: str) -> bool:
        """Verifica si una nota ya existe en el vault."""
        return (self.notes_path / f"{note_id}.md").exists()
    
    def read_note(self, note_id: str) -> str | None:
        """Lee el contenido de una nota existente."""
        path = self.notes_path / f"{note_id}.md"
        if not path.exists():
            return None
        with open(path, "r") as f:
            return f.read()
    
    def list_notes(self) -> list[str]:
        """Lista todos los IDs de notas en el vault."""
        return [p.stem for p in self.notes_path.glob("*.md")]
    
    def list_mocs(self) -> list[str]:
        """Lista todos los MOCs."""
        return [p.stem for p in self.mocs_path.glob("*.md")]
    
    def get_vault_stats(self) -> dict[str, int]:
        """Obtiene estadísticas del vault."""
        return {
            "notes_count": len(list(self.notes_path.glob("*.md"))),
            "literature_count": len(list(self.literature_path.glob("*.md"))),
            "mocs_count": len(list(self.mocs_path.glob("*.md"))),
        }


# =============================================================================
# FUNCIONES DE CONVENIENCIA
# =============================================================================

def prepare_note_content(note: AtomicNote, links: list[ProposedLink]) -> str:
    """
    Prepara el contenido de una nota para escritura.
    Útil para preview antes del commit.
    """
    writer = VaultWriter.__new__(VaultWriter)
    return writer._render_atomic_note(note, links)


def validate_note_format(content: str) -> list[str]:
    """
    Valida que el contenido de una nota sea válido.
    
    Returns:
        Lista de errores (vacía si todo está bien)
    """
    errors = []
    
    # Verificar frontmatter
    if not content.startswith("---"):
        errors.append("Falta frontmatter (debe iniciar con ---)")
    else:
        parts = content.split("---", 2)
        if len(parts) < 3:
            errors.append("Frontmatter mal formado")
        else:
            try:
                yaml.safe_load(parts[1])
            except yaml.YAMLError as e:
                errors.append(f"YAML inválido en frontmatter: {e}")
    
    # Verificar título
    if "\n# " not in content:
        errors.append("Falta título (# Título)")
    
    return errors