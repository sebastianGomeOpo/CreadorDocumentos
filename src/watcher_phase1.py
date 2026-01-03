"""
watcher_phase1.py — Vigilante de Inbox para Fase 1 V2

Este proceso vigila la carpeta data/inbox/raw_classes/ y dispara
el Phase1Graph V2 cuando detecta archivos nuevos.

COMPORTAMIENTO:
1. Escanea la carpeta inbox cada N segundos
2. Detecta archivos .txt/.md nuevos o modificados
3. Ejecuta Phase1Graph V2 (arquitectura paralela)
4. Guarda el bundle resultante en staging/phase1_pending/
5. Mueve el archivo procesado a "processed"

USO:
    python watcher_phase1.py
    python watcher_phase1.py --once
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
from core.state_schema import Phase1Bundle
from core.storage.bundles_fs import BundleStore


# =============================================================================
# CONFIGURACIÓN
# =============================================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("watcher_phase1_v2")


class Phase1Watcher:
    """
    Vigilante que procesa archivos de texto en inbox.
    """
    
    def __init__(self, base_path: Path | str):
        self.base_path = Path(base_path)
        
        # Directorios
        self.inbox_path = self.base_path / "inbox" / "raw_classes"
        self.processed_path = self.base_path / "inbox" / "processed"
        self.drafts_path = self.base_path / "drafts"
        self.notes_path = self.base_path / "section_notes"
        
        # Storage
        self.bundle_store = BundleStore(self.base_path)
        
        # Registro de archivos procesados
        self.state_file = self.base_path / "work" / "phase1" / "watcher_state.json"
        self.processed_hashes: dict[str, str] = {}
        
        # Crear directorios
        self._ensure_directories()
        self._load_state()
    
    def _ensure_directories(self) -> None:
        """Crea los directorios necesarios."""
        for path in [
            self.inbox_path,
            self.processed_path,
            self.drafts_path,
            self.notes_path,
            self.state_file.parent,
        ]:
            path.mkdir(parents=True, exist_ok=True)
    
    def _load_state(self) -> None:
        """Carga el estado persistido del watcher."""
        if self.state_file.exists():
            try:
                with open(self.state_file, "r") as f:
                    data = json.load(f)
                    self.processed_hashes = data.get("processed_hashes", {})
            except Exception as e:
                logger.warning(f"Error cargando estado: {e}")
                self.processed_hashes = {}
    
    def _save_state(self) -> None:
        """Persiste el estado del watcher."""
        with open(self.state_file, "w") as f:
            json.dump({"processed_hashes": self.processed_hashes}, f, indent=2)
    
    def _get_file_hash(self, path: Path) -> str:
        """Calcula hash SHA256 de un archivo."""
        with open(path, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()
    
    def scan_inbox(self) -> list[Path]:
        """Escanea inbox por archivos nuevos o modificados."""
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
        """
        Procesa un archivo con Phase1Graph V2.
        
        Args:
            file_path: Ruta al archivo
            
        Returns:
            True si se procesó exitosamente
        """
        logger.info(f"[FILE] Procesando: {file_path.name}")
        
        try:
            # 1. Leer contenido
            with open(file_path, "r", encoding="utf-8") as f:
                raw_content = f.read()
            
            if not raw_content.strip():
                logger.warning(f"  [WARN] Archivo vacío: {file_path.name}")
                return False
            
            # 2. Ejecutar Phase1Graph V2
            logger.info("  [RUN] Ejecutando Phase1Graph V2...")
            start_time = time.time()
            
            result = run_phase1(file_path, raw_content)
            
            elapsed = time.time() - start_time
            logger.info(f"  [TIME] Completado en {elapsed:.2f}s")
            
            # 3. Extraer bundle del resultado
            bundle_dict = result.get("bundle")
            if not bundle_dict:
                logger.error("  [FAIL] No se generó bundle")
                return False
            
            # 4. Convertir a Phase1Bundle y guardar
            bundle = Phase1Bundle(
                schema_version="2.0.0",
                bundle_id=bundle_dict["bundle_id"],
                source_metadata=bundle_dict["source_metadata"],
                raw_content_preview=bundle_dict["raw_content_preview"],
                master_plan=bundle_dict.get("master_plan"),
                topics=bundle_dict.get("topics", []),
                ordered_outline=bundle_dict.get("ordered_outline", []),
                semantic_chunks=[],  # V2 no usa chunks en memoria
                ordered_class_markdown=bundle_dict.get("ordered_class_markdown", ""),
                draft_path=bundle_dict.get("draft_path", ""),
                section_notes_dir=bundle_dict.get("section_notes_dir", ""),
                chunk_files=bundle_dict.get("chunk_files", []),
                warnings=bundle_dict.get("warnings", []),
            )
            
            # 5. Guardar bundle en staging
            bundle_path = self.bundle_store.save_phase1_bundle(bundle, status="pending")
            logger.info(f"  [OK] Bundle guardado: {bundle_path.name}")
            
            # 6. Log de productos generados
            if bundle.draft_path:
                logger.info(f"  [DRAFT] Draft: {bundle.draft_path}")
            if bundle.section_notes_dir:
                logger.info(f"  [DIR] Notas: {bundle.section_notes_dir}")
            
            # 7. Estadísticas del plan
            if bundle.master_plan:
                plan = bundle.master_plan
                topic_count = len(plan.get("topics", []))
                risk_count = len(plan.get("detected_risks", []))
                logger.info(f"  [STATS] Plan: {topic_count} temas, {risk_count} riesgos detectados")
            
            # 8. Actualizar registro
            self.processed_hashes[str(file_path)] = self._get_file_hash(file_path)
            self._save_state()
            
            # 9. Mover archivo a processed
            processed_file = self.processed_path / file_path.name
            shutil.move(str(file_path), str(processed_file))
            logger.info(f"  [PKG] Archivo movido a processed/")
            
            return True
            
        except Exception as e:
            logger.exception(f"  [FAIL] Error procesando {file_path.name}: {e}")
            return False
    
    def run_once(self) -> int:
        """
        Ejecuta un ciclo de procesamiento.
        
        Returns:
            Número de archivos procesados
        """
        files = self.scan_inbox()
        
        if not files:
            return 0
        
        logger.info(f"[INBOX] Encontrados {len(files)} archivo(s) para procesar")
        
        processed_count = 0
        for file_path in files:
            if self.process_file(file_path):
                processed_count += 1
        
        return processed_count
    
    def run_forever(self, interval: int = 30) -> None:
        """
        Ejecuta el watcher en loop infinito.
        
        Args:
            interval: Segundos entre escaneos
        """
        logger.info(f"[START] Iniciando watcher V2 (intervalo: {interval}s)")
        logger.info(f"[WATCH] Vigilando: {self.inbox_path}")
        
        while True:
            try:
                count = self.run_once()
                if count > 0:
                    logger.info(f"[DONE] Procesados {count} archivo(s)")
            except KeyboardInterrupt:
                logger.info("[STOP] Detenido por usuario")
                break
            except Exception as e:
                logger.exception(f"Error en ciclo de escaneo: {e}")
            
            time.sleep(interval)


# =============================================================================
# CLI
# =============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Watcher de Phase 1 V2 - Arquitectura Paralela"
    )
    parser.add_argument(
        "--base-path",
        type=str,
        default="./data",
        help="Ruta base del proyecto (default: ./data)",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=30,
        help="Segundos entre escaneos (default: 30)",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Ejecutar una sola vez y salir",
    )
    
    args = parser.parse_args()
    
    watcher = Phase1Watcher(args.base_path)
    
    if args.once:
        count = watcher.run_once()
        logger.info(f"Procesados {count} archivo(s)")
    else:
        watcher.run_forever(args.interval)


if __name__ == "__main__":
    main()