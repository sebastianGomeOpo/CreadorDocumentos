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
import sys  # <--- Agregado
import os   # <--- Agregado
from datetime import datetime
from pathlib import Path
from typing import Generator, Literal

# === INICIO MODIFICACIÓN WINDOWS ===
# Reemplaza la linea 'import fcntl' con esto:
try:
    import fcntl
except ImportError:
    if os.name == 'nt':  # Si es Windows
        class fcntl:
            LOCK_EX = 0
            LOCK_SH = 0
            LOCK_UN = 0
            
            @staticmethod
            def flock(fd, op):
                pass # No-op en Windows para desarrollo local
    else:
        raise
# === FIN MODIFICACIÓN WINDOWS ===

from core.state_schema import (
    ApprovalStatus,
    Phase1Bundle,
    Phase2Bundle,
)


class BundleStore:
    """
    Gestiona bundles en el filesystem.
    
    Estructura esperada:
        data/staging/
            phase1_pending/
            phase1_approved/
            phase2_pending/
            phase2_approved/
            rejected/
    """
    
    def __init__(self, base_path: Path | str):
        self.base_path = Path(base_path)
        self.staging_path = self.base_path / "staging"
        
        # Directorios de staging
        self.dirs = {
            "phase1_pending": self.staging_path / "phase1_pending",
            "phase1_approved": self.staging_path / "phase1_approved",
            "phase2_pending": self.staging_path / "phase2_pending",
            "phase2_approved": self.staging_path / "phase2_approved",
            "rejected": self.staging_path / "rejected",
        }
        
        # Crear directorios si no existen
        for dir_path in self.dirs.values():
            dir_path.mkdir(parents=True, exist_ok=True)
    
    # =========================================================================
    # PHASE 1 BUNDLES
    # =========================================================================
    
    def save_phase1_bundle(
        self, 
        bundle: Phase1Bundle, 
        status: Literal["pending", "approved", "rejected"] = "pending"
    ) -> Path:
        """
        Guarda un bundle de Phase 1.
        
        Args:
            bundle: El bundle a guardar
            status: Estado inicial (determina la carpeta)
            
        Returns:
            Path al archivo creado
        """
        dir_key = f"phase1_{status}" if status != "rejected" else "rejected"
        target_dir = self.dirs[dir_key]
        
        filename = f"{bundle.bundle_id}.json"
        target_path = target_dir / filename
        
        # Escribir con lock exclusivo
        with open(target_path, "w") as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            try:
                f.write(bundle.to_json())
            finally:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        
        return target_path
    
    def load_phase1_bundle(self, bundle_id: str) -> Phase1Bundle | None:
        """
        Carga un bundle de Phase 1 buscando en todas las carpetas.
        
        Returns:
            El bundle si existe, None si no se encuentra
        """
        filename = f"{bundle_id}.json"
        
        # Buscar en todas las carpetas de phase1
        for dir_key in ["phase1_pending", "phase1_approved", "rejected"]:
            path = self.dirs[dir_key] / filename
            if path.exists():
                with open(path, "r") as f:
                    fcntl.flock(f.fileno(), fcntl.LOCK_SH)
                    try:
                        content = f.read()
                    finally:
                        fcntl.flock(f.fileno(), fcntl.LOCK_UN)
                return Phase1Bundle.from_json(content)
        
        return None
    
    def list_phase1_pending(self) -> list[Phase1Bundle]:
        """Lista todos los bundles Phase 1 pendientes de revisión."""
        return self._list_bundles_in_dir(self.dirs["phase1_pending"], Phase1Bundle)
    
    def list_phase1_approved(self) -> list[Phase1Bundle]:
        """Lista todos los bundles Phase 1 aprobados."""
        return self._list_bundles_in_dir(self.dirs["phase1_approved"], Phase1Bundle)
    
    # =========================================================================
    # PHASE 2 BUNDLES
    # =========================================================================
    
    def save_phase2_bundle(
        self, 
        bundle: Phase2Bundle, 
        status: Literal["pending", "approved", "rejected"] = "pending"
    ) -> Path:
        """Guarda un bundle de Phase 2."""
        dir_key = f"phase2_{status}" if status != "rejected" else "rejected"
        target_dir = self.dirs[dir_key]
        
        filename = f"{bundle.bundle_id}.json"
        target_path = target_dir / filename
        
        with open(target_path, "w") as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            try:
                f.write(bundle.to_json())
            finally:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        
        return target_path
    
    def load_phase2_bundle(self, bundle_id: str) -> Phase2Bundle | None:
        """Carga un bundle de Phase 2 buscando en todas las carpetas."""
        filename = f"{bundle_id}.json"
        
        for dir_key in ["phase2_pending", "phase2_approved", "rejected"]:
            path = self.dirs[dir_key] / filename
            if path.exists():
                with open(path, "r") as f:
                    fcntl.flock(f.fileno(), fcntl.LOCK_SH)
                    try:
                        content = f.read()
                    finally:
                        fcntl.flock(f.fileno(), fcntl.LOCK_UN)
                return Phase2Bundle.from_json(content)
        
        return None
    
    def list_phase2_pending(self) -> list[Phase2Bundle]:
        """Lista todos los bundles Phase 2 pendientes de revisión."""
        return self._list_bundles_in_dir(self.dirs["phase2_pending"], Phase2Bundle)
    
    # =========================================================================
    # TRANSICIONES DE ESTADO
    # =========================================================================
    
    def approve_phase1(
        self, 
        bundle_id: str, 
        directives: str | None = None
    ) -> Phase1Bundle | None:
        """
        Aprueba un bundle Phase 1: lo mueve de pending a approved.
        
        Args:
            bundle_id: ID del bundle
            directives: Directivas humanas opcionales
            
        Returns:
            El bundle actualizado, o None si no existe
        """
        source_path = self.dirs["phase1_pending"] / f"{bundle_id}.json"
        if not source_path.exists():
            return None
        
        # Cargar, actualizar estado, guardar en nueva ubicación
        bundle = self.load_phase1_bundle(bundle_id)
        if bundle is None:
            return None
        
        bundle.approval_status = ApprovalStatus.APPROVED
        bundle.human_directives = directives
        bundle.reviewed_at = datetime.now()
        
        # Mover a approved
        target_path = self.dirs["phase1_approved"] / f"{bundle_id}.json"
        with open(target_path, "w") as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            try:
                f.write(bundle.to_json())
            finally:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        
        # Eliminar de pending
        source_path.unlink()
        
        return bundle
    
    def reject_phase1(
        self, 
        bundle_id: str, 
        directives: str
    ) -> Phase1Bundle | None:
        """
        Rechaza un bundle Phase 1: lo mueve de pending a rejected.
        Las directivas son OBLIGATORIAS para rechazos.
        """
        source_path = self.dirs["phase1_pending"] / f"{bundle_id}.json"
        if not source_path.exists():
            return None
        
        bundle = self.load_phase1_bundle(bundle_id)
        if bundle is None:
            return None
        
        bundle.approval_status = ApprovalStatus.REJECTED
        bundle.human_directives = directives  # Obligatorio para rechazos
        bundle.reviewed_at = datetime.now()
        
        # Mover a rejected
        target_path = self.dirs["rejected"] / f"{bundle_id}.json"
        with open(target_path, "w") as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            try:
                f.write(bundle.to_json())
            finally:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        
        source_path.unlink()
        
        return bundle
    
    def approve_phase2(
        self, 
        bundle_id: str, 
        directives: str | None = None
    ) -> Phase2Bundle | None:
        """Aprueba un bundle Phase 2: listo para commit al vault."""
        source_path = self.dirs["phase2_pending"] / f"{bundle_id}.json"
        if not source_path.exists():
            return None
        
        bundle = self.load_phase2_bundle(bundle_id)
        if bundle is None:
            return None
        
        bundle.approval_status = ApprovalStatus.APPROVED
        bundle.human_directives = directives
        bundle.reviewed_at = datetime.now()
        
        target_path = self.dirs["phase2_approved"] / f"{bundle_id}.json"
        with open(target_path, "w") as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            try:
                f.write(bundle.to_json())
            finally:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        
        source_path.unlink()
        
        return bundle
    
    def reject_phase2(
        self, 
        bundle_id: str, 
        directives: str,
        return_to_phase1: bool = False
    ) -> Phase2Bundle | None:
        """
        Rechaza un bundle Phase 2.
        
        Args:
            bundle_id: ID del bundle
            directives: Directivas de corrección (obligatorias)
            return_to_phase1: Si True, indica que el problema es estructural
                              y debe volver a Phase 1
        """
        source_path = self.dirs["phase2_pending"] / f"{bundle_id}.json"
        if not source_path.exists():
            return None
        
        bundle = self.load_phase2_bundle(bundle_id)
        if bundle is None:
            return None
        
        bundle.approval_status = ApprovalStatus.REJECTED
        bundle.human_directives = directives
        bundle.reviewed_at = datetime.now()
        
        # Añadir metadata sobre si debe volver a phase1
        if return_to_phase1:
            bundle.human_directives = f"[RETURN_TO_PHASE1] {directives}"
        
        target_path = self.dirs["rejected"] / f"{bundle_id}.json"
        with open(target_path, "w") as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            try:
                f.write(bundle.to_json())
            finally:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        
        source_path.unlink()
        
        return bundle
    
    # =========================================================================
    # UTILIDADES
    # =========================================================================
    
    def _list_bundles_in_dir(
        self, 
        dir_path: Path, 
        bundle_class: type
    ) -> list:
        """Lista todos los bundles en un directorio."""
        bundles = []
        for json_file in dir_path.glob("*.json"):
            try:
                with open(json_file, "r") as f:
                    content = f.read()
                bundle = bundle_class.from_json(content)
                bundles.append(bundle)
            except Exception as e:
                # Log error pero continuar con otros bundles
                print(f"Error loading {json_file}: {e}")
        
        # Ordenar por fecha de creación (más reciente primero)
        bundles.sort(key=lambda b: b.created_at, reverse=True)
        return bundles
    
    def get_bundle_path(self, bundle_id: str) -> Path | None:
        """Encuentra el path de un bundle por su ID."""
        filename = f"{bundle_id}.json"
        for dir_path in self.dirs.values():
            path = dir_path / filename
            if path.exists():
                return path
        return None
    
    def archive_bundle(self, bundle_id: str, archive_dir: Path) -> bool:
        """Mueve un bundle a un directorio de archivo."""
        current_path = self.get_bundle_path(bundle_id)
        if current_path is None:
            return False
        
        archive_dir.mkdir(parents=True, exist_ok=True)
        target_path = archive_dir / current_path.name
        shutil.move(str(current_path), str(target_path))
        return True