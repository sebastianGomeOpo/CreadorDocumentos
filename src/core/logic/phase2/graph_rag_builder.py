"""
graph_rag_builder.py — Constructor y Gestor del GraphRAG

Este módulo es responsable de la "higiene del grafo":
- Canonicalización de IDs
- Tipado de relaciones y consistencia
- Evitar edges basura
- Detección de clusters y hubs
- Sugerencias de MOC

RESPONSABILIDAD:
Mantener el grafo de conocimiento limpio y útil para RAG.
Opera principalmente post-commit, actualizando el grafo
con las notas recién añadidas.

CONEXIONES:
- Llamado por: phase2_graph.py (nodo graph_rag_context, post-commit)
- Lee: data/index/knowledge_graph.*
- Lee: data/vault/notes/
- Escribe: data/index/knowledge_graph.*
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

import networkx as nx

from core.state_schema import LinkType


# =============================================================================
# CONFIGURACIÓN
# =============================================================================

# Tipos de nodos
NODE_TYPES = ["note", "concept", "topic", "moc", "source"]

# Tipos de edges permitidos (mapeo a nuestro LinkType)
EDGE_TYPES = {
    "defines": LinkType.DEFINES,
    "contrasts": LinkType.CONTRASTS,
    "depends_on": LinkType.DEPENDS_ON,
    "exemplifies": LinkType.EXEMPLIFIES,
    "refutes": LinkType.REFUTES,
    "applies": LinkType.APPLIES,
    "extends": LinkType.EXTENDS,
    "relates": LinkType.RELATES,
    "contains": LinkType.RELATES,  # Para MOCs
}


# =============================================================================
# CLASE PRINCIPAL
# =============================================================================

class KnowledgeGraphRAG:
    """
    Gestor del grafo de conocimiento para RAG.
    
    El grafo usa NetworkX internamente y se persiste como GraphML
    o node-link JSON.
    
    Estructura:
    - Nodos: Notas, conceptos, topics, MOCs
    - Edges: Relaciones tipadas entre nodos
    
    Attributes:
        graph: El grafo NetworkX
        index_path: Directorio de índices
    """
    
    def __init__(self, index_path: Path | str):
        self.index_path = Path(index_path)
        self.index_path.mkdir(parents=True, exist_ok=True)
        
        self.graph_file = self.index_path / "knowledge_graph.json"
        self.graph: nx.DiGraph = self._load_or_create()
    
    def _load_or_create(self) -> nx.DiGraph:
        """Carga el grafo existente o crea uno nuevo."""
        if self.graph_file.exists():
            try:
                with open(self.graph_file, "r") as f:
                    data = json.load(f)
                return nx.node_link_graph(data, directed=True)
            except Exception as e:
                print(f"Error cargando grafo: {e}. Creando nuevo.")
        
        return nx.DiGraph()
    
    def save(self) -> None:
        """Persiste el grafo a disco."""
        data = nx.node_link_data(self.graph)
        with open(self.graph_file, "w") as f:
            json.dump(data, f, indent=2, ensure_ascii=False, default=str)
    
    # =========================================================================
    # OPERACIONES CRUD
    # =========================================================================
    
    def add_note(
        self,
        note_id: str,
        title: str,
        note_type: str = "note",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """
        Añade una nota como nodo al grafo.
        
        Args:
            note_id: ID único de la nota
            title: Título de la nota
            note_type: Tipo de nodo (note, concept, moc, etc.)
            metadata: Metadatos adicionales
        """
        metadata = metadata or {}
        
        self.graph.add_node(
            note_id,
            title=title,
            node_type=note_type,
            created_at=datetime.now().isoformat(),
            **metadata
        )
    
    def add_link(
        self,
        source_id: str,
        target_id: str,
        link_type: str | LinkType,
        rationale: str = "",
        confidence: float = 1.0,
    ) -> None:
        """
        Añade un enlace entre dos nodos.
        
        Args:
            source_id: ID del nodo fuente
            target_id: ID del nodo destino
            link_type: Tipo de enlace
            rationale: Razón del enlace
            confidence: Confianza del enlace (0-1)
        """
        if isinstance(link_type, LinkType):
            link_type = link_type.value
        
        # Validar que el tipo sea conocido
        if link_type not in EDGE_TYPES:
            link_type = "relates"
        
        # Crear nodos si no existen (con metadata mínima)
        if source_id not in self.graph:
            self.add_note(source_id, source_id, "unknown")
        if target_id not in self.graph:
            self.add_note(target_id, target_id, "unknown")
        
        self.graph.add_edge(
            source_id,
            target_id,
            link_type=link_type,
            rationale=rationale,
            confidence=confidence,
            created_at=datetime.now().isoformat(),
        )
    
    def remove_note(self, note_id: str) -> None:
        """Elimina una nota y todos sus enlaces."""
        if note_id in self.graph:
            self.graph.remove_node(note_id)
    
    def update_note(self, note_id: str, **kwargs) -> None:
        """Actualiza metadatos de una nota."""
        if note_id in self.graph:
            self.graph.nodes[note_id].update(kwargs)
    
    # =========================================================================
    # CONSULTAS
    # =========================================================================
    
    def get_neighbors(
        self,
        note_id: str,
        hops: int = 1,
        link_types: list[str] | None = None,
    ) -> list[str]:
        """
        Obtiene vecinos a N hops de distancia.
        
        Args:
            note_id: Nodo inicial
            hops: Número de saltos (default: 1)
            link_types: Filtrar por tipos de enlace
            
        Returns:
            Lista de IDs de nodos vecinos
        """
        if note_id not in self.graph:
            return []
        
        neighbors = set()
        current_level = {note_id}
        
        for _ in range(hops):
            next_level = set()
            for node in current_level:
                # Vecinos salientes
                for _, target, data in self.graph.out_edges(node, data=True):
                    if link_types is None or data.get("link_type") in link_types:
                        next_level.add(target)
                
                # Vecinos entrantes
                for source, _, data in self.graph.in_edges(node, data=True):
                    if link_types is None or data.get("link_type") in link_types:
                        next_level.add(source)
            
            neighbors.update(next_level)
            current_level = next_level - {note_id}
        
        return list(neighbors - {note_id})
    
    def find_similar_by_links(
        self,
        note_id: str,
        min_common_neighbors: int = 2,
    ) -> list[tuple[str, int]]:
        """
        Encuentra notas similares basándose en vecinos comunes.
        
        Args:
            note_id: Nota de referencia
            min_common_neighbors: Mínimo de vecinos en común
            
        Returns:
            Lista de (note_id, num_common_neighbors) ordenada
        """
        if note_id not in self.graph:
            return []
        
        my_neighbors = set(self.get_neighbors(note_id, hops=1))
        
        similar = []
        for node in self.graph.nodes():
            if node == note_id:
                continue
            
            their_neighbors = set(self.get_neighbors(node, hops=1))
            common = len(my_neighbors & their_neighbors)
            
            if common >= min_common_neighbors:
                similar.append((node, common))
        
        return sorted(similar, key=lambda x: x[1], reverse=True)
    
    def get_path(
        self,
        source_id: str,
        target_id: str,
    ) -> list[str] | None:
        """
        Encuentra el camino más corto entre dos notas.
        
        Returns:
            Lista de IDs en el camino, o None si no hay conexión
        """
        if source_id not in self.graph or target_id not in self.graph:
            return None
        
        try:
            # Convertir a no dirigido para encontrar caminos
            undirected = self.graph.to_undirected()
            path = nx.shortest_path(undirected, source_id, target_id)
            return path
        except nx.NetworkXNoPath:
            return None
    
    # =========================================================================
    # ANÁLISIS DEL GRAFO
    # =========================================================================
    
    def find_hubs(self, top_n: int = 10) -> list[tuple[str, int]]:
        """
        Encuentra los nodos más conectados (hubs).
        
        Returns:
            Lista de (note_id, degree) ordenada por conectividad
        """
        degrees = [(node, self.graph.degree(node)) for node in self.graph.nodes()]
        return sorted(degrees, key=lambda x: x[1], reverse=True)[:top_n]
    
    def find_orphans(self) -> list[str]:
        """
        Encuentra notas sin conexiones (huérfanas).
        
        Returns:
            Lista de IDs de notas aisladas
        """
        return [
            node for node in self.graph.nodes()
            if self.graph.degree(node) == 0
        ]
    
    def find_clusters(self) -> list[set[str]]:
        """
        Detecta clusters de notas densamente conectadas.
        
        Returns:
            Lista de conjuntos de IDs (cada conjunto es un cluster)
        """
        # Convertir a no dirigido para detectar componentes
        undirected = self.graph.to_undirected()
        
        # Componentes conectados
        components = list(nx.connected_components(undirected))
        
        # Filtrar componentes pequeños
        return [c for c in components if len(c) > 1]
    
    def suggest_mocs(self, min_cluster_size: int = 5) -> list[dict[str, Any]]:
        """
        Sugiere MOCs basándose en clusters de notas.
        
        Args:
            min_cluster_size: Tamaño mínimo de cluster para sugerir MOC
            
        Returns:
            Lista de sugerencias de MOC
        """
        suggestions = []
        clusters = self.find_clusters()
        
        for i, cluster in enumerate(clusters):
            if len(cluster) >= min_cluster_size:
                # Encontrar el hub del cluster
                cluster_subgraph = self.graph.subgraph(cluster)
                hub = max(cluster, key=lambda n: cluster_subgraph.degree(n))
                hub_title = self.graph.nodes[hub].get("title", hub)
                
                suggestions.append({
                    "suggested_moc_title": f"MOC: {hub_title}",
                    "notes_count": len(cluster),
                    "hub_note": hub,
                    "notes": list(cluster),
                })
        
        return suggestions
    
    def detect_inconsistencies(self) -> list[dict[str, Any]]:
        """
        Detecta inconsistencias en el grafo.
        
        Busca:
        - Ciclos en relaciones depends_on
        - Nodos sin metadata
        - Enlaces duplicados contradictorios
        
        Returns:
            Lista de issues encontrados
        """
        issues = []
        
        # 1. Ciclos en depends_on
        depends_on_edges = [
            (u, v) for u, v, d in self.graph.edges(data=True)
            if d.get("link_type") == "depends_on"
        ]
        
        if depends_on_edges:
            deps_graph = nx.DiGraph(depends_on_edges)
            try:
                cycles = list(nx.simple_cycles(deps_graph))
                for cycle in cycles:
                    issues.append({
                        "type": "circular_dependency",
                        "description": f"Dependencia circular: {' -> '.join(cycle)}",
                        "nodes": cycle,
                    })
            except:
                pass
        
        # 2. Nodos sin título
        for node in self.graph.nodes():
            if not self.graph.nodes[node].get("title"):
                issues.append({
                    "type": "missing_metadata",
                    "description": f"Nodo sin título: {node}",
                    "nodes": [node],
                })
        
        # 3. Nodos con tipo desconocido
        for node, data in self.graph.nodes(data=True):
            if data.get("node_type") == "unknown":
                issues.append({
                    "type": "unknown_node_type",
                    "description": f"Nodo referenciado pero no definido: {node}",
                    "nodes": [node],
                })
        
        return issues
    
    # =========================================================================
    # CONSTRUCCIÓN DE CONTEXTO RAG
    # =========================================================================
    
    def build_context_for_query(
        self,
        query_concepts: list[str],
        similar_note_ids: list[str] | None = None,
        max_neighbors: int = 10,
    ) -> dict[str, Any]:
        """
        Construye contexto del grafo para una consulta RAG.
        
        Args:
            query_concepts: Conceptos clave de la consulta
            similar_note_ids: IDs de notas similares (de vector search)
            max_neighbors: Máximo de vecinos a incluir
            
        Returns:
            Contexto estructurado para el prompt
        """
        similar_note_ids = similar_note_ids or []
        
        # Recopilar vecinos de las notas similares
        all_neighbors = set()
        for note_id in similar_note_ids[:5]:  # Limitar
            neighbors = self.get_neighbors(note_id, hops=1)
            all_neighbors.update(neighbors[:max_neighbors // 2])
        
        # Buscar notas por conceptos en el título
        concept_matches = []
        for node, data in self.graph.nodes(data=True):
            title = data.get("title", "").lower()
            for concept in query_concepts:
                if concept.lower() in title:
                    concept_matches.append(node)
                    break
        
        all_neighbors.update(concept_matches[:5])
        
        # Construir resumen
        graph_summary_parts = []
        
        for note_id in list(all_neighbors)[:max_neighbors]:
            if note_id not in self.graph:
                continue
            
            node_data = self.graph.nodes[note_id]
            title = node_data.get("title", note_id)
            node_type = node_data.get("node_type", "note")
            
            # Obtener enlaces
            out_links = [
                f"{d.get('link_type', 'relates')} -> {t}"
                for _, t, d in self.graph.out_edges(note_id, data=True)
            ][:3]
            
            summary = f"- {title} ({node_type})"
            if out_links:
                summary += f": {', '.join(out_links)}"
            
            graph_summary_parts.append(summary)
        
        return {
            "similar_chunks": [],  # Viene de vector store
            "similar_notes": similar_note_ids,
            "graph_neighbors": list(all_neighbors),
            "retrieved_at": datetime.now().isoformat(),
            "summary": "\n".join(graph_summary_parts) if graph_summary_parts else "No hay contexto previo en el grafo",
        }
    
    # =========================================================================
    # INTEGRACIÓN POST-COMMIT
    # =========================================================================
    
    def integrate_bundle(
        self,
        notes: list[dict[str, Any]],
        links: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """
        Integra notas de un bundle aprobado al grafo.
        
        Args:
            notes: Notas del bundle
            links: Enlaces del bundle
            
        Returns:
            Resumen de la integración
        """
        added_nodes = 0
        added_edges = 0
        
        # Añadir notas
        for note in notes:
            self.add_note(
                note_id=note["id"],
                title=note.get("title", ""),
                note_type=note.get("frontmatter", {}).get("type", "note"),
                metadata={
                    "source_id": note.get("source_id", ""),
                    "tags": note.get("frontmatter", {}).get("tags", []),
                }
            )
            added_nodes += 1
        
        # Añadir enlaces
        for link in links:
            self.add_link(
                source_id=link["source_note_id"],
                target_id=link["target_note_id"],
                link_type=link.get("link_type", "relates"),
                rationale=link.get("rationale", ""),
                confidence=link.get("confidence", 0.8),
            )
            added_edges += 1
        
        # Persistir
        self.save()
        
        return {
            "added_nodes": added_nodes,
            "added_edges": added_edges,
            "total_nodes": self.graph.number_of_nodes(),
            "total_edges": self.graph.number_of_edges(),
        }
    
    # =========================================================================
    # ESTADÍSTICAS
    # =========================================================================
    
    def get_stats(self) -> dict[str, Any]:
        """Obtiene estadísticas del grafo."""
        return {
            "total_nodes": self.graph.number_of_nodes(),
            "total_edges": self.graph.number_of_edges(),
            "orphan_count": len(self.find_orphans()),
            "cluster_count": len(self.find_clusters()),
            "top_hubs": self.find_hubs(5),
        }


# =============================================================================
# FUNCIONES DE CONVENIENCIA
# =============================================================================

def build_rag_context(
    index_path: Path | str,
    query_concepts: list[str],
    similar_note_ids: list[str] | None = None,
) -> dict[str, Any]:
    """
    Función de conveniencia para construir contexto RAG.
    
    Args:
        index_path: Path al directorio de índices
        query_concepts: Conceptos de la consulta
        similar_note_ids: IDs de notas similares
        
    Returns:
        Contexto estructurado
    """
    rag = KnowledgeGraphRAG(index_path)
    return rag.build_context_for_query(query_concepts, similar_note_ids)


def integrate_approved_bundle(
    index_path: Path | str,
    notes: list[dict[str, Any]],
    links: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    Integra un bundle aprobado al grafo.
    
    Args:
        index_path: Path al directorio de índices
        notes: Notas del bundle
        links: Enlaces del bundle
        
    Returns:
        Resumen de la integración
    """
    rag = KnowledgeGraphRAG(index_path)
    return rag.integrate_bundle(notes, links)