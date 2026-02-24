"""
app.py — Streamlit frontend for l5x_lad2st.py

Provides a browser-based UI for converting Allen-Bradley L5X/L5K
Ladder Logic (RLL) exports into IEC 61131-3 Structured Text.
"""

import io
import os
import tempfile
import zipfile

import streamlit as st

from l5x_lad2st import (
    ConversionStats,
    extract_context,
    generate_combined,
    generate_context_text,
    generate_split,
    parse_input_file,
)

# ── Page config ──────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="MWES — LAD → ST Converter",
    page_icon="⚙️",
    layout="centered",
)

# ── MWES Brand CSS ───────────────────────────────────────────────────────────
# Colors from MWES Brand Guidelines (Dec 2025):
#   Green  #3BB149  |  Navy #283549  |  Orange #D07A08  |  Gray #E9E9E9
# Fonts: Oswald (headers), Montserrat (body)
st.markdown("""
<style>
    /* ── Google Fonts ─────────────────────────────────────────────────── */
    @import url('https://fonts.googleapis.com/css2?family=Oswald:wght@400;500;600;700&family=Montserrat:wght@400;500;600&display=swap');

    /* ── Global font ─────────────────────────────────────────────────── */
    html, body, [class*="css"] {
        font-family: 'Montserrat', sans-serif;
        color: #283549;
    }

    /* ── Headers → Oswald ────────────────────────────────────────────── */
    h1, h2, h3, h4, h5, h6,
    .stMarkdown h1, .stMarkdown h2, .stMarkdown h3 {
        font-family: 'Oswald', sans-serif !important;
        text-transform: uppercase;
        color: #283549 !important;
    }

    /* ── Primary button (Convert) ────────────────────────────────────── */
    .stButton > button[kind="primary"],
    button[data-testid="stBaseButton-primary"] {
        background-color: #3BB149 !important;
        border-color: #3BB149 !important;
        color: white !important;
        font-family: 'Oswald', sans-serif !important;
        font-weight: 600;
        text-transform: uppercase;
        letter-spacing: 0.5px;
        transition: background-color 0.2s ease;
    }
    .stButton > button[kind="primary"]:hover,
    button[data-testid="stBaseButton-primary"]:hover {
        background-color: #2E8F3A !important;
        border-color: #2E8F3A !important;
    }

    /* ── Secondary / download buttons ────────────────────────────────── */
    .stDownloadButton > button,
    button[data-testid="stBaseButton-secondary"] {
        background-color: #283549 !important;
        border-color: #283549 !important;
        color: white !important;
        font-family: 'Oswald', sans-serif !important;
        font-weight: 500;
        letter-spacing: 0.3px;
        transition: background-color 0.2s ease;
    }
    .stDownloadButton > button:hover,
    button[data-testid="stBaseButton-secondary"]:hover {
        background-color: #1c2636 !important;
        border-color: #1c2636 !important;
    }

    /* ── File uploader ───────────────────────────────────────────────── */
    [data-testid="stFileUploader"] {
        border-color: #3BB149;
    }
    [data-testid="stFileUploader"] section {
        border-color: #3BB149 !important;
    }

    /* ── Metrics ─────────────────────────────────────────────────────── */
    [data-testid="stMetricValue"] {
        font-family: 'Oswald', sans-serif !important;
        color: #283549 !important;
    }
    [data-testid="stMetricLabel"] {
        font-family: 'Montserrat', sans-serif !important;
    }

    /* ── Expander headers ────────────────────────────────────────────── */
    .streamlit-expanderHeader {
        font-family: 'Oswald', sans-serif !important;
        color: #283549 !important;
    }

    /* ── Sidebar & radio/checkbox labels ─────────────────────────────── */
    .stRadio label, .stCheckbox label, .stMultiSelect label {
        font-family: 'Montserrat', sans-serif !important;
    }

    /* ── Success/info/warning/error boxes ────────────────────────────── */
    .stSuccess {
        border-left-color: #3BB149 !important;
    }

    /* ── Dividers ────────────────────────────────────────────────────── */
    hr {
        border-color: #E9E9E9 !important;
    }

    /* ── MWES branded header bar ─────────────────────────────────────── */
    .mwes-header {
        background-color: #283549;
        padding: 1.2rem 1.5rem;
        border-radius: 8px;
        margin-bottom: 1rem;
        display: flex;
        align-items: center;
        justify-content: space-between;
    }
    .mwes-header h1 {
        color: white !important;
        font-family: 'Oswald', sans-serif !important;
        font-size: 1.6rem !important;
        margin: 0 !important;
        padding: 0 !important;
        text-transform: uppercase;
    }
    .mwes-header .mwes-accent {
        color: #3BB149 !important;
    }
    .mwes-header .mwes-subtitle {
        color: #E9E9E9;
        font-family: 'Montserrat', sans-serif;
        font-size: 0.85rem;
        margin-top: 0.25rem;
    }

    /* ── Footer ──────────────────────────────────────────────────────── */
    .mwes-footer {
        text-align: center;
        padding: 1rem 0 0.5rem 0;
        color: #283549;
        font-family: 'Montserrat', sans-serif;
        font-size: 0.78rem;
        opacity: 0.7;
    }
    .mwes-footer a {
        color: #3BB149;
        text-decoration: none;
    }
</style>
""", unsafe_allow_html=True)

