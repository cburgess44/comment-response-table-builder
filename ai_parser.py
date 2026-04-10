"""AI-powered comment parsing using the Claude API.

Two modes:
  • pdf   — user uploads a compiled-comments PDF; Claude reads the full text,
            identifies each distinct submission, and returns structured rows.
  • exhibit_index — user pastes a hearing-examiner exhibit list; Claude
            identifies which items are letters/emails and builds assumed rows.
"""

import json
import re
from typing import Optional

from anthropic import Anthropic

DEFAULT_MODEL = "claude-sonnet-4-20250514"

# ---------------------------------------------------------------------------
# Prompt templates
# ---------------------------------------------------------------------------

_SYSTEM = (
    "You are an expert at reading public-comment records for land-use permits "
    "and extracting structured data. You always return valid JSON and nothing "
    "else outside the JSON block."
)

_PDF_PROMPT = """\
I have the full text of a compiled public-comments document for a land-use
permit project. Please parse it and return **one JSON object** (no markdown
fences) with exactly this schema:

{{
  "rows": [
    {{
      "commenter": "Full name or organization",
      "dates": ["MM/DD/YY", ...],
      "summary": "Concise 3-6 sentence summary of what they raised",
      "source_ref": "Optional page or exhibit reference",
      "comment_type": "Public | Agency | Tribal | Internal | Consultant",
      "topics": ["Traffic", "Environment", ...]
    }}
  ],
  "scope_notes": [
    "One bullet per category of material skipped and why"
  ],
  "parse_notes": "Brief free-text note on what you found"
}}

RULES:
1. Each distinct commenter gets ONE row. If the same person submitted
   multiple times, merge into one row with multiple dates and a combined
   summary.
2. Dates in MM/DD/YY format.
3. Summaries should be concise but substantive (3-6 sentences), capturing
   the key concerns, requests, and positions raised.
4. Classify each row's comment_type: Public (individuals/neighbors),
   Agency (government bodies), Tribal (tribal nations), Internal (county
   staff routing), Consultant (applicant's consultants).
5. Assign 1-5 topic tags from: Traffic, Environment, Wildlife, Stormwater,
   Density, Infrastructure, Schools, Safety, Noise, Property Values,
   Cultural Resources, Procedures, Land Use, Affordability, Trees,
   Wetlands, Air Quality, Hazardous Materials, Other.

{custom_instructions}

DOCUMENT TEXT:
{text}
"""

_EXHIBIT_PROMPT = """\
I have a hearing-examiner exhibit index for a land-use permit project.
Please identify every attachment described as a **letter** or **email** that
qualifies as a comment (per the instructions below) and return **one JSON
object** (no markdown fences) with exactly this schema:

{{
  "rows": [
    {{
      "commenter": "Full name or organization",
      "dates": ["MM/DD/YY", ...],
      "summary": "3-6 sentence summary inferred from the exhibit description and project context",
      "source_ref": "e.g. Staff Report att. u",
      "comment_type": "Public | Agency | Tribal | Internal | Consultant",
      "topics": ["Traffic", "Environment", ...]
    }}
  ],
  "scope_notes": [
    "One bullet per category of material excluded and why, listing specific attachment letters"
  ],
  "parse_notes": "Brief free-text note on what you found"
}}

RULES:
1. Only include items whose description says "Letter from …" or
   "Email from …" unless the custom instructions say otherwise.
2. Merge same commenter into one row with combined dates and summary.
3. Dates in MM/DD/YY format.
4. Summaries should be substantive (3-6 sentences), drawing on the
   exhibit description and reasonable inferences from the project type.
5. For scope_notes, group excluded items by reason (e.g. "City of Tumwater
   items excluded per rule: att. r, s, t") and list every skipped
   attachment with its letter and brief description.
6. Classify comment_type and assign topic tags (same lists as above).

{custom_instructions}

EXHIBIT INDEX TEXT:
{text}
"""


def _extract_json(raw: str) -> dict:
    """Pull the first JSON object from Claude's response, tolerating markdown fences."""
    cleaned = raw.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    return json.loads(cleaned)


def _merge_same_commenter(rows: list[dict]) -> list[dict]:
    """Safety merge in case the LLM returns duplicate commenter rows."""
    by_key: dict[str, dict] = {}
    order: list[str] = []
    for r in rows:
        key = r["commenter"].strip().lower()
        if key not in by_key:
            by_key[key] = {
                "commenter": r["commenter"],
                "dates": [],
                "summary_parts": [],
                "source_refs": [],
                "comment_type": r.get("comment_type", "Public"),
                "topics": [],
            }
            order.append(key)
        entry = by_key[key]
        for d in r.get("dates", []):
            if d not in entry["dates"]:
                entry["dates"].append(d)
        entry["summary_parts"].append(r.get("summary", ""))
        if r.get("source_ref"):
            entry["source_refs"].append(r["source_ref"])
        for t in r.get("topics", []):
            if t not in entry["topics"]:
                entry["topics"].append(t)

    merged = []
    for key in order:
        e = by_key[key]
        dates = e["dates"]
        if len(dates) > 1:
            date_str = ", ".join(dates[:-1]) + ", and " + dates[-1]
        elif dates:
            date_str = dates[0]
        else:
            date_str = ""

        summaries = [s for s in e["summary_parts"] if s]
        summary = " ".join(summaries) if len(summaries) <= 1 else " | ".join(summaries)

        merged.append({
            "commenter": e["commenter"],
            "date": date_str,
            "summary": summary,
            "source_ref": "; ".join(e["source_refs"]),
            "comment_type": e["comment_type"],
            "topics": ", ".join(e["topics"]),
        })
    return merged


def parse_comments(
    text: str,
    *,
    mode: str = "pdf",
    custom_instructions: str = "",
    api_key: str,
    model: str = DEFAULT_MODEL,
) -> dict:
    """Send text to Claude and return parsed comment data.

    Returns dict with keys: rows (list[dict]), scope_notes (list[str]),
    parse_notes (str), model (str), input_chars (int).
    """
    if mode == "exhibit_index":
        prompt = _EXHIBIT_PROMPT.format(
            text=text, custom_instructions=custom_instructions
        )
    else:
        prompt = _PDF_PROMPT.format(
            text=text, custom_instructions=custom_instructions
        )

    client = Anthropic(api_key=api_key)
    message = client.messages.create(
        model=model,
        max_tokens=8192,
        system=_SYSTEM,
        messages=[{"role": "user", "content": prompt}],
    )

    raw_response = message.content[0].text
    parsed = _extract_json(raw_response)

    raw_rows = parsed.get("rows", [])
    merged = _merge_same_commenter(raw_rows)

    return {
        "rows": merged,
        "scope_notes": parsed.get("scope_notes", []),
        "parse_notes": parsed.get("parse_notes", ""),
        "model": model,
        "input_chars": len(text),
        "raw_row_count": len(raw_rows),
        "merged_row_count": len(merged),
    }


def extract_pdf_text(pdf_file) -> str:
    """Extract text from an uploaded PDF file object."""
    from PyPDF2 import PdfReader

    reader = PdfReader(pdf_file)
    pages = []
    for page in reader.pages:
        t = page.extract_text() or ""
        if t.strip():
            pages.append(t)
    return "\n\n".join(pages)
