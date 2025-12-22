"""
bundles_fs.py — Persistencia de Bundles en FileSystem

Este módulo maneja la lectura/escritura de bundles JSON a disco.
Implementa locking para evitar condiciones de carrera.

RESPONSABILIDADES:
- Serializar/deserializar bundles Phase1 y Phase2
- Mover bundles entre carpetas de staging (pending → approved/rejected)
- Versionado y auditoría de cambios
- Locking a nivel de archivo

CONEXIONES:
- Lee/escribe en: data/staging/{phase1_pending, phase1_approved, phase2_pending, etc.}
- Usado por: watcher_phase1.py, runner_phase2.py, ui_app.py
"""

from __future__ import annotations
import json
import shutil
import sys
import os
from datetime import datetime
from pathlib import Path
from typing import Generator, Literal

# === MODIFICACIÓN WINDOWS (Locking) ===
try:
    import fcntl
except ImportError:
    if os.name == 'nt':
        class fcntl:
            LOCK_EX = 0
            LOCK_SH = 0
            LOCK_UN = 0
            @staticmethod
            def flock(fd, op): pass
    else:
        raise

from core.state_schema import ApprovalStatus, Phase1Bundle, Phase2Bundle

class BundleStore:
    def __init__(self, base_path: Path | str):
        self.base_path = Path(base_path)
        self.staging_path = self.base_path / "staging"
        self.dirs = {
            "phase1_pending": self.staging_path / "phase1_pending",
            "phase1_approved": self.staging_path / "phase1_approved",
            "phase2_pending": self.staging_path / "phase2_pending",
            "phase2_approved": self.staging_path / "phase2_approved",
            "rejected": self.staging_path / "rejected",
        }
        for dir_path in self.dirs.values():
            dir_path.mkdir(parents=True, exist_ok=True)
    
    # --- HELPER PARA LEER/ESCRIBIR UTF-8 ---
    def _write_json(self, path: Path, content: str):
        # AQUI ESTA LA MAGIA: encoding="utf-8"
        with open(path, "w", encoding="utf-8") as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            try:
                f.write(content)
            finally:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)

    def _read_json(self, path: Path) -> str:
        # AQUI TAMBIEN
        with open(path, "r", encoding="utf-8") as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_SH)
            try:
                return f.read()
            finally:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)

    # --- PHASE 1 ---
    def save_phase1_bundle(self, bundle: Phase1Bundle, status: Literal["pending", "approved", "rejected"] = "pending") -> Path:
        dir_key = f"phase1_{status}" if status != "rejected" else "rejected"
        target_path = self.dirs[dir_key] / f"{bundle.bundle_id}.json"
        self._write_json(target_path, bundle.to_json())
        return target_path
    
    def load_phase1_bundle(self, bundle_id: str) -> Phase1Bundle | None:
        filename = f"{bundle_id}.json"
        for dir_key in ["phase1_pending", "phase1_approved", "rejected"]:
            path = self.dirs[dir_key] / filename
            if path.exists():
                return Phase1Bundle.from_json(self._read_json(path))
        return None
    
    def list_phase1_pending(self) -> list[Phase1Bundle]:
        return self._list_bundles_in_dir(self.dirs["phase1_pending"], Phase1Bundle)
    
    def list_phase1_approved(self) -> list[Phase1Bundle]:
        return self._list_bundles_in_dir(self.dirs["phase1_approved"], Phase1Bundle)
    
    # --- PHASE 2 ---
    def save_phase2_bundle(self, bundle: Phase2Bundle, status: Literal["pending", "approved", "rejected"] = "pending") -> Path:
        dir_key = f"phase2_{status}" if status != "rejected" else "rejected"
        target_path = self.dirs[dir_key] / f"{bundle.bundle_id}.json"
        self._write_json(target_path, bundle.to_json())
        return target_path
    
    def load_phase2_bundle(self, bundle_id: str) -> Phase2Bundle | None:
        filename = f"{bundle_id}.json"
        for dir_key in ["phase2_pending", "phase2_approved", "rejected"]:
            path = self.dirs[dir_key] / filename
            if path.exists():
                return Phase2Bundle.from_json(self._read_json(path))
        return None
    
    def list_phase2_pending(self) -> list[Phase2Bundle]:
        return self._list_bundles_in_dir(self.dirs["phase2_pending"], Phase2Bundle)
    
    # --- TRANSICIONES ---
    def approve_phase1(self, bundle_id: str, directives: str | None = None) -> Phase1Bundle | None:
        return self._move_bundle(bundle_id, "phase1_pending", "phase1_approved", Phase1Bundle, ApprovalStatus.APPROVED, directives)
    
    def reject_phase1(self, bundle_id: str, directives: str) -> Phase1Bundle | None:
        return self._move_bundle(bundle_id, "phase1_pending", "rejected", Phase1Bundle, ApprovalStatus.REJECTED, directives)
    
    def approve_phase2(self, bundle_id: str, directives: str | None = None) -> Phase2Bundle | None:
        return self._move_bundle(bundle_id, "phase2_pending", "phase2_approved", Phase2Bundle, ApprovalStatus.APPROVED, directives)
    
    def reject_phase2(self, bundle_id: str, directives: str, return_to_phase1: bool = False) -> Phase2Bundle | None:
        final_directives = f"[RETURN_TO_PHASE1] {directives}" if return_to_phase1 else directives
        return self._move_bundle(bundle_id, "phase2_pending", "rejected", Phase2Bundle, ApprovalStatus.REJECTED, final_directives)

    # --- UTILIDADES INTERNAS ---
    def _move_bundle(self, bundle_id, src_key, dst_key, cls, status_enum, directives):
        src_path = self.dirs[src_key] / f"{bundle_id}.json"
        if not src_path.exists(): return None
        
        # Cargar, modificar, guardar, borrar original
        bundle = cls.from_json(self._read_json(src_path))
        bundle.approval_status = status_enum
        bundle.human_directives = directives
        bundle.reviewed_at = datetime.now()
        
        dst_path = self.dirs[dst_key] / f"{bundle_id}.json"
        self._write_json(dst_path, bundle.to_json())
        src_path.unlink()
        return bundle

    def _list_bundles_in_dir(self, dir_path: Path, bundle_class: type) -> list:
        bundles = []
        for json_file in dir_path.glob("*.json"):
            try:
                bundles.append(bundle_class.from_json(self._read_json(json_file)))
            except Exception as e:
                print(f"Error loading {json_file}: {e}")
        bundles.sort(key=lambda b: b.created_at, reverse=True)
        return bundles

    def get_bundle_path(self, bundle_id: str) -> Path | None:
        filename = f"{bundle_id}.json"
        for dir_path in self.dirs.values():
            path = dir_path / filename
            if path.exists(): return path
        return None
    
    def archive_bundle(self, bundle_id: str, archive_dir: Path) -> bool:
        current_path = self.get_bundle_path(bundle_id)
        if current_path is None: return False
        archive_dir.mkdir(parents=True, exist_ok=True)
        shutil.move(str(current_path), str(archive_dir / current_path.name))
        return True