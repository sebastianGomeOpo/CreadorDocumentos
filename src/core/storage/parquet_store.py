"""
parquet_store.py — Almacenamiento de Datasets en Parquet

Este módulo gestiona el almacenamiento de datos tabulares grandes
usando formato Parquet (chunks, métricas, historiales).

RESPONSABILIDAD:
- Almacenar chunks de lecciones
- Almacenar métricas de validación
- Almacenar historiales de procesamiento
- Dataset de evaluación

POR QUÉ PARQUET:
- Compresión eficiente
- Lectura columnar (rápido para análisis)
- Esquema tipado
- Compatible con Pandas/Polars/DuckDB

CONEXIONES:
- Usado por: phase1_graph.py (guardar chunks)
- Usado por: phase2_graph.py (métricas)
- Lee/Escribe: data/lessons/chunks/, data/work/
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

# Pandas para DataFrames
try:
    import pandas as pd
    PANDAS_AVAILABLE = True
except ImportError:
    PANDAS_AVAILABLE = False
    pd = None

# PyArrow para Parquet nativo
try:
    import pyarrow as pa
    import pyarrow.parquet as pq
    PYARROW_AVAILABLE = True
except ImportError:
    PYARROW_AVAILABLE = False
    pa = None
    pq = None


# =============================================================================
# FALLBACK JSON (si no hay Parquet disponible)
# =============================================================================

class JsonDataStore:
    """
    Almacenamiento de datos en JSON (fallback).
    
    Usado cuando Parquet no está disponible.
    """
    
    def __init__(self, store_path: Path | str, name: str):
        self.store_path = Path(store_path)
        self.store_path.mkdir(parents=True, exist_ok=True)
        self.name = name
        self._file = self.store_path / f"{name}.json"
        self._data: list[dict] = []
        self._load()
    
    def _load(self) -> None:
        """Carga datos existentes."""
        if self._file.exists():
            try:
                with open(self._file, "r") as f:
                    self._data = json.load(f)
            except:
                self._data = []
    
    def _save(self) -> None:
        """Guarda datos a disco."""
        with open(self._file, "w") as f:
            json.dump(self._data, f, indent=2, ensure_ascii=False, default=str)
    
    def append(self, records: list[dict]) -> int:
        """Añade registros."""
        self._data.extend(records)
        self._save()
        return len(records)
    
    def read_all(self) -> list[dict]:
        """Lee todos los registros."""
        return self._data.copy()
    
    def query(self, **filters) -> list[dict]:
        """Filtra registros por campos."""
        results = []
        for record in self._data:
            match = all(
                record.get(k) == v
                for k, v in filters.items()
            )
            if match:
                results.append(record)
        return results
    
    def count(self) -> int:
        """Número de registros."""
        return len(self._data)
    
    def clear(self) -> None:
        """Elimina todos los registros."""
        self._data = []
        self._save()


# =============================================================================
# PARQUET STORE
# =============================================================================

class ParquetStore:
    """
    Almacenamiento de datos en formato Parquet.
    
    Soporta append incremental y queries básicas.
    """
    
    def __init__(
        self,
        store_path: Path | str,
        name: str,
        schema: dict[str, str] | None = None,
    ):
        """
        Inicializa el store.
        
        Args:
            store_path: Directorio de almacenamiento
            name: Nombre del dataset
            schema: Esquema opcional {column: type}
        """
        self.store_path = Path(store_path)
        self.store_path.mkdir(parents=True, exist_ok=True)
        self.name = name
        self.schema = schema
        
        self._file = self.store_path / f"{name}.parquet"
        
        # Verificar disponibilidad
        if not PANDAS_AVAILABLE:
            raise ImportError("Pandas requerido. Instalar con: pip install pandas")
        
        if not PYARROW_AVAILABLE:
            raise ImportError("PyArrow requerido. Instalar con: pip install pyarrow")
    
    def append(self, records: list[dict]) -> int:
        """
        Añade registros al dataset.
        
        Args:
            records: Lista de diccionarios con los datos
            
        Returns:
            Número de registros añadidos
        """
        if not records:
            return 0
        
        # Crear DataFrame
        new_df = pd.DataFrame(records)
        
        # Añadir timestamp si no existe
        if "_indexed_at" not in new_df.columns:
            new_df["_indexed_at"] = datetime.now().isoformat()
        
        if self._file.exists():
            # Leer existente y concatenar
            existing_df = pd.read_parquet(self._file)
            combined_df = pd.concat([existing_df, new_df], ignore_index=True)
        else:
            combined_df = new_df
        
        # Guardar
        combined_df.to_parquet(self._file, index=False)
        
        return len(records)
    
    def read_all(self) -> list[dict]:
        """
        Lee todos los registros.
        
        Returns:
            Lista de diccionarios
        """
        if not self._file.exists():
            return []
        
        df = pd.read_parquet(self._file)
        return df.to_dict(orient="records")
    
    def read_dataframe(self) -> "pd.DataFrame":
        """
        Lee como DataFrame de Pandas.
        
        Returns:
            DataFrame con los datos
        """
        if not self._file.exists():
            return pd.DataFrame()
        
        return pd.read_parquet(self._file)
    
    def query(self, **filters) -> list[dict]:
        """
        Filtra registros por campos.
        
        Args:
            **filters: Filtros como column=value
            
        Returns:
            Registros que coinciden
        """
        if not self._file.exists():
            return []
        
        df = pd.read_parquet(self._file)
        
        for column, value in filters.items():
            if column in df.columns:
                df = df[df[column] == value]
        
        return df.to_dict(orient="records")
    
    def query_columns(self, columns: list[str]) -> list[dict]:
        """
        Lee solo columnas específicas (eficiente con Parquet).
        
        Args:
            columns: Lista de columnas a leer
            
        Returns:
            Registros con solo esas columnas
        """
        if not self._file.exists():
            return []
        
        df = pd.read_parquet(self._file, columns=columns)
        return df.to_dict(orient="records")
    
    def count(self) -> int:
        """Número de registros."""
        if not self._file.exists():
            return 0
        
        # Lectura eficiente solo de metadata
        parquet_file = pq.ParquetFile(self._file)
        return parquet_file.metadata.num_rows
    
    def clear(self) -> None:
        """Elimina todos los registros."""
        if self._file.exists():
            self._file.unlink()
    
    def get_schema(self) -> dict[str, str]:
        """
        Obtiene el esquema del dataset.
        
        Returns:
            Diccionario {column: type}
        """
        if not self._file.exists():
            return self.schema or {}
        
        parquet_file = pq.ParquetFile(self._file)
        schema = parquet_file.schema_arrow
        
        return {
            field.name: str(field.type)
            for field in schema
        }
    
    def get_stats(self) -> dict[str, Any]:
        """
        Obtiene estadísticas del dataset.
        
        Returns:
            Diccionario con estadísticas
        """
        if not self._file.exists():
            return {
                "exists": False,
                "count": 0,
            }
        
        parquet_file = pq.ParquetFile(self._file)
        metadata = parquet_file.metadata
        
        return {
            "exists": True,
            "count": metadata.num_rows,
            "columns": metadata.num_columns,
            "row_groups": metadata.num_row_groups,
            "file_size_bytes": self._file.stat().st_size,
            "schema": self.get_schema(),
        }


# =============================================================================
# STORES ESPECÍFICOS
# =============================================================================

class ChunkStore:
    """
    Store especializado para chunks de lecciones.
    """
    
    def __init__(self, data_path: Path | str):
        self.data_path = Path(data_path)
        store_path = self.data_path / "lessons" / "chunks"
        
        # Usar Parquet si disponible, sino JSON
        if PANDAS_AVAILABLE and PYARROW_AVAILABLE:
            self._store = ParquetStore(store_path, "all_chunks")
        else:
            self._store = JsonDataStore(store_path, "all_chunks")
    
    def save_lesson_chunks(
        self,
        lesson_id: str,
        chunks: list[dict[str, Any]],
    ) -> int:
        """
        Guarda chunks de una lección.
        
        Args:
            lesson_id: ID de la lección
            chunks: Lista de chunks
            
        Returns:
            Número de chunks guardados
        """
        # Añadir lesson_id a cada chunk
        records = []
        for chunk in chunks:
            record = {
                "lesson_id": lesson_id,
                "chunk_id": chunk.get("id", ""),
                "topic_id": chunk.get("topic_id", ""),
                "content": chunk.get("content", ""),
                "start_position": chunk.get("start_position", 0),
                "end_position": chunk.get("end_position", 0),
                "word_count": chunk.get("word_count", 0),
                "anchor_text": chunk.get("anchor_text", ""),
            }
            records.append(record)
        
        return self._store.append(records)
    
    def get_lesson_chunks(self, lesson_id: str) -> list[dict]:
        """Obtiene chunks de una lección."""
        return self._store.query(lesson_id=lesson_id)
    
    def get_all_chunks(self) -> list[dict]:
        """Obtiene todos los chunks."""
        return self._store.read_all()
    
    def count(self) -> int:
        """Número total de chunks."""
        return self._store.count()


class MetricsStore:
    """
    Store especializado para métricas de validación.
    """
    
    def __init__(self, data_path: Path | str):
        self.data_path = Path(data_path)
        store_path = self.data_path / "work" / "metrics"
        
        if PANDAS_AVAILABLE and PYARROW_AVAILABLE:
            self._store = ParquetStore(store_path, "validation_metrics")
        else:
            self._store = JsonDataStore(store_path, "validation_metrics")
    
    def save_validation_metrics(
        self,
        bundle_id: str,
        validation_report: dict[str, Any],
    ) -> None:
        """
        Guarda métricas de validación de un bundle.
        
        Args:
            bundle_id: ID del bundle
            validation_report: Reporte de validación
        """
        record = {
            "bundle_id": bundle_id,
            "atomicity_score": validation_report.get("atomicity_score", 0),
            "evidence_score": validation_report.get("evidence_score", 0),
            "format_score": validation_report.get("format_score", 0),
            "coherence_score": validation_report.get("coherence_score", 0),
            "total_score": validation_report.get("total_score", 0),
            "is_passing": validation_report.get("is_passing", False),
            "issue_count": len(validation_report.get("issues", [])),
        }
        
        self._store.append([record])
    
    def get_bundle_metrics(self, bundle_id: str) -> dict | None:
        """Obtiene métricas de un bundle."""
        results = self._store.query(bundle_id=bundle_id)
        return results[0] if results else None
    
    def get_all_metrics(self) -> list[dict]:
        """Obtiene todas las métricas."""
        return self._store.read_all()
    
    def get_average_scores(self) -> dict[str, float]:
        """Calcula scores promedio."""
        records = self._store.read_all()
        if not records:
            return {}
        
        scores = ["atomicity_score", "evidence_score", "format_score", "coherence_score", "total_score"]
        averages = {}
        
        for score in scores:
            values = [r.get(score, 0) for r in records]
            averages[score] = sum(values) / len(values) if values else 0
        
        return averages


class ProcessingHistoryStore:
    """
    Store para historial de procesamiento.
    """
    
    def __init__(self, data_path: Path | str):
        self.data_path = Path(data_path)
        store_path = self.data_path / "work" / "history"
        
        if PANDAS_AVAILABLE and PYARROW_AVAILABLE:
            self._store = ParquetStore(store_path, "processing_history")
        else:
            self._store = JsonDataStore(store_path, "processing_history")
    
    def log_event(
        self,
        event_type: str,
        source_id: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        """
        Registra un evento de procesamiento.
        
        Args:
            event_type: Tipo de evento (phase1_started, phase2_completed, etc.)
            source_id: ID de la fuente
            details: Detalles adicionales
        """
        record = {
            "event_type": event_type,
            "source_id": source_id,
            "timestamp": datetime.now().isoformat(),
            "details_json": json.dumps(details or {}),
        }
        
        self._store.append([record])
    
    def get_source_history(self, source_id: str) -> list[dict]:
        """Obtiene historial de una fuente."""
        return self._store.query(source_id=source_id)
    
    def get_recent_events(self, limit: int = 100) -> list[dict]:
        """Obtiene eventos recientes."""
        all_events = self._store.read_all()
        # Ordenar por timestamp descendente
        sorted_events = sorted(
            all_events,
            key=lambda x: x.get("timestamp", ""),
            reverse=True
        )
        return sorted_events[:limit]


# =============================================================================
# FUNCIONES DE CONVENIENCIA
# =============================================================================

def save_chunks(
    data_path: Path | str,
    lesson_id: str,
    chunks: list[dict],
) -> int:
    """
    Guarda chunks de una lección.
    
    Args:
        data_path: Path base de datos
        lesson_id: ID de la lección
        chunks: Chunks a guardar
        
    Returns:
        Número de chunks guardados
    """
    store = ChunkStore(data_path)
    return store.save_lesson_chunks(lesson_id, chunks)


def save_metrics(
    data_path: Path | str,
    bundle_id: str,
    validation_report: dict,
) -> None:
    """
    Guarda métricas de validación.
    
    Args:
        data_path: Path base de datos
        bundle_id: ID del bundle
        validation_report: Reporte de validación
    """
    store = MetricsStore(data_path)
    store.save_validation_metrics(bundle_id, validation_report)


def log_processing_event(
    data_path: Path | str,
    event_type: str,
    source_id: str,
    details: dict | None = None,
) -> None:
    """
    Registra un evento de procesamiento.
    
    Args:
        data_path: Path base de datos
        event_type: Tipo de evento
        source_id: ID de la fuente
        details: Detalles adicionales
    """
    store = ProcessingHistoryStore(data_path)
    store.log_event(event_type, source_id, details)