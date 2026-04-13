"""Public Comment Response Table Builder — Streamlit app.

Upload a compiled-comments PDF, paste a hearing-examiner exhibit index,
or provide a web URL — let Claude parse and summarize the comments,
review and edit the rows, then export in your preferred format.
"""

import re
import streamlit as st
import pandas as pd
import requests

from ai_parser import parse_comments, extract_pdf_text, DEFAULT_MODEL
from exporters import (
    ExportConfig, ProjectInfo,
    export_docx, export_xlsx, export_csv, export_pdf,
)

# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="Comment Response Table Builder",
    page_icon="📋",
    layout="wide",
)

BUILT_IN_COLUMNS = [
    "No.", "Commenter", "Date", "Summary", "Applicant's Response",
    "Source Reference", "Comment Type", "Topics",
]

DEFAULT_COLUMNS = [
    "No.", "Commenter", "Date", "Summary", "Applicant's Response",
]

MODEL_OPTIONS = {
    "Claude Sonnet (fast, recommended)": "claude-sonnet-4-20250514",
    "Claude Opus (most capable)": "claude-opus-4-20250514",
    "Claude Haiku (fastest, cheapest)": "claude-haiku-4-20250514",
}


def _init_state():
    defaults = {
        "parsed_rows": None,
        "scope_notes": [],
        "parse_notes": "",
        "raw_row_count": 0,
        "merged_row_count": 0,
        "pdf_text": "",
        "url_projects": None,
        "url_full_text": "",
        "custom_columns": [],
        "custom_column_defs": {},
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v


_init_state()

# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------

with st.sidebar:
    st.header("Project Details")
    project_name = st.text_input("Project name", placeholder="e.g. 88th Place Preliminary Plat")
    file_number = st.text_input("File / permit number", placeholder="e.g. 2024104513")
    jurisdiction = st.text_input("Jurisdiction", placeholder="e.g. Thurston County")

    # ---- AI Settings (collapsed by default) ----
    with st.expander("AI Settings", expanded=False):
        secrets_key = ""
        try:
            secrets_key = st.secrets.get("ANTHROPIC_API_KEY", "")
        except Exception:
            pass
        if secrets_key:
            api_key = secrets_key
            st.success("API key loaded automatically.")
        else:
            api_key = st.text_input("Claude API key", type="password",
                                    help="Your Anthropic API key. Never shared or stored.")
        model_label = st.selectbox("AI model", list(MODEL_OPTIONS.keys()))
        model = MODEL_OPTIONS[model_label]

    st.divider()

    # ---- Export Settings ----
    st.header("Export Settings")
    export_format = st.selectbox(
        "Format",
        ["Word (.docx)", "Excel (.xlsx)", "CSV (.csv)", "PDF (.pdf)"],
    )
    orientation = st.radio("Page orientation", ["Landscape", "Portrait"], horizontal=True)

    # Column picker: built-in + any custom columns the user added
    all_available = BUILT_IN_COLUMNS + st.session_state.get("custom_columns", [])
    selected_cols = st.multiselect(
        "Columns to include",
        all_available,
        default=[c for c in DEFAULT_COLUMNS if c in all_available],
        help="Choose and reorder the columns for your export.",
    )

    include_provenance = st.checkbox(
        "Include source info header",
        value=True,
        help="Adds a block above the table with project name, source, "
             "date generated, and row counts — useful as an audit trail.",
    )
    include_scope_notes = st.checkbox("Include scope notes", value=True)

    st.divider()

    # ---- Custom Columns ----
    with st.expander("Custom Columns", expanded=False):
        st.caption(
            "Add your own columns beyond the built-in ones. "
            "Define what should go in each column and the AI will try to fill it."
        )
        new_col_name = st.text_input("New column name", placeholder="e.g. Priority Level")
        new_col_def = st.text_area(
            "What goes in this column?",
            placeholder='e.g. "Rate each comment High / Medium / Low based on '
                        'how directly it affects permit conditions."',
            height=80,
            key="new_col_def",
        )
        if st.button("Add column") and new_col_name:
            if new_col_name not in st.session_state["custom_columns"]:
                st.session_state["custom_columns"].append(new_col_name)
                st.session_state["custom_column_defs"][new_col_name] = new_col_def
                st.success(f"Added column: {new_col_name}")
                st.rerun()

        if st.session_state["custom_columns"]:
            st.write("**Custom columns defined:**")
            for cc in st.session_state["custom_columns"]:
                defn = st.session_state["custom_column_defs"].get(cc, "")
                st.write(f"- **{cc}**: {defn or '(no definition)'}")
            if st.button("Clear all custom columns"):
                st.session_state["custom_columns"] = []
                st.session_state["custom_column_defs"] = {}
                st.rerun()

    st.divider()

    # ---- Styling ----
    with st.expander("Styling", expanded=False):
        font_name = st.selectbox("Font", ["Calibri", "Arial", "Times New Roman", "Aptos"])
        font_size = st.slider("Font size (pt)", 8, 14, 10)
        header_color = st.color_picker("Header background", "#2563EB")

# Defaults if styling expander was never opened
if "font_name" not in dir():
    font_name = "Calibri"
if "font_size" not in dir():
    font_size = 10
if "header_color" not in dir():
    header_color = "#2563EB"

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
        [
            "Upload compiled-comments PDF",
            "Paste exhibit index or text",
            "Fetch from a website URL",
        ],
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

    # Append custom-column instructions so the AI knows to fill them
    extra_col_instructions = ""
    if st.session_state["custom_column_defs"]:
        parts = []
        for cname, cdef in st.session_state["custom_column_defs"].items():
            parts.append(f'- Column "{cname}": {cdef}')
        extra_col_instructions = (
            "\n\nADDITIONAL COLUMNS TO POPULATE:\n"
            "For each row, also include these extra fields in the JSON:\n"
            + "\n".join(parts)
            + "\nUse the column name (lowercase, spaces replaced with underscores) as the JSON key."
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

    elif mode == "Fetch from a website URL":
        url = st.text_input(
            "Website URL",
            placeholder="https://www.thurstoncountywa.gov/...",
        )
        if url and st.button("Fetch page"):
            with st.spinner("Fetching page content…"):
                try:
                    resp = requests.get(url, timeout=30, headers={
                        "User-Agent": "CommentResponseTableBuilder/1.0"
                    })
                    resp.raise_for_status()
                    from bs4 import BeautifulSoup
                    soup = BeautifulSoup(resp.text, "html.parser")
                    for tag in soup(["script", "style", "nav", "footer", "header"]):
                        tag.decompose()
                    page_text = soup.get_text(separator="\n", strip=True)

                    # Detect if the page contains multiple projects.
                    # Hearing examiner pages use lines like:
                    #   " 2024104513 88th Place Preliminary Plat - April 14, 2026 "
                    # They may have leading/trailing whitespace, em-dashes,
                    # and varying separators.
                    project_pattern = re.compile(
                        r"^\s*(\d{10})\s*[–—-]?\s*(.+?)$", re.MULTILINE
                    )
                    matches = project_pattern.findall(page_text)
                    matches = [
                        (n, name) for n, name in matches
                        if len(name.strip()) > 15
                    ]

                    if len(matches) > 1:
                        sections = {}
                        lines = page_text.split("\n")
                        current_key = None
                        current_lines = []
                        for line in lines:
                            m = project_pattern.match(line)
                            if m and len(m.group(2).strip()) > 15:
                                if current_key:
                                    sections[current_key] = "\n".join(current_lines)
                                proj_num = m.group(1)
                                proj_name = m.group(2).strip()
                                current_key = f"{proj_num} — {proj_name}"
                                current_lines = [line]
                            elif current_key:
                                current_lines.append(line)
                        if current_key:
                            sections[current_key] = "\n".join(current_lines)

                        st.session_state["url_projects"] = sections
                        st.session_state["url_full_text"] = page_text
                        st.success(
                            f"Found **{len(sections)} projects** on this page. "
                            f"Select one below."
                        )
                    else:
                        st.session_state["url_projects"] = None
                        st.session_state["pdf_text"] = page_text
                        st.success(
                            f"Fetched {len(page_text):,} characters from {url}"
                        )
                except Exception as e:
                    st.error(f"Failed to fetch URL: {e}")

        # Project selector (if multi-project page was fetched)
        if st.session_state.get("url_projects"):
            sections = st.session_state["url_projects"]
            selected_project = st.selectbox(
                "Select a project",
                list(sections.keys()),
                help="This page contains multiple projects. Pick the one you want to parse.",
            )
            if selected_project:
                project_text = sections[selected_project]
                st.session_state["pdf_text"] = project_text

                # Auto-fill project details from the selection
                parts = selected_project.split(" — ", 1)
                if len(parts) == 2 and not project_name:
                    # Only suggest, don't overwrite if user already typed something
                    st.caption(
                        f"Tip: project number **{parts[0]}**, "
                        f"name **{parts[1]}** — fill these in the sidebar."
                    )

                with st.expander(
                    f"Preview: {selected_project} ({len(project_text):,} chars)"
                ):
                    st.text(project_text[:5000])

        source_text = st.session_state.get("pdf_text", "")
        parse_mode = "exhibit_index"

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
            st.info("Set your Claude API key in AI Settings (sidebar) to enable parsing.")
        elif not source_text:
            st.info("Upload a PDF, paste text, or fetch a URL above.")

    if parse_clicked:
        full_instructions = custom_instructions + extra_col_instructions
        with st.spinner("Claude is reading and parsing the comments…"):
            try:
                result = parse_comments(
                    source_text,
                    mode=parse_mode,
                    custom_instructions=full_instructions,
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

    # Show a quick results preview right here on Tab 1 after parsing
    if st.session_state["parsed_rows"] is not None:
        rows = st.session_state["parsed_rows"]
        st.divider()
        st.subheader(f"Results: {len(rows)} rows parsed")
        if st.session_state["parse_notes"]:
            st.caption(st.session_state["parse_notes"])

        preview = pd.DataFrame([
            {
                "No.": i,
                "Commenter": r.get("commenter", ""),
                "Date": r.get("date", ""),
                "Summary": (r.get("summary", "")[:120] + "…"
                            if len(r.get("summary", "")) > 120
                            else r.get("summary", "")),
            }
            for i, r in enumerate(rows, 1)
        ])
        st.dataframe(preview, use_container_width=True, hide_index=True)
        st.info(
            "Go to the **2 — Review & Edit** tab to edit rows and summaries, "
            "then **3 — Export** to download."
        )

# ---- Tab 2: Review & Edit -----------------------------------------------

with tab_review:
    if st.session_state["parsed_rows"] is None:
        st.info("Parse comments in the Input tab first.")
    else:
        rows = st.session_state["parsed_rows"]

        if st.session_state["parse_notes"]:
            st.caption(f"AI notes: {st.session_state['parse_notes']}")

        df = pd.DataFrame(rows)
        base_display = ["commenter", "date", "summary", "source_ref",
                        "comment_type", "topics"]
        custom_keys = [
            c.lower().replace(" ", "_")
            for c in st.session_state.get("custom_columns", [])
        ]
        display_cols = [c for c in base_display + custom_keys if c in df.columns]

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
        for ck in custom_keys:
            col_config[ck] = st.column_config.TextColumn(
                ck.replace("_", " ").title(), width="medium"
            )

        edited_df = st.data_editor(
            df[display_cols] if display_cols else df,
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
            custom_column_keys={
                c: c.lower().replace(" ", "_")
                for c in st.session_state.get("custom_columns", [])
            },
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
        preview_cols = {"No.": range(1, len(rows) + 1)}
        key_map = {
            "Commenter": "commenter", "Date": "date", "Summary": "summary",
            "Source Reference": "source_ref", "Comment Type": "comment_type",
            "Topics": "topics", "Applicant's Response": "_blank",
        }
        for cc in st.session_state.get("custom_columns", []):
            key_map[cc] = cc.lower().replace(" ", "_")

        for col in cols_to_use:
            if col == "No.":
                continue
            k = key_map.get(col, col.lower().replace(" ", "_"))
            if k == "_blank":
                preview_cols[col] = [""] * len(rows)
            elif k in preview_df.columns:
                preview_cols[col] = preview_df[k].tolist()
            else:
                preview_cols[col] = [""] * len(rows)
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
        elif export_format == "PDF (.pdf)":
            buf = export_pdf(rows, info, config)
            st.download_button(
                "Download PDF (.pdf)",
                data=buf,
                file_name=f"{safe_name}_Tab_B.pdf",
                mime="application/pdf",
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
