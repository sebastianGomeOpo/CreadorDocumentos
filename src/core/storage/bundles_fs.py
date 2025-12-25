"""
bundles_fs.py — Persistencia de Bundles en FileSystem V2

Este módulo maneja la lectura/escritura de bundles JSON a disco.
Actualizado para soportar la arquitectura paralela V2.

RESPONSABILIDADES:
- Serializar/deserializar bundles Phase1 y Phase2
- Mover bundles entre carpetas de staging
- Gestionar chunks temporales
- Versionado y auditoría de cambios

CONEXIONES:
- Lee/escribe en: data/staging/{phase1_pending, phase1_approved, etc.}
- Gestiona: data/temp/chunks/
- Usado por: watcher_phase1.py, ui_app.py
"""

from __future__ import annotations

import json
import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import Literal

from core.state_schema import ApprovalStatus, Phase1Bundle, Phase2Bundle


# =============================================================================
# LOCKING (Cross-platform)
# =============================================================================

try:
    import fcntl
    HAS_FCNTL = True
except ImportError:
    HAS_FCNTL = False


def lock_file(f, exclusive: bool = True):
    """Aplica lock al archivo si está disponible."""
    if HAS_FCNTL:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)


def unlock_file(f):
    """Libera lock del archivo si está disponible."""
    if HAS_FCNTL:
        fcntl.flock(f.fileno(), fcntl.LOCK_UN)


# =============================================================================
# CLASE PRINCIPAL
# =============================================================================