# ── Header ───────────────────────────────────────────────────────────────────
st.markdown("""
<div class="mwes-header">
    <div>
        <h1><span class="mwes-accent">LAD</span> → <span class="mwes-accent">ST</span> Converter</h1>
        <div class="mwes-subtitle">Allen-Bradley L5X / L5K Ladder Logic → IEC 61131-3 Structured Text</div>
    </div>
</div>
""", unsafe_allow_html=True)

st.markdown(
    "Upload an Allen-Bradley **L5X** or **L5K** export file to convert "
    "RLL (Relay Ladder Logic) routines into IEC 61131-3 Structured Text."
)

st.divider()

# ── File upload ──────────────────────────────────────────────────────────────
uploaded_file = st.file_uploader(
    "Upload L5X or L5K file",
    type=["l5x", "l5k"],
    help="Exported from Studio 5000 Logix Designer via File → Save As (.L5X) or Export (.L5K)",
)

if uploaded_file is None:
    st.info("👆 Upload a file to get started.")
    st.stop()

# ── Save upload to temp file ─────────────────────────────────────────────────
file_ext = os.path.splitext(uploaded_file.name)[1]
with tempfile.NamedTemporaryFile(delete=False, suffix=file_ext) as tmp:
    tmp.write(uploaded_file.getbuffer())
    tmp_path = tmp.name

# ── Parse ────────────────────────────────────────────────────────────────────
try:
    all_routines = parse_input_file(tmp_path)
except Exception as e:
    st.error(f"**Failed to parse file:** {e}")
    os.unlink(tmp_path)
    st.stop()

rll_routines = [r for r in all_routines if r.rtype == "RLL"]
non_rll = [r for r in all_routines if r.rtype != "RLL"]

# ── File summary ─────────────────────────────────────────────────────────────
st.success(f"**{uploaded_file.name}** parsed successfully.")

col1, col2, col3 = st.columns(3)
col1.metric("RLL Routines", len(rll_routines))
col2.metric("Non-RLL (skipped)", len(non_rll))
col3.metric("Total Rungs", sum(len(r.rungs) for r in rll_routines))

if not rll_routines:
    st.warning("No RLL routines found in this file. Nothing to convert.")
    os.unlink(tmp_path)
    st.stop()

st.divider()

