"""
hierarchical_chunker.py — Chunking Semántico Jerárquico

Divide el documento en una estructura de dos niveles:
- BLOQUES: Ideas completas (párrafos semánticos, secciones)
- CHUNKS: Fragmentos de ~500-1000 chars dentro de cada bloque

Cada chunk conoce:
- Su bloque padre
- Sus vecinos (prev/next)
- Su posición en el bloque

Esto permite recuperación estructural, no mecánica.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Optional
from enum import Enum


# =============================================================================
# ESTRUCTURAS DE DATOS
# =============================================================================

class BlockType(Enum):
    """Tipos de bloques detectados."""
    HEADING_SECTION = "heading_section"      # Sección con encabezado
    PARAGRAPH_GROUP = "paragraph_group"      # Grupo de párrafos relacionados
    LIST_BLOCK = "list_block"                # Bloque de lista/enumeración
    CODE_BLOCK = "code_block"                # Bloque de código
    DIALOGUE_BLOCK = "dialogue_block"        # Diálogo/conversación
    GENERIC = "generic"                      # Fallback


@dataclass
class ChunkNode:
    """Un chunk individual con su contexto jerárquico."""
    chunk_id: str
    content: str
    block_id: str
    position_in_block: int
    total_in_block: int
    prev_chunk_id: Optional[str] = None
    next_chunk_id: Optional[str] = None
    char_start: int = 0
    char_end: int = 0
    
    @property
    def is_first_in_block(self) -> bool:
        return self.position_in_block == 0
    
    @property
    def is_last_in_block(self) -> bool:
        return self.position_in_block == self.total_in_block - 1


@dataclass
class BlockNode:
    """Un bloque (idea completa) que contiene chunks."""
    block_id: str
    content: str
    block_type: BlockType
    heading: Optional[str] = None
    chunk_ids: list[str] = field(default_factory=list)
    position_in_doc: int = 0
    prev_block_id: Optional[str] = None
    next_block_id: Optional[str] = None
    
    @property
    def summary(self) -> str:
        """Resumen del bloque para embedding."""
        if self.heading:
            return f"{self.heading}: {self.content[:200]}"
        return self.content[:300]


@dataclass
class HierarchicalDocument:
    """Documento completo con estructura jerárquica."""
    source_id: str
    blocks: list[BlockNode]
    chunks: list[ChunkNode]
    block_index: dict[str, BlockNode] = field(default_factory=dict)
    chunk_index: dict[str, ChunkNode] = field(default_factory=dict)
    
    def __post_init__(self):
        self.block_index = {b.block_id: b for b in self.blocks}
        self.chunk_index = {c.chunk_id: c for c in self.chunks}
    
    def get_parent(self, chunk_id: str) -> Optional[BlockNode]:
        """Obtiene el bloque padre de un chunk."""
        chunk = self.chunk_index.get(chunk_id)
        if chunk:
            return self.block_index.get(chunk.block_id)
        return None
    
    def get_neighbors(self, chunk_id: str) -> tuple[Optional[ChunkNode], Optional[ChunkNode]]:
        """Obtiene chunks vecinos (prev, next)."""
        chunk = self.chunk_index.get(chunk_id)
        if not chunk:
            return None, None
        prev_chunk = self.chunk_index.get(chunk.prev_chunk_id) if chunk.prev_chunk_id else None
        next_chunk = self.chunk_index.get(chunk.next_chunk_id) if chunk.next_chunk_id else None
        return prev_chunk, next_chunk
    
    def get_siblings(self, chunk_id: str) -> list[ChunkNode]:
        """Obtiene todos los chunks del mismo bloque."""
        chunk = self.chunk_index.get(chunk_id)
        if not chunk:
            return []
        block = self.block_index.get(chunk.block_id)
        if not block:
            return []
        return [self.chunk_index[cid] for cid in block.chunk_ids if cid in self.chunk_index]


# =============================================================================
# CONFIGURACIÓN
# =============================================================================

DEFAULT_CHUNK_SIZE = 800
DEFAULT_CHUNK_OVERLAP = 150
MIN_BLOCK_SIZE = 200
MAX_BLOCK_SIZE = 4000


# =============================================================================
# DETECCIÓN DE BLOQUES
# =============================================================================

def _generate_id(content: str, prefix: str, index: int) -> str:
    """Genera ID único basado en contenido."""
    hash_input = f"{content[:100]}_{index}"
    short_hash = hashlib.md5(hash_input.encode()).hexdigest()[:8]
    return f"{prefix}_{index:03d}_{short_hash}"


def _detect_block_type(text: str) -> BlockType:
    """Detecta el tipo de bloque basado en patrones."""
    text_stripped = text.strip()
    
    # Código
    if text_stripped.startswith("```") or text_stripped.startswith("    "):
        return BlockType.CODE_BLOCK
    
    # Lista
    if re.match(r'^[\s]*[-*•]\s', text_stripped, re.MULTILINE):
        return BlockType.LIST_BLOCK
    if re.match(r'^[\s]*\d+[.)]\s', text_stripped, re.MULTILINE):
        return BlockType.LIST_BLOCK
    
    # Diálogo (patrones comunes de transcripción)
    if re.search(r'^[A-Z][a-z]+:', text_stripped, re.MULTILINE):
        return BlockType.DIALOGUE_BLOCK
    if re.search(r'^\[.+?\]:', text_stripped, re.MULTILINE):
        return BlockType.DIALOGUE_BLOCK
    
    return BlockType.GENERIC


def _split_into_blocks(text: str) -> list[tuple[str, Optional[str], BlockType]]:
    """
    Divide el texto en bloques semánticos.
    
    Estrategia:
    1. Primero intenta dividir por headers markdown
    2. Si no hay headers, divide por dobles saltos de línea
    3. Agrupa párrafos pequeños consecutivos
    
    Returns:
        Lista de (contenido, heading, tipo)
    """
    blocks = []
    
    # Patrón para headers markdown
    header_pattern = r'^(#{1,4})\s+(.+)$'
    
    # Intentar división por headers
    header_splits = re.split(r'(^#{1,4}\s+.+$)', text, flags=re.MULTILINE)
    
    if len(header_splits) > 1:
        # Hay headers, procesar por secciones
        current_heading = None
        current_content = []
        
        for part in header_splits:
            part = part.strip()
            if not part:
                continue
            
            header_match = re.match(header_pattern, part)
            if header_match:
                # Guardar bloque anterior si existe
                if current_content:
                    content = "\n\n".join(current_content)
                    if len(content) >= MIN_BLOCK_SIZE:
                        block_type = _detect_block_type(content)
                        blocks.append((content, current_heading, block_type))
                    current_content = []
                current_heading = header_match.group(2).strip()
            else:
                current_content.append(part)
        
        # Último bloque
        if current_content:
            content = "\n\n".join(current_content)
            if len(content) >= MIN_BLOCK_SIZE // 2:
                block_type = _detect_block_type(content)
                blocks.append((content, current_heading, block_type))
    
    # Si no se detectaron bloques por headers, dividir por párrafos
    if not blocks:
        paragraphs = re.split(r'\n\s*\n', text)
        
        current_block = []
        current_size = 0
        
        for para in paragraphs:
            para = para.strip()
            if not para:
                continue
            
            para_size = len(para)
            
            # Si añadir este párrafo excede el máximo, cerrar bloque
            if current_size + para_size > MAX_BLOCK_SIZE and current_block:
                content = "\n\n".join(current_block)
                block_type = _detect_block_type(content)
                blocks.append((content, None, block_type))
                current_block = []
                current_size = 0
            
            current_block.append(para)
            current_size += para_size
            
            # Si alcanzamos un buen tamaño, cerrar bloque
            if current_size >= MIN_BLOCK_SIZE * 2:
                content = "\n\n".join(current_block)
                block_type = _detect_block_type(content)
                blocks.append((content, None, block_type))
                current_block = []
                current_size = 0
        
        # Último bloque
        if current_block:
            content = "\n\n".join(current_block)
            block_type = _detect_block_type(content)
            blocks.append((content, None, block_type))
    
    # Fallback: si aún no hay bloques, crear uno solo
    if not blocks:
        blocks.append((text, None, BlockType.GENERIC))
    
    return blocks


# =============================================================================
# CHUNKING DENTRO DE BLOQUES
# =============================================================================

def _split_block_into_chunks(
    block_content: str,
    block_id: str,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
) -> list[ChunkNode]:
    """
    Divide un bloque en chunks con overlap.
    Intenta cortar en límites naturales (oraciones, párrafos).
    """
    chunks = []
    
    # Si el bloque es pequeño, es un solo chunk
    if len(block_content) <= chunk_size:
        chunk_id = _generate_id(block_content, "chk", 0)
        chunk = ChunkNode(
            chunk_id=chunk_id,
            content=block_content,
            block_id=block_id,
            position_in_block=0,
            total_in_block=1,
            char_start=0,
            char_end=len(block_content),
        )
        return [chunk]
    
    # Dividir en oraciones para cortes más limpios
    sentences = re.split(r'(?<=[.!?])\s+', block_content)
    
    current_chunk = []
    current_size = 0
    chunk_index = 0
    char_position = 0
    
    for sentence in sentences:
        sentence_size = len(sentence)
        
        # Si añadir esta oración excede el tamaño, cerrar chunk
        if current_size + sentence_size > chunk_size and current_chunk:
            chunk_content = " ".join(current_chunk)
            chunk_id = _generate_id(chunk_content, "chk", chunk_index)
            
            chunk = ChunkNode(
                chunk_id=chunk_id,
                content=chunk_content,
                block_id=block_id,
                position_in_block=chunk_index,
                total_in_block=0,  # Se actualizará después
                char_start=char_position - len(chunk_content),
                char_end=char_position,
            )
            chunks.append(chunk)
            chunk_index += 1
            
            # Overlap: mantener las últimas oraciones
            overlap_size = 0
            overlap_sentences = []
            for s in reversed(current_chunk):
                if overlap_size + len(s) <= chunk_overlap:
                    overlap_sentences.insert(0, s)
                    overlap_size += len(s)
                else:
                    break
            
            current_chunk = overlap_sentences
            current_size = overlap_size
        
        current_chunk.append(sentence)
        current_size += sentence_size
        char_position += sentence_size + 1  # +1 por el espacio
    
    # Último chunk
    if current_chunk:
        chunk_content = " ".join(current_chunk)
        chunk_id = _generate_id(chunk_content, "chk", chunk_index)
        
        chunk = ChunkNode(
            chunk_id=chunk_id,
            content=chunk_content,
            block_id=block_id,
            position_in_block=chunk_index,
            total_in_block=0,
            char_start=char_position - len(chunk_content),
            char_end=char_position,
        )
        chunks.append(chunk)
    
    # Actualizar total_in_block y enlaces prev/next
    total = len(chunks)
    for i, chunk in enumerate(chunks):
        chunk.total_in_block = total
        if i > 0:
            chunk.prev_chunk_id = chunks[i - 1].chunk_id
        if i < total - 1:
            chunk.next_chunk_id = chunks[i + 1].chunk_id
    
    return chunks


# =============================================================================
# FUNCIÓN PRINCIPAL
# =============================================================================

class HierarchicalChunker:
    """
    Chunker jerárquico que produce bloques y chunks con relaciones.
    """
    
    def __init__(
        self,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
        chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
    ):
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
    
    def chunk_document(
        self,
        text: str,
        source_id: str,
    ) -> HierarchicalDocument:
        """
        Procesa un documento y retorna estructura jerárquica completa.
        
        Args:
            text: Contenido del documento
            source_id: ID único de la fuente
            
        Returns:
            HierarchicalDocument con bloques, chunks e índices
        """
        # 1. Limpiar texto
        text = self._clean_text(text)
        
        # 2. Dividir en bloques
        raw_blocks = _split_into_blocks(text)
        
        # 3. Crear BlockNodes y ChunkNodes
        blocks: list[BlockNode] = []
        all_chunks: list[ChunkNode] = []
        
        for block_idx, (content, heading, block_type) in enumerate(raw_blocks):
            block_id = _generate_id(content, "blk", block_idx)
            
            # Crear chunks para este bloque
            chunks = _split_block_into_chunks(
                content,
                block_id,
                self.chunk_size,
                self.chunk_overlap,
            )
            
            # Crear BlockNode
            block = BlockNode(
                block_id=block_id,
                content=content,
                block_type=block_type,
                heading=heading,
                chunk_ids=[c.chunk_id for c in chunks],
                position_in_doc=block_idx,
            )
            
            blocks.append(block)
            all_chunks.extend(chunks)
        
        # 4. Enlazar bloques prev/next
        for i, block in enumerate(blocks):
            if i > 0:
                block.prev_block_id = blocks[i - 1].block_id
            if i < len(blocks) - 1:
                block.next_block_id = blocks[i + 1].block_id
        
        # 5. Enlazar chunks entre bloques (prev del primero → último del bloque anterior)
        for i, block in enumerate(blocks):
            if i > 0 and block.chunk_ids and blocks[i - 1].chunk_ids:
                # Encontrar primer chunk de este bloque
                first_chunk_id = block.chunk_ids[0]
                # Encontrar último chunk del bloque anterior
                last_chunk_id = blocks[i - 1].chunk_ids[-1]
                
                # Buscar y actualizar
                for chunk in all_chunks:
                    if chunk.chunk_id == first_chunk_id:
                        chunk.prev_chunk_id = last_chunk_id
                    elif chunk.chunk_id == last_chunk_id:
                        chunk.next_chunk_id = first_chunk_id
        
        # 6. Crear documento jerárquico
        return HierarchicalDocument(
            source_id=source_id,
            blocks=blocks,
            chunks=all_chunks,
        )
    
    def _clean_text(self, text: str) -> str:
        """Limpia y normaliza el texto."""
        # Normalizar saltos de línea
        text = text.replace('\r\n', '\n').replace('\r', '\n')
        
        # Reducir múltiples saltos a máximo 2
        text = re.sub(r'\n{3,}', '\n\n', text)
        
        # Reducir espacios múltiples
        text = re.sub(r'[ \t]+', ' ', text)
        
        return text.strip()


# =============================================================================
# FUNCIONES DE CONVENIENCIA
# =============================================================================

def chunk_document(
    text: str,
    source_id: str,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
) -> HierarchicalDocument:
    """
    Función de conveniencia para chunking jerárquico.
    
    Args:
        text: Contenido del documento
        source_id: ID de la fuente
        chunk_size: Tamaño objetivo de chunks
        chunk_overlap: Solapamiento entre chunks
        
    Returns:
        HierarchicalDocument completo
    """
    chunker = HierarchicalChunker(chunk_size, chunk_overlap)
    return chunker.chunk_document(text, source_id)


def get_chunk_with_context(
    doc: HierarchicalDocument,
    chunk_id: str,
    include_neighbors: bool = True,
    include_parent_summary: bool = True,
) -> dict:
    """
    Obtiene un chunk con su contexto estructural.
    
    Args:
        doc: Documento jerárquico
        chunk_id: ID del chunk
        include_neighbors: Incluir chunks vecinos
        include_parent_summary: Incluir resumen del bloque padre
        
    Returns:
        Dict con chunk y contexto
    """
    chunk = doc.chunk_index.get(chunk_id)
    if not chunk:
        return {}
    
    result = {
        "chunk": chunk,
        "content": chunk.content,
    }
    
    if include_parent_summary:
        parent = doc.get_parent(chunk_id)
        if parent:
            result["parent_heading"] = parent.heading
            result["parent_summary"] = parent.summary
            result["block_type"] = parent.block_type.value
    
    if include_neighbors:
        prev_chunk, next_chunk = doc.get_neighbors(chunk_id)
        result["prev_content"] = prev_chunk.content if prev_chunk else None
        result["next_content"] = next_chunk.content if next_chunk else None
    
    return result