"""
graph_store.py — Persistencia del Grafo de Conocimiento

Este módulo gestiona la persistencia del knowledge graph,
soportando múltiples formatos (GraphML, JSON node-link, GML).

RESPONSABILIDAD:
- Cargar/guardar el grafo
- Conversión entre formatos
- Backup y recovery
- Validación de integridad

FORMATOS SOPORTADOS:
- JSON (node-link): Recomendado, legible y fácil de debuggear
- GraphML: Estándar XML, buena interoperabilidad
- GML: Formato texto, legacy

CONEXIONES:
- Usado por: graph_rag_builder.py
- Lee/Escribe: data/index/knowledge_graph.*
"""

from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

import networkx as nx


# =============================================================================
# CONFIGURACIÓN
# =============================================================================

SUPPORTED_FORMATS = ["json", "graphml", "gml"]
DEFAULT_FORMAT = "json"


# =============================================================================
# CLASE PRINCIPAL
# =============================================================================

class GraphStore:
    """
    Gestor de persistencia para el grafo de conocimiento.
    
    Attributes:
        index_path: Directorio donde se guarda el grafo
        format: Formato de archivo (json, graphml, gml)
    """
    
    def __init__(
        self,
        index_path: Path | str,
        format: str = DEFAULT_FORMAT,
    ):
        self.index_path = Path(index_path)
        self.index_path.mkdir(parents=True, exist_ok=True)
        
        if format not in SUPPORTED_FORMATS:
            raise ValueError(f"Formato no soportado: {format}. Use: {SUPPORTED_FORMATS}")
        
        self.format = format
        self._graph_file = self._get_graph_file()
        self._backup_dir = self.index_path / "backups"
    
    def _get_graph_file(self) -> Path:
        """Obtiene el path del archivo de grafo según el formato."""
        extensions = {
            "json": "json",
            "graphml": "graphml",
            "gml": "gml",
        }
        return self.index_path / f"knowledge_graph.{extensions[self.format]}"
    
    # =========================================================================
    # OPERACIONES BÁSICAS
    # =========================================================================
    
    def load(self) -> nx.DiGraph:
        """
        Carga el grafo desde disco.
        
        Returns:
            Grafo cargado (o nuevo si no existe)
        """
        if not self._graph_file.exists():
            return nx.DiGraph()
        
        try:
            if self.format == "json":
                return self._load_json()
            elif self.format == "graphml":
                return self._load_graphml()
            elif self.format == "gml":
                return self._load_gml()
        except Exception as e:
            print(f"Error cargando grafo: {e}")
            # Intentar recuperar de backup
            return self._recover_from_backup()
        
        return nx.DiGraph()
    
    def save(self, graph: nx.DiGraph, create_backup: bool = True) -> None:
        """
        Guarda el grafo a disco.
        
        Args:
            graph: Grafo a guardar
            create_backup: Si crear backup antes de sobrescribir
        """
        if create_backup and self._graph_file.exists():
            self._create_backup()
        
        try:
            if self.format == "json":
                self._save_json(graph)
            elif self.format == "graphml":
                self._save_graphml(graph)
            elif self.format == "gml":
                self._save_gml(graph)
        except Exception as e:
            print(f"Error guardando grafo: {e}")
            raise
    
    # =========================================================================
    # IMPLEMENTACIONES POR FORMATO
    # =========================================================================
    
    def _load_json(self) -> nx.DiGraph:
        """Carga desde formato JSON (node-link)."""
        with open(self._graph_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        return nx.node_link_graph(data, directed=True)
    
    def _save_json(self, graph: nx.DiGraph) -> None:
        """Guarda en formato JSON (node-link)."""
        data = nx.node_link_data(graph)
        
        # Añadir metadata
        data["_metadata"] = {
            "saved_at": datetime.now().isoformat(),
            "node_count": graph.number_of_nodes(),
            "edge_count": graph.number_of_edges(),
            "format_version": "1.0",
        }
        
        # Escritura atómica
        temp_file = self._graph_file.with_suffix(".tmp")
        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False, default=str)
        
        temp_file.rename(self._graph_file)
    
    def _load_graphml(self) -> nx.DiGraph:
        """Carga desde formato GraphML."""
        return nx.read_graphml(self._graph_file)
    
    def _save_graphml(self, graph: nx.DiGraph) -> None:
        """Guarda en formato GraphML."""
        # GraphML no soporta todos los tipos de datos
        # Convertir valores complejos a strings
        clean_graph = self._prepare_for_graphml(graph)
        nx.write_graphml(clean_graph, self._graph_file)
    
    def _load_gml(self) -> nx.DiGraph:
        """Carga desde formato GML."""
        return nx.read_gml(self._graph_file)
    
    def _save_gml(self, graph: nx.DiGraph) -> None:
        """Guarda en formato GML."""
        # GML tiene restricciones similares a GraphML
        clean_graph = self._prepare_for_gml(graph)
        nx.write_gml(clean_graph, self._graph_file)
    
    def _prepare_for_graphml(self, graph: nx.DiGraph) -> nx.DiGraph:
        """Prepara el grafo para GraphML (convierte tipos no soportados)."""
        clean = graph.copy()
        
        for node in clean.nodes():
            for key, value in list(clean.nodes[node].items()):
                if isinstance(value, (list, dict)):
                    clean.nodes[node][key] = json.dumps(value)
                elif value is None:
                    clean.nodes[node][key] = ""
        
        for u, v in clean.edges():
            for key, value in list(clean.edges[u, v].items()):
                if isinstance(value, (list, dict)):
                    clean.edges[u, v][key] = json.dumps(value)
                elif value is None:
                    clean.edges[u, v][key] = ""
        
        return clean
    
    def _prepare_for_gml(self, graph: nx.DiGraph) -> nx.DiGraph:
        """Prepara el grafo para GML."""
        # Similar a GraphML pero más restrictivo
        return self._prepare_for_graphml(graph)
    
    # =========================================================================
    # BACKUP Y RECOVERY
    # =========================================================================
    
    def _create_backup(self) -> Path | None:
        """
        Crea un backup del grafo actual.
        
        Returns:
            Path al backup creado
        """
        if not self._graph_file.exists():
            return None
        
        self._backup_dir.mkdir(parents=True, exist_ok=True)
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_file = self._backup_dir / f"knowledge_graph_{timestamp}.{self.format}"
        
        shutil.copy2(self._graph_file, backup_file)
        
        # Limpiar backups antiguos (mantener últimos 10)
        self._cleanup_old_backups(keep=10)
        
        return backup_file
    
    def _cleanup_old_backups(self, keep: int = 10) -> None:
        """Elimina backups antiguos."""
        backups = sorted(
            self._backup_dir.glob(f"knowledge_graph_*.{self.format}"),
            key=lambda p: p.stat().st_mtime,
            reverse=True
        )
        
        for backup in backups[keep:]:
            backup.unlink()
    
    def _recover_from_backup(self) -> nx.DiGraph:
        """
        Intenta recuperar el grafo desde el backup más reciente.
        
        Returns:
            Grafo recuperado o nuevo si no hay backups
        """
        if not self._backup_dir.exists():
            return nx.DiGraph()
        
        backups = sorted(
            self._backup_dir.glob(f"knowledge_graph_*.{self.format}"),
            key=lambda p: p.stat().st_mtime,
            reverse=True
        )
        
        for backup in backups:
            try:
                # Restaurar backup
                shutil.copy2(backup, self._graph_file)
                return self.load()
            except Exception as e:
                print(f"Error restaurando backup {backup}: {e}")
                continue
        
        return nx.DiGraph()
    
    def list_backups(self) -> list[dict[str, Any]]:
        """
        Lista todos los backups disponibles.
        
        Returns:
            Lista de {path, created_at, size_bytes}
        """
        if not self._backup_dir.exists():
            return []
        
        backups = []
        for backup in self._backup_dir.glob(f"knowledge_graph_*.{self.format}"):
            stat = backup.stat()
            backups.append({
                "path": str(backup),
                "created_at": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                "size_bytes": stat.st_size,
            })
        
        return sorted(backups, key=lambda x: x["created_at"], reverse=True)
    
    def restore_backup(self, backup_path: Path | str) -> nx.DiGraph:
        """
        Restaura un backup específico.
        
        Args:
            backup_path: Path al archivo de backup
            
        Returns:
            Grafo restaurado
        """
        backup_path = Path(backup_path)
        if not backup_path.exists():
            raise FileNotFoundError(f"Backup no encontrado: {backup_path}")
        
        # Crear backup del actual antes de restaurar
        self._create_backup()
        
        # Restaurar
        shutil.copy2(backup_path, self._graph_file)
        return self.load()
    
    # =========================================================================
    # VALIDACIÓN
    # =========================================================================
    
    def validate(self, graph: nx.DiGraph) -> list[str]:
        """
        Valida la integridad del grafo.
        
        Args:
            graph: Grafo a validar
            
        Returns:
            Lista de errores encontrados (vacía si todo OK)
        """
        errors = []
        
        # 1. Verificar nodos tienen ID
        for node in graph.nodes():
            if not node:
                errors.append("Nodo con ID vacío encontrado")
        
        # 2. Verificar edges apuntan a nodos existentes
        for u, v in graph.edges():
            if u not in graph:
                errors.append(f"Edge apunta a nodo inexistente: {u}")
            if v not in graph:
                errors.append(f"Edge apunta a nodo inexistente: {v}")
        
        # 3. Verificar no hay self-loops problemáticos
        self_loops = list(nx.selfloop_edges(graph))
        if self_loops:
            errors.append(f"Self-loops encontrados: {len(self_loops)}")
        
        # 4. Verificar metadata básica en nodos
        nodes_without_title = [
            n for n in graph.nodes()
            if not graph.nodes[n].get("title")
        ]
        if nodes_without_title:
            errors.append(f"Nodos sin título: {len(nodes_without_title)}")
        
        return errors
    
    # =========================================================================
    # CONVERSIÓN ENTRE FORMATOS
    # =========================================================================
    
    def export(
        self,
        graph: nx.DiGraph,
        target_format: str,
        target_path: Path | str | None = None,
    ) -> Path:
        """
        Exporta el grafo a otro formato.
        
        Args:
            graph: Grafo a exportar
            target_format: Formato destino (json, graphml, gml)
            target_path: Path destino (opcional)
            
        Returns:
            Path al archivo exportado
        """
        if target_format not in SUPPORTED_FORMATS:
            raise ValueError(f"Formato no soportado: {target_format}")
        
        if target_path is None:
            target_path = self.index_path / f"knowledge_graph_export.{target_format}"
        else:
            target_path = Path(target_path)
        
        # Crear store temporal para el formato destino
        temp_store = GraphStore(target_path.parent, format=target_format)
        temp_store._graph_file = target_path
        temp_store.save(graph, create_backup=False)
        
        return target_path
    
    # =========================================================================
    # ESTADÍSTICAS
    # =========================================================================
    
    def get_stats(self) -> dict[str, Any]:
        """
        Obtiene estadísticas del almacenamiento.
        
        Returns:
            Diccionario con estadísticas
        """
        graph = self.load()
        
        stats = {
            "format": self.format,
            "file_path": str(self._graph_file),
            "file_exists": self._graph_file.exists(),
            "file_size_bytes": self._graph_file.stat().st_size if self._graph_file.exists() else 0,
            "node_count": graph.number_of_nodes(),
            "edge_count": graph.number_of_edges(),
            "backup_count": len(self.list_backups()),
        }
        
        if graph.number_of_nodes() > 0:
            stats["avg_degree"] = sum(dict(graph.degree()).values()) / graph.number_of_nodes()
            stats["density"] = nx.density(graph)
        
        return stats


# =============================================================================
# FUNCIONES DE CONVENIENCIA
# =============================================================================

def load_knowledge_graph(index_path: Path | str) -> nx.DiGraph:
    """
    Carga el grafo de conocimiento.
    
    Args:
        index_path: Directorio de índices
        
    Returns:
        Grafo cargado
    """
    store = GraphStore(index_path)
    return store.load()


def save_knowledge_graph(
    index_path: Path | str,
    graph: nx.DiGraph,
) -> None:
    """
    Guarda el grafo de conocimiento.
    
    Args:
        index_path: Directorio de índices
        graph: Grafo a guardar
    """
    store = GraphStore(index_path)
    store.save(graph)


def get_graph_stats(index_path: Path | str) -> dict[str, Any]:
    """
    Obtiene estadísticas del grafo.
    
    Args:
        index_path: Directorio de índices
        
    Returns:
        Diccionario con estadísticas
    """
    store = GraphStore(index_path)
    return store.get_stats()