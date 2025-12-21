"""
ui_app.py — Streamlit Gatekeeper UI

Interfaz de usuario para revisar y aprobar/rechazar propuestas
de ambas fases del pipeline.

BANDEJAS:
- Bandeja A (Phase 1): Revisión de clases ordenadas
- Bandeja B (Phase 2): Revisión de atomic notes

FUNCIONALIDADES:
- Ver temario y chunks propuestos
- Ver notas atómicas y enlaces
- Aprobar con comentarios opcionales
- Rechazar con directivas obligatorias
- Visualizar métricas de validación

CONEXIONES:
- Lee/Escribe: data/staging/ (vía BundleStore)
- Dispara: VaultWriter (para commits aprobados de Phase 2)

USO:
    streamlit run ui_app.py
    
    # O con ruta personalizada:
    streamlit run ui_app.py -- --base-path ./mi_proyecto/data
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

import streamlit as st

# Añadir src al path
sys.path.insert(0, str(Path(__file__).parent))

from core.state_schema import ApprovalStatus, Phase1Bundle, Phase2Bundle
from core.storage.bundles_fs import BundleStore
from core.storage.vault_io import VaultWriter


# =============================================================================
# CONFIGURACIÓN
# =============================================================================

# Obtener base_path de argumentos o usar default
BASE_PATH = Path("./data")

# Intentar leer de argumentos de Streamlit
if "--base-path" in sys.argv:
    idx = sys.argv.index("--base-path")
    if idx + 1 < len(sys.argv):
        BASE_PATH = Path(sys.argv[idx + 1])


# =============================================================================
# INICIALIZACIÓN
# =============================================================================

@st.cache_resource
def get_bundle_store() -> BundleStore:
    """Inicializa el BundleStore (singleton)."""
    return BundleStore(BASE_PATH)


@st.cache_resource
def get_vault_writer() -> VaultWriter:
    """Inicializa el VaultWriter (singleton)."""
    return VaultWriter(BASE_PATH)


# =============================================================================
# COMPONENTES UI
# =============================================================================

def render_header():
    """Renderiza el header de la aplicación."""
    st.title("🧠 ZK Foundry Gatekeeper")
    st.markdown("*Revisión y aprobación de conocimiento estructurado*")
    st.divider()


def render_sidebar():
    """Renderiza el sidebar con estadísticas."""
    store = get_bundle_store()
    
    with st.sidebar:
        st.header("📊 Estado del Sistema")
        
        # Contadores
        phase1_pending = len(store.list_phase1_pending())
        phase2_pending = len(store.list_phase2_pending())
        
        col1, col2 = st.columns(2)
        with col1:
            st.metric("Phase 1 Pending", phase1_pending)
        with col2:
            st.metric("Phase 2 Pending", phase2_pending)
        
        st.divider()
        
        # Vault stats
        vault = get_vault_writer()
        stats = vault.get_vault_stats()
        
        st.subheader("📁 Vault")
        st.write(f"- Notas: {stats['notes_count']}")
        st.write(f"- Literature: {stats['literature_count']}")
        st.write(f"- MOCs: {stats['mocs_count']}")
        
        st.divider()
        
        # Refresh button
        if st.button("🔄 Refrescar", use_container_width=True):
            st.cache_resource.clear()
            st.rerun()


def render_phase1_review(bundle: Phase1Bundle):
    """Renderiza la vista de revisión de Phase 1."""
    st.subheader(f"📄 {bundle.source_metadata.get('filename', 'Sin nombre')}")
    
    # Metadata
    with st.expander("ℹ️ Metadatos", expanded=False):
        col1, col2, col3 = st.columns(3)
        with col1:
            st.write(f"**Source ID:** `{bundle.source_metadata.get('source_id', 'N/A')}`")
        with col2:
            st.write(f"**Tamaño:** {bundle.source_metadata.get('file_size_bytes', 0):,} bytes")
        with col3:
            st.write(f"**Ingesta:** {bundle.created_at.strftime('%Y-%m-%d %H:%M')}")
    
    # Tabs para diferentes vistas
    tab1, tab2, tab3, tab4 = st.tabs(["📋 Temario", "📦 Chunks", "📝 Clase Ordenada", "⚠️ Warnings"])
    
    with tab1:
        st.markdown("### Temario Propuesto")
        for item in bundle.ordered_outline:
            st.markdown(f"**{item.get('position', '?')}. {item.get('topic_name', 'Sin nombre')}**")
            st.caption(item.get('rationale', ''))
            if item.get('subtopics'):
                for sub in item['subtopics']:
                    st.markdown(f"   - {sub}")
    
    with tab2:
        st.markdown("### Chunks Semánticos")
        for i, chunk in enumerate(bundle.semantic_chunks[:10]):  # Limitar a 10
            with st.expander(f"Chunk {i+1}: {chunk.get('anchor_text', '')[:50]}..."):
                st.write(f"**Tema:** {chunk.get('topic_id', 'N/A')}")
                st.write(f"**Palabras:** {chunk.get('word_count', 0)}")
                st.text(chunk.get('content', '')[:500])
        
        if len(bundle.semantic_chunks) > 10:
            st.info(f"... y {len(bundle.semantic_chunks) - 10} chunks más")
    
    with tab3:
        st.markdown("### Clase Ordenada")
        st.markdown(bundle.ordered_class_markdown)
    
    with tab4:
        if bundle.warnings:
            for warning in bundle.warnings:
                severity = warning.get('severity', 'medium')
                icon = {"low": "ℹ️", "medium": "⚠️", "high": "🚨"}.get(severity, "⚠️")
                st.warning(f"{icon} **{warning.get('type', 'Unknown')}**: {warning.get('description', '')}")
        else:
            st.success("✅ No se detectaron problemas")
    
    st.divider()
    
    # Acciones
    st.markdown("### ✅ Decisión")
    
    col1, col2 = st.columns(2)
    
    with col1:
        if st.button("✅ Aprobar", type="primary", use_container_width=True, key=f"approve_{bundle.bundle_id}"):
            directives = st.session_state.get(f"directives_{bundle.bundle_id}", "")
            store = get_bundle_store()
            store.approve_phase1(bundle.bundle_id, directives if directives else None)
            st.success("¡Bundle aprobado!")
            st.rerun()
    
    with col2:
        reject_directives = st.text_area(
            "Directivas de corrección (requerido para rechazo)",
            key=f"reject_dir_{bundle.bundle_id}",
            placeholder="Explica qué debe corregirse..."
        )
        
        if st.button("❌ Rechazar", type="secondary", use_container_width=True, key=f"reject_{bundle.bundle_id}"):
            if not reject_directives.strip():
                st.error("Debes proporcionar directivas para el rechazo")
            else:
                store = get_bundle_store()
                store.reject_phase1(bundle.bundle_id, reject_directives)
                st.warning("Bundle rechazado con directivas")
                st.rerun()


def render_phase2_review(bundle: Phase2Bundle):
    """Renderiza la vista de revisión de Phase 2."""
    st.subheader(f"🧩 Atomic Notes: {bundle.lesson_id}")
    
    # Métricas de validación
    report = bundle.validation_report
    total_score = report.get("total_score", 0)
    
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("Atomicidad", f"{report.get('atomicity_score', 0):.0f}")
    with col2:
        st.metric("Evidencia", f"{report.get('evidence_score', 0):.0f}")
    with col3:
        st.metric("Formato", f"{report.get('format_score', 0):.0f}")
    with col4:
        st.metric("Score Total", f"{total_score:.0f}", 
                  delta="Pasa" if total_score >= 85 else "No pasa")
    
    # Tabs
    tab1, tab2, tab3, tab4, tab5 = st.tabs([
        "📋 Plan", "📝 Notas", "🔗 Enlaces", "⚠️ Issues", "🔍 Contexto RAG"
    ])
    
    with tab1:
        st.markdown("### Plan de Atomización")
        st.caption(bundle.plan_rationale)
        
        for item in bundle.atomic_plan:
            novelty = item.get('novelty_score', 0) * 100
            st.markdown(f"**{item.get('proposed_title', 'Sin título')}**")
            st.progress(novelty / 100, text=f"Novedad: {novelty:.0f}%")
            st.caption(item.get('rationale', ''))
    
    with tab2:
        st.markdown("### Notas Propuestas")
        for note in bundle.atomic_proposals:
            with st.expander(f"📝 {note.get('title', 'Sin título')}", expanded=False):
                st.code(f"ID: {note.get('id', 'N/A')}")
                st.markdown(note.get('content', ''))
                
                # Frontmatter
                if note.get('frontmatter'):
                    st.json(note['frontmatter'])
    
    with tab3:
        st.markdown("### Enlaces Propuestos")
        if bundle.linking_matrix:
            for link in bundle.linking_matrix:
                st.markdown(
                    f"- `{link.get('source_note_id', '?')}` → "
                    f"`{link.get('target_note_id', '?')}` "
                    f"(*{link.get('link_type', 'relates')}*)"
                )
                st.caption(f"   {link.get('rationale', '')}")
        else:
            st.info("No se propusieron enlaces")
    
    with tab4:
        issues = report.get('issues', [])
        if issues:
            for issue in issues:
                severity = issue.get('severity', 'warning')
                icon = {"error": "🚨", "warning": "⚠️", "info": "ℹ️"}.get(severity, "⚠️")
                
                st.markdown(f"{icon} **{issue.get('issue_type', 'Unknown')}** en `{issue.get('note_id', 'N/A')}`")
                st.write(issue.get('description', ''))
                st.caption(f"💡 {issue.get('suggestion', '')}")
        else:
            st.success("✅ Sin issues de validación")
    
    with tab5:
        context = bundle.graph_rag_context
        st.markdown("### Contexto Recuperado")
        st.write(context.get('summary', 'No hay resumen disponible'))
        
        if context.get('similar_notes'):
            st.markdown("**Notas similares:**")
            for note_id in context['similar_notes']:
                st.write(f"- `{note_id}`")
    
    st.divider()
    
    # Acciones
    st.markdown("### ✅ Decisión")
    
    col1, col2 = st.columns(2)
    
    with col1:
        if st.button("✅ Aprobar y Commit", type="primary", use_container_width=True, key=f"approve2_{bundle.bundle_id}"):
            store = get_bundle_store()
            vault = get_vault_writer()
            
            # Aprobar
            approved = store.approve_phase2(bundle.bundle_id)
            
            if approved:
                # Commit al vault
                result = vault.commit_bundle(approved)
                
                if result["success"]:
                    st.success(f"¡Commit exitoso! {len(result['files_written'])} archivos escritos")
                else:
                    st.error(f"Error en commit: {result['error']}")
            else:
                st.error("Error aprobando bundle")
            
            st.rerun()
    
    with col2:
        return_to_phase1 = st.checkbox("Problema estructural (volver a Phase 1)", key=f"return_{bundle.bundle_id}")
        
        reject_directives = st.text_area(
            "Directivas de corrección",
            key=f"reject_dir2_{bundle.bundle_id}",
            placeholder="Explica qué debe corregirse..."
        )
        
        if st.button("❌ Rechazar", type="secondary", use_container_width=True, key=f"reject2_{bundle.bundle_id}"):
            if not reject_directives.strip():
                st.error("Debes proporcionar directivas")
            else:
                store = get_bundle_store()
                store.reject_phase2(
                    bundle.bundle_id, 
                    reject_directives,
                    return_to_phase1=return_to_phase1
                )
                st.warning("Bundle rechazado")
                st.rerun()


# =============================================================================
# PÁGINAS PRINCIPALES
# =============================================================================

def page_phase1():
    """Página de revisión de Phase 1."""
    st.header("📚 Bandeja A: Clases Ordenadas")
    
    store = get_bundle_store()
    pending = store.list_phase1_pending()
    
    if not pending:
        st.info("🎉 No hay bundles pendientes de revisión")
        return
    
    st.write(f"**{len(pending)}** bundle(s) pendiente(s)")
    
    # Selector de bundle
    bundle_options = {b.bundle_id: b for b in pending}
    selected_id = st.selectbox(
        "Seleccionar bundle:",
        options=list(bundle_options.keys()),
        format_func=lambda x: f"{x} ({bundle_options[x].source_metadata.get('filename', 'N/A')})"
    )
    
    if selected_id:
        render_phase1_review(bundle_options[selected_id])


def page_phase2():
    """Página de revisión de Phase 2."""
    st.header("🧩 Bandeja B: Atomic Notes")
    
    store = get_bundle_store()
    pending = store.list_phase2_pending()
    
    if not pending:
        st.info("🎉 No hay bundles pendientes de revisión")
        return
    
    st.write(f"**{len(pending)}** bundle(s) pendiente(s)")
    
    # Selector de bundle
    bundle_options = {b.bundle_id: b for b in pending}
    selected_id = st.selectbox(
        "Seleccionar bundle:",
        options=list(bundle_options.keys()),
        format_func=lambda x: f"{x} ({bundle_options[x].lesson_id})"
    )
    
    if selected_id:
        render_phase2_review(bundle_options[selected_id])


def page_vault():
    """Página de vista del vault."""
    st.header("📁 Vault Explorer")
    
    vault = get_vault_writer()
    
    # Estadísticas
    stats = vault.get_vault_stats()
    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("Notas Atómicas", stats['notes_count'])
    with col2:
        st.metric("Literature Notes", stats['literature_count'])
    with col3:
        st.metric("MOCs", stats['mocs_count'])
    
    st.divider()
    
    # Lista de notas
    notes = vault.list_notes()
    
    if notes:
        selected_note = st.selectbox("Ver nota:", options=notes)
        
        if selected_note:
            content = vault.read_note(selected_note)
            if content:
                st.markdown(content)
    else:
        st.info("El vault está vacío")


# =============================================================================
# MAIN
# =============================================================================

def main():
    st.set_page_config(
        page_title="ZK Foundry Gatekeeper",
        page_icon="🧠",
        layout="wide",
    )
    
    render_header()
    render_sidebar()
    
    # Navegación por tabs
    tab1, tab2, tab3 = st.tabs([
        "📚 Phase 1: Clases Ordenadas",
        "🧩 Phase 2: Atomic Notes",
        "📁 Vault Explorer"
    ])
    
    with tab1:
        page_phase1()
    
    with tab2:
        page_phase2()
    
    with tab3:
        page_vault()


if __name__ == "__main__":
    main()