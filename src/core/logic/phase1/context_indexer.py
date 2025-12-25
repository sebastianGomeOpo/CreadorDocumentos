"""
context_indexer.py — El Indexador RAG

Reemplaza al chunk_persister. En lugar de cortar archivos físicamente,
crea un índice vectorial que permite búsqueda semántica.

PROBLEMA QUE RESUELVE:
- La segmentación física fallaba con textos sin estructura clara
- Un bloque recibía todo, los demás quedaban vacíos
- Los writers alucinaban por falta de contexto

SOLUCIÓN RAG:
- Normaliza y trocea el texto con sliding windows
- Crea embeddings y los guarda en ChromaDB
- Cada Writer busca activamente lo que necesita (Pull vs Push)

VENTAJAS:
1. Independencia del formato original
2. Contexto dinámico por tema
3. Sin duplicación de archivos

CONEXIONES:
- Input: raw_content + MasterPlan
- Output: Ruta a la base de datos vectorial
- Usado por: writer_agent.py (búsqueda semántica)
"""

from __future__ import annotations

import hashlib
import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_core.documents import Document

# Imports para embeddings y vectorstore
try:
    from langchain_openai import OpenAIEmbeddings
    from langchain_chroma import Chroma
    HAS_VECTOR_DEPS = True
except ImportError:
    HAS_VECTOR_DEPS = False
    print("⚠️ Dependencias vectoriales no instaladas. Ejecuta:")
    print("   pip install langchain-chroma langchain-openai chromadb")

from dotenv import load_dotenv

load_dotenv()


# =============================================================================
# CONFIGURACIÓN
# =============================================================================

DEFAULT_VECTOR_DB_DIR = Path("data/temp/vector_db")
CHUNK_SIZE = 1000  # Caracteres por chunk
CHUNK_OVERLAP = 200  # Solapamiento para contexto
COLLECTION_NAME = "class_content"


# =============================================================================
# LIMPIEZA DE TEXTO
# =============================================================================

def clean_text(text: str) -> str:
    """
    Limpia y normaliza el texto antes de indexar.
    
    - Normaliza saltos de línea
    - Elimina espacios múltiples
    - Preserva estructura semántica
    """
    import re
    
    # Normalizar saltos de línea
    text = text.replace('\r\n', '\n').replace('\r', '\n')
    
    # Reducir múltiples saltos a máximo 2
    text = re.sub(r'\n{3,}', '\n\n', text)
    
    # Reducir espacios múltiples a uno
    text = re.sub(r'[ \t]+', ' ', text)
    
    # Limpiar líneas que solo tienen espacios
    lines = [line.strip() for line in text.split('\n')]
    text = '\n'.join(lines)
    
    return text.strip()


# =============================================================================
# CLASE PRINCIPAL: CONTEXT INDEXER
# =============================================================================

