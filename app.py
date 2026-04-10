"""Public Comment Response Table Builder — Streamlit app.

Upload a compiled-comments PDF or paste a hearing-examiner exhibit index,
let Claude parse and summarize the comments, review and edit the rows,
then export in your preferred format.
"""

import streamlit as st
import pandas as pd

from ai_parser import parse_comments, extract_pdf_text, DEFAULT_MODEL
from exporters import ExportConfig, ProjectInfo, export_docx, export_xlsx, export_csv

# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="Comment Response Table Builder",
    page_icon="📋",
    layout="wide",
)

ALL_COLUMNS = [
    "No.", "Commenter", "Date", "Summary", "Applicant's Response",
    "Source Reference", "Comment Type", "Topics",
]

DEFAULT_COLUMNS = [
    "No.", "Commenter", "Date", "Summary", "Applicant's Response",
]


def _init_state():
    defaults = {
        "parsed_rows": None,
        "scope_notes": [],
        "parse_notes": "",
        "raw_row_count": 0,
        "merged_row_count": 0,
        "pdf_text": "",
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v


_init_state()

# ---------------------------------------------------------------------------
# Sidebar — project info, AI settings, export settings
# ---------------------------------------------------------------------------

with st.sidebar:
    st.header("Project Details")
    project_name = st.text_input("Project name", placeholder="e.g. 88th Place Preliminary Plat")
    file_number = st.text_input("File / permit number", placeholder="e.g. 2024104513")
    jurisdiction = st.text_input("Jurisdiction", placeholder="e.g. Thurston County")

    st.divider()
    st.header("AI Settings")
    secrets_key = st.secrets.get("ANTHROPIC_API_KEY", "") if hasattr(st, "secrets") else ""
    if secrets_key:
        api_key = secrets_key
        st.success("API key loaded from app secrets.")
    else:
        api_key = st.text_input("Claude API key", type="password",
                                help="Your Anthropic API key. Never shared or stored.")
    model = st.text_input("Model", value=DEFAULT_MODEL,
                          help="Claude model to use for parsing.")

    st.divider()
    st.header("Export Settings")
    export_format = st.selectbox("Format", ["Word (.docx)", "Excel (.xlsx)", "CSV (.csv)"])
    orientation = st.radio("Page orientation", ["Landscape", "Portrait"], horizontal=True)
    selected_cols = st.multiselect(
        "Columns to include",
        ALL_COLUMNS,
        default=DEFAULT_COLUMNS,
        help="Choose and reorder the columns for your export.",
    )
    include_provenance = st.checkbox("Include provenance block", value=True)
    include_scope_notes = st.checkbox("Include scope notes", value=True)

    st.divider()
    st.header("Styling")
    font_name = st.selectbox("Font", ["Calibri", "Arial", "Times New Roman", "Aptos"])
    font_size = st.slider("Font size (pt)", 8, 14, 10)
    header_color = st.color_picker("Header background", "#2563EB")

# ---------------------------------------------------------------------------
# Main area — three tabs
# ---------------------------------------------------------------------------

st.title("Public Comment Response Table Builder")
st.caption("Upload comments, let AI parse them, review, and export.")

tab_input, tab_review, tab_export = st.tabs(["1 — Input", "2 — Review & Edit", "3 — Export"])

# ---- Tab 1: Input --------------------------------------------------------

with tab_input:
    mode = st.radio(
        "Input type",
        ["Upload compiled-comments PDF", "Paste exhibit index or text"],
        horizontal=True,
    )

    custom_instructions = st.text_area(
        "Custom parsing instructions (optional)",
        placeholder=(
            'e.g. "Exclude items from the City of Tumwater." '
            'or "Include memos as well as letters and emails." '
            'or "Only include public comments, not agency comments."'
        ),
        height=80,
    )

    if mode == "Upload compiled-comments PDF":
        uploaded = st.file_uploader("Upload PDF", type=["pdf"])
        if uploaded:
            with st.spinner("Extracting text from PDF…"):
                st.session_state["pdf_text"] = extract_pdf_text(uploaded)
            st.success(f"Extracted {len(st.session_state['pdf_text']):,} characters "
                       f"from {uploaded.name}")
            with st.expander("Preview extracted text (first 3,000 chars)"):
                st.text(st.session_state["pdf_text"][:3000])
        source_text = st.session_state.get("pdf_text", "")
        parse_mode = "pdf"
    else:
        source_text = st.text_area(
            "Paste exhibit index or comment text",
            height=350,
            placeholder="Paste the hearing examiner exhibit list or any text with comments…",
        )
        parse_mode = "exhibit_index"

    col_parse, col_status = st.columns([1, 3])
    with col_parse:
        parse_clicked = st.button("Parse with Claude", type="primary",
                                  disabled=not (source_text and api_key))
    with col_status:
        if not api_key:
            st.info("Enter your Claude API key in the sidebar to enable parsing.")
        elif not source_text:
            st.info("Upload a PDF or paste text above.")

    if parse_clicked:
        with st.spinner("Claude is reading and parsing the comments…"):
            try:
                result = parse_comments(
                    source_text,
                    mode=parse_mode,
                    custom_instructions=custom_instructions,
                    api_key=api_key,
                    model=model,
                )
                st.session_state["parsed_rows"] = result["rows"]
                st.session_state["scope_notes"] = result["scope_notes"]
                st.session_state["parse_notes"] = result["parse_notes"]
                st.session_state["raw_row_count"] = result["raw_row_count"]
                st.session_state["merged_row_count"] = result["merged_row_count"]
                st.success(
                    f"Parsed {result['raw_row_count']} raw → "
                    f"{result['merged_row_count']} merged rows."
                )
            except Exception as e:
                st.error(f"Parsing failed: {e}")

# ---- Tab 2: Review & Edit -----------------------------------------------

with tab_review:
    if st.session_state["parsed_rows"] is None:
        st.info("Parse comments in the Input tab first.")
    else:
        rows = st.session_state["parsed_rows"]

        if st.session_state["parse_notes"]:
            st.caption(f"AI notes: {st.session_state['parse_notes']}")

        df = pd.DataFrame(rows)
        display_cols = [c for c in ["commenter", "date", "summary", "source_ref",
                                     "comment_type", "topics"] if c in df.columns]
        col_config = {
            "commenter": st.column_config.TextColumn("Commenter", width="medium"),
            "date": st.column_config.TextColumn("Date", width="small"),
            "summary": st.column_config.TextColumn("Summary", width="large"),
            "source_ref": st.column_config.TextColumn("Source Ref", width="medium"),
            "comment_type": st.column_config.SelectboxColumn(
                "Type",
                options=["Public", "Agency", "Tribal", "Internal", "Consultant"],
                width="small",
            ),
            "topics": st.column_config.TextColumn("Topics", width="medium"),
        }

        edited_df = st.data_editor(
            df[display_cols],
            column_config=col_config,
            num_rows="dynamic",
            use_container_width=True,
            key="comment_editor",
        )
        st.session_state["parsed_rows"] = edited_df.to_dict("records")

        st.divider()
        st.subheader("Scope Notes")
        scope_text = st.text_area(
            "Edit scope notes (one per line)",
            value="\n".join(st.session_state.get("scope_notes", [])),
            height=200,
            key="scope_editor",
        )
        st.session_state["scope_notes"] = [
            line.strip() for line in scope_text.split("\n") if line.strip()
        ]

# ---- Tab 3: Export -------------------------------------------------------

with tab_export:
    if st.session_state["parsed_rows"] is None:
        st.info("Parse and review comments first.")
    else:
        rows = st.session_state["parsed_rows"]
        st.write(f"**{len(rows)} rows** ready for export.")

        cols_to_use = selected_cols if selected_cols else DEFAULT_COLUMNS

        config = ExportConfig(
            columns=cols_to_use,
            orientation=orientation.lower(),
            include_provenance=include_provenance,
            include_scope_notes=include_scope_notes,
            font_name=font_name,
            font_size_pt=font_size,
            header_bg_color=header_color.lstrip("#"),
            header_font_color="FFFFFF",
        )

        info = ProjectInfo(
            project_name=project_name,
            file_number=file_number,
            jurisdiction=jurisdiction,
            source_description=(
                f"AI-parsed ({st.session_state.get('parse_notes', '')})"
            ),
            scope_notes=st.session_state.get("scope_notes", []),
            parse_notes=st.session_state.get("parse_notes", ""),
            raw_row_count=st.session_state.get("raw_row_count", len(rows)),
            merged_row_count=len(rows),
        )

        safe_name = (project_name or "comments").replace(" ", "_")

        st.subheader("Preview")
        preview_df = pd.DataFrame(rows)
        preview_cols = {
            "No.": range(1, len(rows) + 1),
        }
        key_map = {"Commenter": "commenter", "Date": "date", "Summary": "summary",
                    "Source Reference": "source_ref", "Comment Type": "comment_type",
                    "Topics": "topics", "Applicant's Response": "_blank"}
        for col in cols_to_use:
            if col == "No.":
                continue
            k = key_map.get(col, "")
            if k == "_blank":
                preview_cols[col] = [""] * len(rows)
            elif k in preview_df.columns:
                preview_cols[col] = preview_df[k].tolist()
        st.dataframe(pd.DataFrame(preview_cols), use_container_width=True, hide_index=True)

        st.divider()

        if export_format == "Word (.docx)":
            buf = export_docx(rows, info, config)
            st.download_button(
                "Download Word (.docx)",
                data=buf,
                file_name=f"{safe_name}_Tab_B.docx",
                mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                type="primary",
            )
        elif export_format == "Excel (.xlsx)":
            buf = export_xlsx(rows, info, config)
            st.download_button(
                "Download Excel (.xlsx)",
                data=buf,
                file_name=f"{safe_name}_Tab_B.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                type="primary",
            )
        else:
            csv_str = export_csv(rows, info, config)
            st.download_button(
                "Download CSV (.csv)",
                data=csv_str,
                file_name=f"{safe_name}_Tab_B.csv",
                mime="text/csv",
                type="primary",
            )
