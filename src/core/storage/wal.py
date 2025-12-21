"""
wal.py — Write-Ahead Log para Transacciones Atómicas

Este módulo implementa un WAL simple para garantizar escrituras
ACID-like al vault de Obsidian.

PRINCIPIO:
Antes de escribir CUALQUIER archivo al vault, se registra la intención
en el WAL. Si el proceso falla a mitad, el WAL permite:
1. Detectar transacciones incompletas
2. Hacer rollback (limpiar archivos parciales)
3. Reintentar la transacción

ESTRUCTURA DEL WAL:
data/wal/
    current.json          # Transacción en progreso (si existe)
    completed/            # Transacciones exitosas (auditoría)
    failed/               # Transacciones fallidas (para retry)

CONEXIONES:
- Usado por: vault_io.py (para commits)
- Escribe en: data/wal/
- Lee: data/vault/ (para verificar estado)
"""

from __future__ import annotations

import json
import os
import shutil
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


class TransactionStatus(str, Enum):
    """Estados posibles de una transacción."""
    PREPARING = "preparing"       # Registrando intención
    EXECUTING = "executing"       # Escribiendo archivos
    COMMITTING = "committing"     # Renombres atómicos
    COMMITTED = "committed"       # Éxito
    ROLLING_BACK = "rolling_back" # En proceso de rollback
    ROLLED_BACK = "rolled_back"   # Rollback completado
    FAILED = "failed"             # Fallo irrecuperable


class FileOperation(BaseModel):
    """Una operación de archivo pendiente."""
    operation: str              # "create", "update", "delete"
    target_path: str            # Path final en el vault
    temp_path: str | None       # Path temporal durante la transacción
    content_hash: str | None    # Hash del contenido (para verificación)
    completed: bool = False     # ¿Se completó esta operación?


class TransactionRecord(BaseModel):
    """Registro completo de una transacción."""
    transaction_id: str
    bundle_id: str                          # Bundle que originó esta transacción
    status: TransactionStatus = TransactionStatus.PREPARING
    operations: list[FileOperation] = Field(default_factory=list)
    started_at: datetime = Field(default_factory=datetime.now)
    completed_at: datetime | None = None
    error_message: str | None = None
    
    def to_json(self) -> str:
        return self.model_dump_json(indent=2)
    
    @classmethod
    def from_json(cls, json_str: str) -> TransactionRecord:
        return cls.model_validate_json(json_str)