# ── Routine list & selection ─────────────────────────────────────────────────
with st.expander("📋 Routine list", expanded=False):
    header = f"{'Name':<35s} {'Type':<6s} {'Rungs':>6s}"
    lines = [header, "─" * 50]
    for r in all_routines:
        tag = "" if r.rtype == "RLL" else "  (skip)"
        lines.append(f"{r.name:<35s} {r.rtype:<6s} {len(r.rungs):>6d}{tag}")
    st.code("\n".join(lines), language=None)

routine_names = [r.name for r in rll_routines]
selected_routines = st.multiselect(
    "Select routines to convert",
    options=routine_names,
    default=routine_names,
    help="Leave all selected for a full conversion, or pick specific routines.",
)

if not selected_routines:
    st.warning("Select at least one routine.")
    st.stop()

# ── Options ──────────────────────────────────────────────────────────────────
st.markdown('<h3 style="color: #D07A08 !important; font-size: 1.2rem;">Options</h3>', unsafe_allow_html=True)

col_a, col_b = st.columns(2)

with col_a:
    output_mode = st.radio(
        "Output mode",
        ["Combined (.st)", "Split (one .st per routine)"],
        help="Combined = single file with all routines. Split = separate file for each routine (downloaded as .zip).",
    )

with col_b:
    output_format = st.radio(
        "File extension",
        [".st", ".txt"],
        help="Choose the output file extension. Content is identical either way.",
    )

strip_nop = st.checkbox("Strip NOP-only rungs", value=False,
                         help="Omit rungs that contain only NOP() from the output.")
simplify = st.checkbox("Simplify always-true patterns", value=False,
                        help="Optimize patterns like EQU(X,X) → TRUE and clean up trivial conditions.")
generate_ctx = st.checkbox("Generate context file", value=True,
                            help="Produce a companion _context.txt with UDTs, tag definitions, AOI signatures, and I/O modules.")

st.divider()

