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
    generate_combined,
    generate_split,
    parse_input_file,
)

# ── Page config ──────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="LAD → ST Converter",
    page_icon="⚡",
    layout="centered",
)

# ── Header ───────────────────────────────────────────────────────────────────
st.title("⚡ Ladder Logic → Structured Text")
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
st.subheader("Options")

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

st.divider()

# ── Convert ──────────────────────────────────────────────────────────────────
if st.button("🔄 Convert", type="primary", use_container_width=True):

    # Filter to selected routines
    selected_set = set(selected_routines)
    routines_to_convert = [r for r in rll_routines if r.name in selected_set]

    stats = ConversionStats()

    with st.spinner("Converting..."):
        is_split = output_mode.startswith("Split")

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

                # Create zip in memory
                zip_buffer = io.BytesIO()
                with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
                    for p in paths:
                        zf.write(p, os.path.basename(p))
                zip_buffer.seek(0)
                zip_data = zip_buffer.getvalue()

                base_name = os.path.splitext(uploaded_file.name)[0]
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

            base_name = os.path.splitext(uploaded_file.name)[0]
            out_filename = f"{base_name}{output_format}"

    # ── Results ──────────────────────────────────────────────────────────
    st.success("Conversion complete!")

    # Stats
    st.subheader("Conversion Report")
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

    st.divider()

    # Download & preview
    if is_split:
        st.download_button(
            label=f"⬇️ Download {zip_filename}",
            data=zip_data,
            file_name=zip_filename,
            mime="application/zip",
            use_container_width=True,
        )
        st.caption(f"{len(paths)} file(s) in archive.")

    else:
        st.download_button(
            label=f"⬇️ Download {out_filename}",
            data=result_text,
            file_name=out_filename,
            mime="text/plain",
            use_container_width=True,
        )

        with st.expander("📄 Preview output", expanded=False):
            # Show first ~200 lines to keep UI responsive
            preview_lines = result_text.split("\n")
            if len(preview_lines) > 200:
                preview = "\n".join(preview_lines[:200])
                preview += f"\n\n... ({len(preview_lines) - 200} more lines)"
            else:
                preview = result_text
            st.code(preview, language="pascal")

# ── Cleanup ──────────────────────────────────────────────────────────────────
# temp file cleaned up when Streamlit reruns; but be explicit
try:
    os.unlink(tmp_path)
except OSError:
    pass

# ── Footer ───────────────────────────────────────────────────────────────────
st.divider()
st.caption("l5x_lad2st v2.0.0 — Ladder Logic (RLL) → Structured Text Converter")