class ContextIndexer:
    """
    Indexador de contexto usando RAG (Retrieval Augmented Generation).
    
    Workflow:
    1. Recibe texto crudo
    2. Limpia y normaliza
    3. Divide en chunks con sliding window
    4. Crea embeddings con OpenAI
    5. Almacena en ChromaDB local
    """
    
    def __init__(
        self,
        base_path: Path | str = DEFAULT_VECTOR_DB_DIR,
        chunk_size: int = CHUNK_SIZE,
        chunk_overlap: int = CHUNK_OVERLAP,
    ):
        if not HAS_VECTOR_DEPS:
            raise ImportError(
                "Dependencias vectoriales no instaladas. "
                "Ejecuta: pip install langchain-chroma langchain-openai chromadb"
            )
        
        self.base_path = Path(base_path)
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        
        # Inicializar text splitter
        self.text_splitter = RecursiveCharacterTextSplitter(
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            length_function=len,
            separators=["\n\n", "\n", ". ", " ", ""],
        )
        
        # Inicializar embeddings
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise ValueError("OPENAI_API_KEY no configurada")
        
        self.embeddings = OpenAIEmbeddings(
            model="text-embedding-3-small",
            api_key=api_key,
        )
    
    def index_content(
        self,
        raw_content: str,
        source_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        Indexa el contenido crudo en una base de datos vectorial.
        
        Args:
            raw_content: Texto crudo completo
            source_id: ID único de la fuente
            metadata: Metadata adicional opcional
            
        Returns:
            Diccionario con info de la DB creada
        """
        # 1. Limpiar texto
        clean_content = clean_text(raw_content)
        
        # 2. Dividir en chunks
        chunks = self.text_splitter.split_text(clean_content)
        
        # 3. Crear documentos con metadata
        documents = []
        for i, chunk in enumerate(chunks):
            doc = Document(
                page_content=chunk,
                metadata={
                    "source_id": source_id,
                    "chunk_index": i,
                    "total_chunks": len(chunks),
                    "chunk_size": len(chunk),
                    "indexed_at": datetime.now().isoformat(),
                    **(metadata or {}),
                }
            )
            documents.append(doc)
        
        # 4. Crear directorio para esta fuente
        db_path = self.base_path / source_id
        
        # Limpiar si existe
        if db_path.exists():
            shutil.rmtree(db_path)
        db_path.mkdir(parents=True, exist_ok=True)
        
        # 5. Crear vectorstore
        vectorstore = Chroma.from_documents(
            documents=documents,
            embedding=self.embeddings,
            collection_name=COLLECTION_NAME,
            persist_directory=str(db_path),
        )
        
        # 6. Generar estadísticas
        stats = {
            "source_id": source_id,
            "db_path": str(db_path),
            "total_chunks": len(chunks),
            "total_characters": len(clean_content),
            "avg_chunk_size": sum(len(c) for c in chunks) // len(chunks) if chunks else 0,
            "indexed_at": datetime.now().isoformat(),
        }
        
        return stats
    
    def get_retriever(
        self,
        source_id: str,
        k: int = 5,
    ):
        """
        Obtiene un retriever para una fuente indexada.
        
        Args:
            source_id: ID de la fuente
            k: Número de chunks a recuperar
            
        Returns:
            Retriever configurado
        """
        db_path = self.base_path / source_id
        
        if not db_path.exists():
            raise ValueError(f"No existe índice para source_id: {source_id}")
        
        vectorstore = Chroma(
            collection_name=COLLECTION_NAME,
            embedding_function=self.embeddings,
            persist_directory=str(db_path),
        )
        
        return vectorstore.as_retriever(
            search_type="similarity",
            search_kwargs={"k": k},
        )
    
    def search(
        self,
        source_id: str,
        query: str,
        k: int = 5,
    ) -> list[Document]:
        """
        Busca chunks relevantes para una query.
        
        Args:
            source_id: ID de la fuente
            query: Texto de búsqueda
            k: Número de resultados
            
        Returns:
            Lista de documentos relevantes
        """
        retriever = self.get_retriever(source_id, k)
        return retriever.invoke(query)
    
    def cleanup(self, source_id: str | None = None) -> int:
        """
        Limpia bases de datos vectoriales.
        
        Args:
            source_id: Si se proporciona, solo limpia esa fuente.
                      Si es None, limpia todas.
        
        Returns:
            Número de DBs eliminadas
        """
        count = 0
        
        if source_id:
            db_path = self.base_path / source_id
            if db_path.exists():
                shutil.rmtree(db_path)
                count = 1
        else:
            if self.base_path.exists():
                for item in self.base_path.iterdir():
                    if item.is_dir():
                        shutil.rmtree(item)
                        count += 1
        
        return count


# =============================================================================
# FUNCIONES DE CONVENIENCIA PARA EL GRAFO
# =============================================================================

def index_content_for_rag(
    raw_content: str,
    source_id: str,
    base_path: Path | str = DEFAULT_VECTOR_DB_DIR,
) -> dict[str, Any]:
    """
    Función de entrada para el nodo indexer del grafo.
    
    Args:
        raw_content: Texto crudo
        source_id: ID de la fuente
        base_path: Directorio para la DB
        
    Returns:
        Info de la DB creada
    """
    indexer = ContextIndexer(base_path)
    return indexer.index_content(raw_content, source_id)


def search_context(
    source_id: str,
    query: str,
    k: int = 5,
    base_path: Path | str = DEFAULT_VECTOR_DB_DIR,
) -> list[str]:
    """
    Busca contexto relevante para un tema.
    
    Args:
        source_id: ID de la fuente
        query: Texto de búsqueda (nombre del tema + conceptos)
        k: Número de chunks a recuperar
        base_path: Directorio de la DB
        
    Returns:
        Lista de textos relevantes
    """
    indexer = ContextIndexer(base_path)
    docs = indexer.search(source_id, query, k)
    return [doc.page_content for doc in docs]


def cleanup_vector_db(
    source_id: str | None = None,
    base_path: Path | str = DEFAULT_VECTOR_DB_DIR,
) -> int:
    """
    Limpia bases de datos vectoriales.
    
    Args:
        source_id: ID específico o None para todos
        base_path: Directorio base
        
    Returns:
        Número de DBs eliminadas
    """
    indexer = ContextIndexer(base_path)
    return indexer.cleanup(source_id)


# =============================================================================
# RETRIEVER FACTORY PARA WRITERS
# =============================================================================

class TopicRetriever:
    """
    Retriever especializado para un tema específico.
    
    Combina el nombre del tema + conceptos clave para construir
    queries más efectivas.
    """
    
    def __init__(
        self,
        source_id: str,
        topic_name: str,
        key_concepts: list[str],
        must_include: list[str],
        base_path: Path | str = DEFAULT_VECTOR_DB_DIR,
        k: int = 8,
    ):
        self.source_id = source_id
        self.topic_name = topic_name
        self.key_concepts = key_concepts
        self.must_include = must_include
        self.base_path = Path(base_path)
        self.k = k
        
        self.indexer = ContextIndexer(base_path)
    
    def get_context(self) -> str:
        """
        Recupera contexto relevante para el tema.
        
        Estrategia de búsqueda múltiple:
        1. Buscar por nombre del tema
        2. Buscar por cada concepto clave
        3. Buscar por cada must_include
        4. Combinar y deduplicar
        
        Returns:
            Texto concatenado de contexto relevante
        """
        all_chunks = set()
        
        # Búsqueda 1: Por nombre del tema
        query1 = self.topic_name
        docs1 = self.indexer.search(self.source_id, query1, k=self.k)
        for doc in docs1:
            all_chunks.add(doc.page_content)
        
        # Búsqueda 2: Por conceptos clave
        if self.key_concepts:
            query2 = " ".join(self.key_concepts[:5])  # Máximo 5
            docs2 = self.indexer.search(self.source_id, query2, k=self.k // 2)
            for doc in docs2:
                all_chunks.add(doc.page_content)
        
        # Búsqueda 3: Por must_include
        if self.must_include:
            query3 = " ".join(self.must_include[:3])  # Máximo 3
            docs3 = self.indexer.search(self.source_id, query3, k=self.k // 2)
            for doc in docs3:
                all_chunks.add(doc.page_content)
        
        # Combinar con separadores claros
        combined = "\n\n---\n\n".join(sorted(all_chunks, key=len, reverse=True))
        
        return combined
    
    def get_context_for_query(self, custom_query: str, k: int = 5) -> str:
        """
        Búsqueda personalizada con query específica.
        
        Args:
            custom_query: Query personalizada
            k: Número de resultados
            
        Returns:
            Contexto concatenado
        """
        docs = self.indexer.search(self.source_id, custom_query, k)
        return "\n\n---\n\n".join(doc.page_content for doc in docs)