# ── Convert ──────────────────────────────────────────────────────────────────
if st.button("CONVERT", type="primary", use_container_width=True):

    # Filter to selected routines
    selected_set = set(selected_routines)
    routines_to_convert = [r for r in rll_routines if r.name in selected_set]

    stats = ConversionStats()

    with st.spinner("Converting..."):
        is_split = output_mode.startswith("Split")
        base_name = os.path.splitext(uploaded_file.name)[0]

        # ── Extract context if requested ─────────────────────────────────
        context_text = None
        if generate_ctx:
            try:
                ctx = extract_context(tmp_path)
                if not ctx.is_empty():
                    context_text = generate_context_text(ctx)
            except Exception as e:
                st.warning(f"Context extraction encountered an issue: {e}")

        if is_split:
            # Split mode → generate into temp dir, zip, offer download
            with tempfile.TemporaryDirectory() as tmpdir:
                paths = generate_split(
                    routines_to_convert,
                    uploaded_file.name,
                    tmpdir,
                    stats,
                    strip_nop=strip_nop,
                    simplify=simplify,
                )

                # Rename extensions if user chose .txt
                if output_format == ".txt":
                    new_paths = []
                    for p in paths:
                        new_p = os.path.splitext(p)[0] + ".txt"
                        os.rename(p, new_p)
                        new_paths.append(new_p)
                    paths = new_paths

                # Add context file into the zip if generated
                ctx_tmp_path = None
                if context_text:
                    ctx_tmp_path = os.path.join(tmpdir, f"{base_name}_context.txt")
                    with open(ctx_tmp_path, "w", encoding="utf-8") as f:
                        f.write(context_text)

                # Create zip in memory
                zip_buffer = io.BytesIO()
                with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
                    for p in paths:
                        zf.write(p, os.path.basename(p))
                    if ctx_tmp_path:
                        zf.write(ctx_tmp_path, os.path.basename(ctx_tmp_path))
                zip_buffer.seek(0)
                zip_data = zip_buffer.getvalue()

                zip_filename = f"{base_name}_ST.zip"

        else:
            # Combined mode → single string
            result_text = generate_combined(
                routines_to_convert,
                uploaded_file.name,
                stats,
                strip_nop=strip_nop,
                simplify=simplify,
            )

            out_filename = f"{base_name}{output_format}"

    # ── Results ──────────────────────────────────────────────────────────
    st.success("Conversion complete!")

    # Stats
    st.markdown('<h3 style="color: #D07A08 !important; font-size: 1.2rem;">Conversion Report</h3>', unsafe_allow_html=True)
    c1, c2, c3 = st.columns(3)
    c1.metric("Routines", stats.routines)
    c2.metric("Rungs Converted", stats.rungs_converted)
    c3.metric("NOP / Empty", stats.rungs_nop)

    if stats.parse_errors or stats.conversion_errors:
        err_col1, err_col2 = st.columns(2)
        if stats.parse_errors:
            err_col1.metric("⚠️ Parse Errors", stats.parse_errors)
        if stats.conversion_errors:
            err_col2.metric("⚠️ Conversion Errors", stats.conversion_errors)

    if stats.review_items:
        with st.expander(f"🔍 Review Items ({len(stats.review_items)})", expanded=True):
            for item in stats.review_items:
                st.markdown(f"- `{item}`")

    # Context stats
    if context_text:
        ctx_counts = ctx.summary_counts()
        non_zero = {k: v for k, v in ctx_counts.items() if v > 0}
        if non_zero:
            with st.expander(f"📦 Context Summary", expanded=False):
                for k, v in non_zero.items():
                    st.markdown(f"- **{k}:** {v}")

    st.divider()

    # Download & preview — ST output
    if is_split:
        st.download_button(
            label=f"⬇️ Download {zip_filename}",
            data=zip_data,
            file_name=zip_filename,
            mime="application/zip",
            use_container_width=True,
        )
        file_count = len(paths) + (1 if context_text else 0)
        st.caption(f"{file_count} file(s) in archive" + (" (includes context file)" if context_text else "") + ".")

    else:
        st.download_button(
            label=f"⬇️ Download {out_filename}",
            data=result_text,
            file_name=out_filename,
            mime="text/plain",
            use_container_width=True,
        )

        with st.expander("📄 Preview ST output", expanded=False):
            preview_lines = result_text.split("\n")
            if len(preview_lines) > 200:
                preview = "\n".join(preview_lines[:200])
                preview += f"\n\n... ({len(preview_lines) - 200} more lines)"
            else:
                preview = result_text
            st.code(preview, language="pascal")

    # Download — Context file (separate button for combined mode, included in zip for split)
    if context_text and not is_split:
        ctx_filename = f"{base_name}_context.txt"
        st.download_button(
            label=f"📦 Download {ctx_filename}",
            data=context_text,
            file_name=ctx_filename,
            mime="text/plain",
            use_container_width=True,
        )

        with st.expander("📦 Preview context file", expanded=False):
            ctx_preview_lines = context_text.split("\n")
            if len(ctx_preview_lines) > 150:
                ctx_preview = "\n".join(ctx_preview_lines[:150])
                ctx_preview += f"\n\n... ({len(ctx_preview_lines) - 150} more lines)"
            else:
                ctx_preview = context_text
            st.code(ctx_preview, language=None)

# ── Cleanup ──────────────────────────────────────────────────────────────────
# temp file cleaned up when Streamlit reruns; but be explicit
try:
    os.unlink(tmp_path)
except OSError:
    pass

# ── Footer ───────────────────────────────────────────────────────────────────
st.divider()
st.markdown("""
<div class="mwes-footer">
    <strong>MWES</strong> — Midwest Engineered Systems &nbsp;|&nbsp;
    l5x_lad2st v2.0.0 &nbsp;|&nbsp;
    <a href="https://www.mwes.com" target="_blank">mwes.com</a> &nbsp;|&nbsp;
    866.880.MWES
</div>
""", unsafe_allow_html=True)
