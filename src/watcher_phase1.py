"""
watcher_phase1.py — Vigilante de Inbox para Fase 1

Este proceso vigila la carpeta data/inbox/raw_classes/ y dispara
el Phase1Graph cuando detecta archivos nuevos o modificados.

COMPORTAMIENTO:
1. Escanea la carpeta inbox cada N segundos
2. Detecta archivos .txt/.md nuevos o modificados
3. Ejecuta Phase1Graph para cada archivo
4. Guarda el bundle resultante en staging/phase1_pending/
5. Mueve el archivo procesado a un directorio de "processed"

CONEXIONES:
- Vigila: data/inbox/raw_classes/
- Ejecuta: core/graphs/phase1_graph.py
- Escribe: data/staging/phase1_pending/
- Mueve a: data/inbox/processed/

USO:
    python watcher_phase1.py
    
    # O con intervalo personalizado:
    python watcher_phase1.py --interval 10
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path

# Añadir src al path
sys.path.insert(0, str(Path(__file__).parent))

from core.graphs.phase1_graph import run_phase1
from core.state_schema import Phase1Bundle, generate_bundle_id
from core.storage.bundles_fs import BundleStore


# =============================================================================
# CONFIGURACIÓN
# =============================================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("watcher_phase1")


class Phase1Watcher:
    """
    Vigilante que procesa archivos de texto en inbox.
    """
    
    def __init__(self, base_path: Path | str):
        self.base_path = Path(base_path)
        
        # Directorios
        self.inbox_path = self.base_path / "inbox" / "raw_classes"
        self.processed_path = self.base_path / "inbox" / "processed"
        self.lessons_ordered_path = self.base_path / "lessons" / "ordered"
        self.lessons_chunks_path = self.base_path / "lessons" / "chunks"
        
        # Storage
        self.bundle_store = BundleStore(self.base_path)
        
        # Registro de archivos procesados
        self.state_file = self.base_path / "work" / "phase1" / "watcher_state.json"
        self.processed_hashes: dict[str, str] = {}
        
        # Crear directorios
        self._ensure_directories()
        self._load_state()
    
    def _ensure_directories(self) -> None:
        for path in [
            self.inbox_path,
            self.processed_path,
            self.lessons_ordered_path,
            self.lessons_chunks_path,
            self.state_file.parent,
        ]:
            path.mkdir(parents=True, exist_ok=True)
    
    def _load_state(self) -> None:
        if self.state_file.exists():
            try:
                with open(self.state_file, "r") as f:
                    data = json.load(f)
                    self.processed_hashes = data.get("processed_hashes", {})
            except Exception as e:
                logger.warning(f"Error cargando estado: {e}")
                self.processed_hashes = {}
    
    def _save_state(self) -> None:
        with open(self.state_file, "w") as f:
            json.dump({"processed_hashes": self.processed_hashes}, f, indent=2)
    
    def _get_file_hash(self, path: Path) -> str:
        with open(path, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()
    
    def scan_inbox(self) -> list[Path]:
        to_process = []
        for ext in ["*.txt", "*.md"]:
            for file_path in self.inbox_path.glob(ext):
                if file_path.is_file():
                    current_hash = self._get_file_hash(file_path)
                    previous_hash = self.processed_hashes.get(str(file_path))
                    
                    if previous_hash != current_hash:
                        to_process.append(file_path)
        return to_process
    
    def process_file(self, file_path: Path) -> bool:
        logger.info(f"Procesando: {file_path.name}")
        
        try:
            # 1. Leer contenido
            with open(file_path, "r", encoding="utf-8") as f:
                raw_content = f.read()
            
            if not raw_content.strip():
                logger.warning(f"Archivo vacío: {file_path.name}")
                return False
            
            # 2. Ejecutar Phase1Graph
            logger.info("  Ejecutando Phase1Graph...")
            result = run_phase1(file_path, raw_content)
            
            # 3. Extraer bundle del resultado
            bundle_dict = result.get("bundle")
            if not bundle_dict:
                logger.error("  No se generó bundle")
                return False
            
            # 4. Convertir a Phase1Bundle y guardar
            # NOTA: bundle_dict tiene source_metadata como dict, pero al crear Phase1Bundle
            # Pydantic lo convierte a objeto SourceMetadata.
            bundle = Phase1Bundle(
                schema_version="1.0.0",
                bundle_id=bundle_dict["bundle_id"],
                source_metadata=bundle_dict["source_metadata"],
                raw_content_preview=bundle_dict["raw_content_preview"],
                topics=bundle_dict["topics"],
                ordered_outline=bundle_dict["ordered_outline"],
                semantic_chunks=bundle_dict["semantic_chunks"],
                ordered_class_markdown=bundle_dict["ordered_class_markdown"],
                warnings=bundle_dict.get("warnings", []),
            )
            
            # 5. Guardar bundle en staging
            bundle_path = self.bundle_store.save_phase1_bundle(bundle, status="pending")
            logger.info(f"  Bundle guardado: {bundle_path.name}")
            
            # 6. Guardar clase ordenada
            # --- FIX: Usar notación de punto para acceder al objeto Pydantic ---
            lesson_filename = f"{bundle.source_metadata.source_id}.md"
            lesson_path = self.lessons_ordered_path / lesson_filename
            with open(lesson_path, "w", encoding="utf-8") as f:
                f.write(bundle.ordered_class_markdown)
            logger.info(f"  Lección ordenada: {lesson_filename}")
            
            # 7. Guardar chunks como JSON
            # --- FIX: Usar notación de punto aquí también ---
            chunks_filename = f"{bundle.source_metadata.source_id}_chunks.json"
            chunks_path = self.lessons_chunks_path / chunks_filename
            
            # Convertir chunks (objetos Pydantic) a dicts para JSON
            chunks_data = [chunk.model_dump() for chunk in bundle.semantic_chunks]
            
            with open(chunks_path, "w", encoding="utf-8") as f:
                json.dump(chunks_data, f, indent=2, ensure_ascii=False)
            
            # 8. Actualizar registro
            self.processed_hashes[str(file_path)] = self._get_file_hash(file_path)
            self._save_state()
            
            # 9. Mover archivo original a processed
            processed_file = self.processed_path / file_path.name
            shutil.move(str(file_path), str(processed_file))
            logger.info(f"  Archivo movido a processed/")
            
            return True
            
        except Exception as e:
            logger.exception(f"Error procesando {file_path.name}: {e}")
            return False
    
    def run_once(self) -> int:
        files = self.scan_inbox()
        if not files:
            return 0
        
        logger.info(f"Encontrados {len(files)} archivo(s) para procesar")
        processed_count = 0
        for file_path in files:
            if self.process_file(file_path):
                processed_count += 1
        return processed_count
    
    def run_forever(self, interval: int = 30) -> None:
        logger.info(f"Iniciando watcher (intervalo: {interval}s)")
        logger.info(f"Vigilando: {self.inbox_path}")
        
        while True:
            try:
                count = self.run_once()
                if count > 0:
                    logger.info(f"Procesados {count} archivo(s)")
            except KeyboardInterrupt:
                logger.info("Detenido por usuario")
                break
            except Exception as e:
                logger.exception(f"Error en ciclo de escaneo: {e}")
            
            time.sleep(interval)


def main():
    parser = argparse.ArgumentParser(description="Watcher de Phase 1")
    parser.add_argument("--base-path", type=str, default="./data")
    parser.add_argument("--interval", type=int, default=30)
    parser.add_argument("--once", action="store_true")
    
    args = parser.parse_args()
    
    watcher = Phase1Watcher(args.base_path)
    
    if args.once:
        count = watcher.run_once()
        logger.info(f"Procesados {count} archivo(s)")
    else:
        watcher.run_forever(args.interval)

if __name__ == "__main__":
    main()