class WriteAheadLog:
    """
    Gestor del Write-Ahead Log.
    
    Uso típico:
        wal = WriteAheadLog(base_path)
        
        # Iniciar transacción
        tx = wal.begin_transaction(bundle_id)
        
        # Registrar operaciones
        wal.add_operation(tx.transaction_id, FileOperation(...))
        
        # Ejecutar
        try:
            wal.mark_executing(tx.transaction_id)
            # ... escribir archivos ...
            wal.mark_committed(tx.transaction_id)
        except Exception as e:
            wal.rollback(tx.transaction_id, str(e))
    """
    
    def __init__(self, base_path: Path | str):
        self.base_path = Path(base_path)
        self.wal_path = self.base_path / "wal"
        
        # Crear estructura
        self.current_path = self.wal_path / "current.json"
        self.completed_path = self.wal_path / "completed"
        self.failed_path = self.wal_path / "failed"
        self.temp_path = self.wal_path / "temp"
        
        for p in [self.completed_path, self.failed_path, self.temp_path]:
            p.mkdir(parents=True, exist_ok=True)
    
    # =========================================================================
    # CICLO DE VIDA DE TRANSACCIÓN
    # =========================================================================
    
    def begin_transaction(self, bundle_id: str) -> TransactionRecord:
        """
        Inicia una nueva transacción.
        
        IMPORTANTE: Solo puede haber UNA transacción activa a la vez.
        Si hay una transacción incompleta, se debe resolver primero.
        """
        # Verificar si hay transacción pendiente
        if self.current_path.exists():
            pending = self._load_current()
            if pending.status not in [
                TransactionStatus.COMMITTED, 
                TransactionStatus.ROLLED_BACK
            ]:
                raise RuntimeError(
                    f"Hay una transacción pendiente: {pending.transaction_id}. "
                    f"Debe resolverse antes de iniciar una nueva."
                )
        
        # Crear nueva transacción
        tx_id = f"tx_{bundle_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        tx = TransactionRecord(
            transaction_id=tx_id,
            bundle_id=bundle_id,
            status=TransactionStatus.PREPARING,
        )
        
        self._save_current(tx)
        return tx
    
    def add_operation(
        self, 
        transaction_id: str, 
        operation: FileOperation
    ) -> None:
        """Registra una operación pendiente en la transacción."""
        tx = self._load_current()
        if tx.transaction_id != transaction_id:
            raise ValueError(f"Transaction ID mismatch")
        
        if tx.status != TransactionStatus.PREPARING:
            raise RuntimeError(f"Cannot add operations in status: {tx.status}")
        
        tx.operations.append(operation)
        self._save_current(tx)
    
    def mark_executing(self, transaction_id: str) -> None:
        """Marca la transacción como en ejecución."""
        tx = self._load_current()
        if tx.transaction_id != transaction_id:
            raise ValueError(f"Transaction ID mismatch")
        
        tx.status = TransactionStatus.EXECUTING
        self._save_current(tx)
    
    def mark_operation_completed(
        self, 
        transaction_id: str, 
        operation_index: int
    ) -> None:
        """Marca una operación individual como completada."""
        tx = self._load_current()
        if tx.transaction_id != transaction_id:
            raise ValueError(f"Transaction ID mismatch")
        
        tx.operations[operation_index].completed = True
        self._save_current(tx)
    
    def mark_committing(self, transaction_id: str) -> None:
        """Marca la transacción como en fase de commit (renombres atómicos)."""
        tx = self._load_current()
        if tx.transaction_id != transaction_id:
            raise ValueError(f"Transaction ID mismatch")
        
        tx.status = TransactionStatus.COMMITTING
        self._save_current(tx)
    
    def mark_committed(self, transaction_id: str) -> TransactionRecord:
        """
        Marca la transacción como completada exitosamente.
        Mueve el registro a completed/ para auditoría.
        """
        tx = self._load_current()
        if tx.transaction_id != transaction_id:
            raise ValueError(f"Transaction ID mismatch")
        
        tx.status = TransactionStatus.COMMITTED
        tx.completed_at = datetime.now()
        
        # Mover a completed
        completed_file = self.completed_path / f"{transaction_id}.json"
        with open(completed_file, "w") as f:
            f.write(tx.to_json())
        
        # Limpiar current y temp
        self.current_path.unlink(missing_ok=True)
        self._cleanup_temp(tx)
        
        return tx
    
    def rollback(
        self, 
        transaction_id: str, 
        error_message: str
    ) -> TransactionRecord:
        """
        Hace rollback de una transacción fallida.
        
        1. Elimina archivos parcialmente escritos
        2. Limpia archivos temporales
        3. Registra el fallo
        """
        tx = self._load_current()
        if tx.transaction_id != transaction_id:
            raise ValueError(f"Transaction ID mismatch")
        
        tx.status = TransactionStatus.ROLLING_BACK
        tx.error_message = error_message
        self._save_current(tx)
        
        # Eliminar archivos creados
        for op in tx.operations:
            if op.completed and op.operation == "create":
                try:
                    Path(op.target_path).unlink(missing_ok=True)
                except Exception:
                    pass  # Best effort
        
        # Limpiar temporales
        self._cleanup_temp(tx)
        
        # Marcar como rolled back
        tx.status = TransactionStatus.ROLLED_BACK
        tx.completed_at = datetime.now()
        
        # Mover a failed
        failed_file = self.failed_path / f"{transaction_id}.json"
        with open(failed_file, "w") as f:
            f.write(tx.to_json())
        
        self.current_path.unlink(missing_ok=True)
        
        return tx
    
    # =========================================================================
    # RECOVERY
    # =========================================================================
    
    def check_pending_transaction(self) -> TransactionRecord | None:
        """
        Verifica si hay una transacción pendiente.
        Llamar al inicio de la aplicación para recovery.
        """
        if not self.current_path.exists():
            return None
        
        tx = self._load_current()
        if tx.status in [
            TransactionStatus.COMMITTED, 
            TransactionStatus.ROLLED_BACK
        ]:
            # Limpieza tardía
            self.current_path.unlink(missing_ok=True)
            return None
        
        return tx
    
    def recover_or_rollback(self) -> str:
        """
        Intenta recuperar o hace rollback de una transacción pendiente.
        
        Returns:
            Mensaje describiendo la acción tomada
        """
        tx = self.check_pending_transaction()
        if tx is None:
            return "No pending transactions"
        
        if tx.status == TransactionStatus.PREPARING:
            # No se llegó a ejecutar nada, solo limpiar
            self.rollback(tx.transaction_id, "Recovered: transaction never started")
            return f"Cleaned up unstarted transaction: {tx.transaction_id}"
        
        if tx.status == TransactionStatus.EXECUTING:
            # Parcialmente ejecutado, hacer rollback
            self.rollback(tx.transaction_id, "Recovered: partial execution")
            return f"Rolled back partial transaction: {tx.transaction_id}"
        
        if tx.status == TransactionStatus.COMMITTING:
            # Estaba en medio del commit, intentar completar
            # En un sistema real, aquí verificaríamos qué operaciones
            # se completaron y terminaríamos las pendientes
            self.rollback(tx.transaction_id, "Recovered: interrupted during commit")
            return f"Rolled back interrupted commit: {tx.transaction_id}"
        
        return f"Unknown state for transaction: {tx.transaction_id}"
    
    # =========================================================================
    # UTILIDADES INTERNAS
    # =========================================================================
    
    def _load_current(self) -> TransactionRecord:
        """Carga la transacción actual."""
        with open(self.current_path, "r") as f:
            return TransactionRecord.from_json(f.read())
    
    def _save_current(self, tx: TransactionRecord) -> None:
        """Guarda la transacción actual (atómicamente)."""
        temp_file = self.current_path.with_suffix(".tmp")
        with open(temp_file, "w") as f:
            f.write(tx.to_json())
        os.rename(temp_file, self.current_path)
    
    def _cleanup_temp(self, tx: TransactionRecord) -> None:
        """Limpia archivos temporales de una transacción."""
        for op in tx.operations:
            if op.temp_path:
                try:
                    Path(op.temp_path).unlink(missing_ok=True)
                except Exception:
                    pass
        
        # Limpiar directorio de la transacción
        tx_temp_dir = self.temp_path / tx.transaction_id
        if tx_temp_dir.exists():
            shutil.rmtree(tx_temp_dir, ignore_errors=True)
    
    def get_temp_path_for_transaction(self, transaction_id: str) -> Path:
        """Obtiene el directorio temporal para una transacción."""
        path = self.temp_path / transaction_id
        path.mkdir(parents=True, exist_ok=True)
        return path
    
    # =========================================================================
    # AUDITORÍA
    # =========================================================================
    
    def list_completed(self, limit: int = 100) -> list[TransactionRecord]:
        """Lista las últimas transacciones completadas."""
        records = []
        for f in sorted(self.completed_path.glob("*.json"), reverse=True)[:limit]:
            try:
                with open(f, "r") as file:
                    records.append(TransactionRecord.from_json(file.read()))
            except Exception:
                pass
        return records
    
    def list_failed(self) -> list[TransactionRecord]:
        """Lista las transacciones fallidas (para retry manual)."""
        records = []
        for f in self.failed_path.glob("*.json"):
            try:
                with open(f, "r") as file:
                    records.append(TransactionRecord.from_json(file.read()))
            except Exception:
                pass
        return records