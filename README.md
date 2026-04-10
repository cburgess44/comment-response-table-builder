# Public Comment Response Table Builder

AI-powered tool for parsing public comment records and generating structured response tables for land-use permit projects.

## What it does

1. **Upload** a compiled-comments PDF or paste a hearing-examiner exhibit index
2. **AI parses** the document — identifies commenters, dates, and key concerns
3. **Review & edit** the parsed rows in an interactive table
4. **Export** in your preferred format: Word (.docx), Excel (.xlsx), or CSV

## Features

- Claude AI backend for intelligent document parsing and summarization
- Multiple input modes: PDF upload or text paste
- Custom parsing instructions per project (e.g. "Exclude items from City of Tumwater")
- Editable review table — add, remove, or modify any row before export
- Configurable output: choose columns, orientation, fonts, colors
- Provenance block and scope notes for audit trail

## Quick start (local)

```bash
pip install -r requirements.txt
streamlit run app.py
```

Enter your Claude API key in the sidebar when the app opens.

## Deploy on Streamlit Cloud

1. Push this repo to GitHub
2. Go to [share.streamlit.io](https://share.streamlit.io)
3. Connect your repo and set `app.py` as the main file
4. Add your API key in **Settings → Secrets**:
   ```toml
   ANTHROPIC_API_KEY = "sk-ant-..."
   ```
5. Deploy — users get a shareable URL, no API key needed on their end
