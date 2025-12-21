# ZK Foundry Static - Makefile
# ================================

.PHONY: help install dev lint format test run-ui run-watcher run-runner clean

# Variables
PYTHON := python3
PIP := pip
SRC := src
DATA := data

# Colores para output
BLUE := \033[34m
GREEN := \033[32m
YELLOW := \033[33m
RESET := \033[0m

help: ## Muestra esta ayuda
	@echo "$(BLUE)ZK Foundry Static$(RESET) - Comandos disponibles:"
	@echo ""
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "  $(GREEN)%-15s$(RESET) %s\n", $$1, $$2}'

# ================================
# INSTALACIÓN
# ================================

install: ## Instala dependencias de producción
	$(PIP) install -e .

dev: ## Instala dependencias de desarrollo
	$(PIP) install -e ".[dev]"
	pre-commit install || true

# ================================
# CALIDAD DE CÓDIGO
# ================================

lint: ## Ejecuta linter (ruff)
	ruff check $(SRC)
	mypy $(SRC) --ignore-missing-imports || true

format: ## Formatea código (ruff)
	ruff format $(SRC)
	ruff check --fix $(SRC)

# ================================
# TESTS
# ================================

test: ## Ejecuta tests
	pytest tests/ -v

test-cov: ## Ejecuta tests con cobertura
	pytest tests/ -v --cov=$(SRC) --cov-report=html
	@echo "$(GREEN)Reporte en htmlcov/index.html$(RESET)"

# ================================
# EJECUCIÓN
# ================================

run-ui: ## Inicia la UI de Streamlit (Gatekeeper)
	cd $(SRC) && streamlit run ui_app.py -- --base-path ../$(DATA)

run-watcher: ## Inicia el watcher de Phase 1
	cd $(SRC) && $(PYTHON) watcher_phase1.py --base-path ../$(DATA)

run-runner: ## Inicia el runner de Phase 2
	cd $(SRC) && $(PYTHON) runner_phase2.py --base-path ../$(DATA)

run-watcher-once: ## Ejecuta watcher una sola vez
	cd $(SRC) && $(PYTHON) watcher_phase1.py --base-path ../$(DATA) --once

run-runner-once: ## Ejecuta runner una sola vez
	cd $(SRC) && $(PYTHON) runner_phase2.py --base-path ../$(DATA) --once

# ================================
# DATOS
# ================================

init-data: ## Crea estructura de directorios de datos
	@echo "$(YELLOW)Creando estructura de datos...$(RESET)"
	mkdir -p $(DATA)/inbox/raw_classes
	mkdir -p $(DATA)/inbox/processed
	mkdir -p $(DATA)/work/phase1
	mkdir -p $(DATA)/work/phase2
	mkdir -p $(DATA)/lessons/ordered
	mkdir -p $(DATA)/lessons/chunks
	mkdir -p $(DATA)/staging/phase1_pending
	mkdir -p $(DATA)/staging/phase1_approved
	mkdir -p $(DATA)/staging/phase2_pending
	mkdir -p $(DATA)/staging/phase2_approved
	mkdir -p $(DATA)/staging/rejected
	mkdir -p $(DATA)/vault/notes
	mkdir -p $(DATA)/vault/literature
	mkdir -p $(DATA)/vault/mocs
	mkdir -p $(DATA)/index/vector_chunks
	mkdir -p $(DATA)/index/vector_notes
	mkdir -p $(DATA)/wal/completed
	mkdir -p $(DATA)/wal/failed
	@echo "$(GREEN)✓ Estructura creada$(RESET)"

clean-data: ## Limpia datos temporales (mantiene vault)
	rm -rf $(DATA)/work/*
	rm -rf $(DATA)/staging/phase1_pending/*
	rm -rf $(DATA)/staging/phase2_pending/*
	rm -rf $(DATA)/wal/temp/*
	@echo "$(GREEN)✓ Datos temporales limpiados$(RESET)"

reset-data: ## PELIGRO: Borra TODOS los datos incluyendo vault
	@echo "$(YELLOW)⚠️  Esto borrará TODOS los datos incluyendo el vault$(RESET)"
	@read -p "¿Estás seguro? [y/N] " confirm && [ "$$confirm" = "y" ]
	rm -rf $(DATA)/*
	$(MAKE) init-data
	@echo "$(GREEN)✓ Datos reiniciados$(RESET)"

# ================================
# DESARROLLO
# ================================

shell: ## Abre shell Python con contexto del proyecto
	cd $(SRC) && $(PYTHON) -i -c "from core.state_schema import *; from core.storage.bundles_fs import *; print('Contexto cargado')"

# ================================
# DEMO
# ================================

demo-ingest: ## Crea un archivo de prueba en inbox
	@echo "# Clase de Prueba\n\n## Introducción\n\nEste es un texto de prueba.\n\n## Conceptos Básicos\n\nExplicación de conceptos.\n\n## Conclusión\n\nResumen final." > $(DATA)/inbox/raw_classes/clase_demo.md
	@echo "$(GREEN)✓ Archivo de prueba creado en inbox$(RESET)"

# ================================
# LIMPIEZA
# ================================

clean: ## Limpia archivos generados
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".pytest_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".mypy_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".ruff_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name "htmlcov" -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true
	@echo "$(GREEN)✓ Limpieza completada$(RESET)"