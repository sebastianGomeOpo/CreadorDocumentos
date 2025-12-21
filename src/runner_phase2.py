"""
runner_phase2.py — Runner de Fase 2

Este proceso detecta bundles aprobados de Phase 1 y ejecuta
el Phase2Graph para generar atomic notes.

COMPORTAMIENTO:
1. Escanea staging/phase1_approved/ buscando bundles
2. Para cada bundle aprobado:
   a. Carga la clase ordenada correspondiente
   b. Ejecuta Phase2Graph
   c. Guarda el bundle resultante en staging/phase2_pending/
3. Mantiene registro de qué bundles ya fueron procesados

CONEXIONES:
- Lee: data/staging/phase1_approved/
- Lee: data/lessons/ordered/
- Ejecuta: core/graphs/phase2_graph.py
- Escribe: data/staging/phase2_pending/

USO:
    python runner_phase2.py
    
    # O ejecutar una sola vez:
    python runner_phase2.py --once
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import datetime
from pathlib import Path

# Añadir src al path
sys.path.insert(0, str(Path(__file__).parent))

from core.graphs.phase2_graph import run_phase2
from core.state_schema import Phase2Bundle, generate_bundle_id
from core.storage.bundles_fs import BundleStore


# =============================================================================
# CONFIGURACIÓN
# =============================================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("runner_phase2")


class Phase2Runner:
    """
    Runner que procesa bundles de Phase 1 aprobados.
    """
    
    def __init__(self, base_path: Path | str):
        self.base_path = Path(base_path)
        
        # Directorios
        self.lessons_ordered_path = self.base_path / "lessons" / "ordered"
        
        # Storage
        self.bundle_store = BundleStore(self.base_path)
        
        # Registro de procesados
        self.state_file = self.base_path / "work" / "phase2" / "runner_state.json"
        self.processed_bundles: set[str] = set()
        
        # Crear directorios
        self._ensure_directories()
        self._load_state()
    
    def _ensure_directories(self) -> None:
        """Crea los directorios necesarios."""
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
    
    def _load_state(self) -> None:
        """Carga el estado persistido del runner."""
        if self.state_file.exists():
            try:
                with open(self.state_file, "r") as f:
                    data = json.load(f)
                    self.processed_bundles = set(data.get("processed_bundles", []))
            except Exception as e:
                logger.warning(f"Error cargando estado: {e}")
                self.processed_bundles = set()
    
    def _save_state(self) -> None:
        """Persiste el estado del runner."""
        with open(self.state_file, "w") as f:
            json.dump(
                {"processed_bundles": list(self.processed_bundles)}, 
                f, 
                indent=2
            )
    
    def find_pending_bundles(self) -> list[str]:
        """
        Encuentra bundles de Phase 1 aprobados que aún no se procesaron.
        
        Returns:
            Lista de bundle_ids pendientes
        """
        pending = []
        
        approved_bundles = self.bundle_store.list_phase1_approved()
        
        for bundle in approved_bundles:
            if bundle.bundle_id not in self.processed_bundles:
                pending.append(bundle.bundle_id)
        
        return pending
    
    def process_bundle(self, bundle_id: str) -> bool:
        """
        Procesa un bundle de Phase 1 aprobado.
        
        Args:
            bundle_id: ID del bundle a procesar
            
        Returns:
            True si se procesó exitosamente
        """
        logger.info(f"Procesando bundle: {bundle_id}")
        
        try:
            # 1. Cargar bundle de Phase 1
            phase1_bundle = self.bundle_store.load_phase1_bundle(bundle_id)
            if not phase1_bundle:
                logger.error(f"  Bundle no encontrado: {bundle_id}")
                return False
            
            # 2. Encontrar la clase ordenada
            source_id = phase1_bundle.source_metadata.get("source_id", "")
            lesson_filename = f"{source_id}.md"
            lesson_path = self.lessons_ordered_path / lesson_filename
            
            if not lesson_path.exists():
                logger.error(f"  Lección no encontrada: {lesson_filename}")
                return False
            
            # 3. Ejecutar Phase2Graph
            logger.info("  Ejecutando Phase2Graph...")
            
            # Obtener directivas humanas si las hay
            human_directives = None
            if phase1_bundle.human_directives:
                human_directives = phase1_bundle.human_directives
            
            result = run_phase2(
                lesson_id=source_id,
                ordered_class_path=lesson_path,
                phase1_bundle_id=bundle_id,
                human_directives=human_directives,
            )
            
            # 4. Extraer bundle del resultado
            bundle_dict = result.get("bundle")
            if not bundle_dict:
                logger.error("  No se generó bundle de Phase 2")
                return False
            
            # 5. Convertir a Phase2Bundle
            phase2_bundle = Phase2Bundle(
                schema_version="1.0.0",
                bundle_id=bundle_dict["bundle_id"],
                lesson_id=bundle_dict["lesson_id"],
                phase1_bundle_id=bundle_dict["phase1_bundle_id"],
                atomic_plan=bundle_dict["atomic_plan"],
                plan_rationale=bundle_dict["plan_rationale"],
                atomic_proposals=bundle_dict["atomic_proposals"],
                linking_matrix=bundle_dict["linking_matrix"],
                moc_updates=bundle_dict.get("moc_updates", []),
                validation_report=bundle_dict["validation_report"],
                graph_rag_context=bundle_dict["graph_rag_context"],
                iteration_count=bundle_dict.get("iteration_count", 0),
            )
            
            # 6. Guardar bundle en staging
            bundle_path = self.bundle_store.save_phase2_bundle(
                phase2_bundle, 
                status="pending"
            )
            logger.info(f"  Bundle Phase 2 guardado: {bundle_path.name}")
            
            # 7. Actualizar registro
            self.processed_bundles.add(bundle_id)
            self._save_state()
            
            # 8. Log de resumen
            report = phase2_bundle.validation_report
            total_score = report.get("total_score", 0)
            num_notes = len(phase2_bundle.atomic_proposals)
            num_links = len(phase2_bundle.linking_matrix)
            
            logger.info(f"  Generadas {num_notes} notas, {num_links} enlaces")
            logger.info(f"  Score de validación: {total_score:.1f}")
            
            return True
            
        except Exception as e:
            logger.exception(f"Error procesando bundle {bundle_id}: {e}")
            return False
    
    def run_once(self) -> int:
        """
        Ejecuta un ciclo de procesamiento.
        
        Returns:
            Número de bundles procesados
        """
        pending = self.find_pending_bundles()
        
        if not pending:
            return 0
        
        logger.info(f"Encontrados {len(pending)} bundle(s) pendientes")
        
        processed_count = 0
        for bundle_id in pending:
            if self.process_bundle(bundle_id):
                processed_count += 1
        
        return processed_count
    
    def run_forever(self, interval: int = 30) -> None:
        """
        Ejecuta el runner en loop infinito.
        
        Args:
            interval: Segundos entre escaneos
        """
        logger.info(f"Iniciando runner Phase 2 (intervalo: {interval}s)")
        
        while True:
            try:
                count = self.run_once()
                if count > 0:
                    logger.info(f"Procesados {count} bundle(s)")
            except KeyboardInterrupt:
                logger.info("Detenido por usuario")
                break
            except Exception as e:
                logger.exception(f"Error en ciclo de procesamiento: {e}")
            
            time.sleep(interval)


# =============================================================================
# CLI
# =============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Runner de Phase 2 - genera atomic notes desde bundles aprobados"
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
    
    runner = Phase2Runner(args.base_path)
    
    if args.once:
        count = runner.run_once()
        logger.info(f"Procesados {count} bundle(s)")
    else:
        runner.run_forever(args.interval)


if __name__ == "__main__":
    main()