class BundleStore:
    """
    Gestor de persistencia para bundles.
    """
    
    def __init__(self, base_path: Path | str):
        self.base_path = Path(base_path)
        self.staging_path = self.base_path / "staging"
        self.temp_path = self.base_path / "temp"
        
        self.dirs = {
            "phase1_pending": self.staging_path / "phase1_pending",
            "phase1_approved": self.staging_path / "phase1_approved",
            "phase2_pending": self.staging_path / "phase2_pending",
            "phase2_approved": self.staging_path / "phase2_approved",
            "rejected": self.staging_path / "rejected",
            "chunks": self.temp_path / "chunks",
        }
        
        # Crear directorios
        for dir_path in self.dirs.values():
            dir_path.mkdir(parents=True, exist_ok=True)
    
    # =========================================================================
    # I/O HELPERS
    # =========================================================================
    
    def _write_json(self, path: Path, content: str) -> None:
        """Escribe JSON con encoding UTF-8 y locking."""
        with open(path, "w", encoding="utf-8") as f:
            lock_file(f, exclusive=True)
            try:
                f.write(content)
            finally:
                unlock_file(f)
    
    def _read_json(self, path: Path) -> str:
        """Lee JSON con encoding UTF-8 y locking."""
        with open(path, "r", encoding="utf-8") as f:
            lock_file(f, exclusive=False)
            try:
                return f.read()
            finally:
                unlock_file(f)
    
    # =========================================================================
    # PHASE 1
    # =========================================================================
    
    def save_phase1_bundle(
        self,
        bundle: Phase1Bundle,
        status: Literal["pending", "approved", "rejected"] = "pending",
    ) -> Path:
        """Guarda un bundle de Phase 1."""
        dir_key = f"phase1_{status}" if status != "rejected" else "rejected"
        target_path = self.dirs[dir_key] / f"{bundle.bundle_id}.json"
        self._write_json(target_path, bundle.to_json())
        return target_path
    
    def load_phase1_bundle(self, bundle_id: str) -> Phase1Bundle | None:
        """Carga un bundle de Phase 1 por ID."""
        filename = f"{bundle_id}.json"
        
        for dir_key in ["phase1_pending", "phase1_approved", "rejected"]:
            path = self.dirs[dir_key] / filename
            if path.exists():
                return Phase1Bundle.from_json(self._read_json(path))
        
        return None
    
    def list_phase1_pending(self) -> list[Phase1Bundle]:
        """Lista bundles de Phase 1 pendientes."""
        return self._list_bundles_in_dir(
            self.dirs["phase1_pending"],
            Phase1Bundle
        )
    
    def list_phase1_approved(self) -> list[Phase1Bundle]:
        """Lista bundles de Phase 1 aprobados."""
        return self._list_bundles_in_dir(
            self.dirs["phase1_approved"],
            Phase1Bundle
        )
    
    def approve_phase1(
        self,
        bundle_id: str,
        directives: str | None = None,
    ) -> Phase1Bundle | None:
        """Aprueba un bundle de Phase 1."""
        return self._move_bundle(
            bundle_id,
            "phase1_pending",
            "phase1_approved",
            Phase1Bundle,
            ApprovalStatus.APPROVED,
            directives,
        )
    
    def reject_phase1(
        self,
        bundle_id: str,
        directives: str,
    ) -> Phase1Bundle | None:
        """Rechaza un bundle de Phase 1."""
        return self._move_bundle(
            bundle_id,
            "phase1_pending",
            "rejected",
            Phase1Bundle,
            ApprovalStatus.REJECTED,
            directives,
        )
    
    # =========================================================================
    # PHASE 2
    # =========================================================================
    
    def save_phase2_bundle(
        self,
        bundle: Phase2Bundle,
        status: Literal["pending", "approved", "rejected"] = "pending",
    ) -> Path:
        """Guarda un bundle de Phase 2."""
        dir_key = f"phase2_{status}" if status != "rejected" else "rejected"
        target_path = self.dirs[dir_key] / f"{bundle.bundle_id}.json"
        self._write_json(target_path, bundle.to_json())
        return target_path
    
    def load_phase2_bundle(self, bundle_id: str) -> Phase2Bundle | None:
        """Carga un bundle de Phase 2 por ID."""
        filename = f"{bundle_id}.json"
        
        for dir_key in ["phase2_pending", "phase2_approved", "rejected"]:
            path = self.dirs[dir_key] / filename
            if path.exists():
                return Phase2Bundle.from_json(self._read_json(path))
        
        return None
    
    def list_phase2_pending(self) -> list[Phase2Bundle]:
        """Lista bundles de Phase 2 pendientes."""
        return self._list_bundles_in_dir(
            self.dirs["phase2_pending"],
            Phase2Bundle
        )
    
    def approve_phase2(
        self,
        bundle_id: str,
        directives: str | None = None,
    ) -> Phase2Bundle | None:
        """Aprueba un bundle de Phase 2."""
        return self._move_bundle(
            bundle_id,
            "phase2_pending",
            "phase2_approved",
            Phase2Bundle,
            ApprovalStatus.APPROVED,
            directives,
        )
    
    def reject_phase2(
        self,
        bundle_id: str,
        directives: str,
        return_to_phase1: bool = False,
    ) -> Phase2Bundle | None:
        """Rechaza un bundle de Phase 2."""
        final_directives = (
            f"[RETURN_TO_PHASE1] {directives}"
            if return_to_phase1
            else directives
        )
        return self._move_bundle(
            bundle_id,
            "phase2_pending",
            "rejected",
            Phase2Bundle,
            ApprovalStatus.REJECTED,
            final_directives,
        )
    
    # =========================================================================
    # CHUNKS TEMPORALES
    # =========================================================================
    
    def cleanup_chunks(self, source_id: str | None = None) -> int:
        """
        Limpia chunks temporales.
        
        Args:
            source_id: Si se proporciona, solo limpia chunks de ese source.
                      Si es None, limpia todos.
        
        Returns:
            Número de archivos eliminados
        """
        count = 0
        chunks_dir = self.dirs["chunks"]
        
        if source_id:
            # Solo chunks de este source
            for f in chunks_dir.glob(f"*{source_id}*"):
                if f.is_file():
                    f.unlink()
                    count += 1
        else:
            # Todos los chunks
            for f in chunks_dir.glob("*"):
                if f.is_file():
                    f.unlink()
                    count += 1
        
        return count
    
    def list_chunks(self) -> list[Path]:
        """Lista todos los chunks temporales."""
        return list(self.dirs["chunks"].glob("chunk_*.txt"))
    
    # =========================================================================
    # UTILIDADES
    # =========================================================================
    
    def _move_bundle(
        self,
        bundle_id: str,
        src_key: str,
        dst_key: str,
        cls: type,
        status_enum: ApprovalStatus,
        directives: str | None,
    ):
        """Mueve un bundle entre directorios."""
        src_path = self.dirs[src_key] / f"{bundle_id}.json"
        if not src_path.exists():
            return None
        
        # Cargar, modificar, guardar
        bundle = cls.from_json(self._read_json(src_path))
        bundle.approval_status = status_enum
        bundle.human_directives = directives
        bundle.reviewed_at = datetime.now()
        
        dst_path = self.dirs[dst_key] / f"{bundle_id}.json"
        self._write_json(dst_path, bundle.to_json())
        
        # Eliminar original
        src_path.unlink()
        
        return bundle
    
    def _list_bundles_in_dir(
        self,
        dir_path: Path,
        bundle_class: type,
    ) -> list:
        """Lista bundles en un directorio."""
        bundles = []
        
        for json_file in dir_path.glob("*.json"):
            try:
                bundles.append(
                    bundle_class.from_json(self._read_json(json_file))
                )
            except Exception as e:
                print(f"Error loading {json_file}: {e}")
        
        # Ordenar por fecha de creación (más reciente primero)
        bundles.sort(key=lambda b: b.created_at, reverse=True)
        
        return bundles
    
    def get_bundle_path(self, bundle_id: str) -> Path | None:
        """Obtiene la ruta de un bundle por ID."""
        filename = f"{bundle_id}.json"
        
        for dir_path in self.dirs.values():
            path = dir_path / filename
            if path.exists():
                return path
        
        return None
    
    def archive_bundle(
        self,
        bundle_id: str,
        archive_dir: Path,
    ) -> bool:
        """Archiva un bundle a un directorio externo."""
        current_path = self.get_bundle_path(bundle_id)
        if current_path is None:
            return False
        
        archive_dir.mkdir(parents=True, exist_ok=True)
        shutil.move(str(current_path), str(archive_dir / current_path.name))
        
        return True
    
    def get_stats(self) -> dict[str, int]:
        """Obtiene estadísticas del store."""
        return {
            "phase1_pending": len(list(self.dirs["phase1_pending"].glob("*.json"))),
            "phase1_approved": len(list(self.dirs["phase1_approved"].glob("*.json"))),
            "phase2_pending": len(list(self.dirs["phase2_pending"].glob("*.json"))),
            "phase2_approved": len(list(self.dirs["phase2_approved"].glob("*.json"))),
            "rejected": len(list(self.dirs["rejected"].glob("*.json"))),
            "temp_chunks": len(list(self.dirs["chunks"].glob("*.txt"))),
        }