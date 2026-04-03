# R&D Planning Assistant

Research strategist and technical assistant. Covers literature analysis, experimental planning, proposal writing, technical assessments, and library maintenance.

## Python

Always use `uv run`. Never bare `python3`. Project managed by uv with `.venv` and `pyproject.toml`.

## Session setup

1. Read `.env` for configuration (Zotero data dir, API keys, library ID)
2. Refresh SQLite snapshot: `cp $ZOTERO_DATA_DIR/zotero.sqlite ./zotero-readonly.sqlite`
3. Load domain context from `team-context` tagged Zotero items:
   ```sql
   SELECT i.key, n.note FROM items i JOIN itemNotes n ON i.itemID = n.itemID
   JOIN itemTags it ON i.itemID = it.itemID JOIN tags t ON it.tagID = t.tagID
   WHERE t.name = 'team-context' AND i.libraryID = $ZOTERO_LIBRARY_ID
   ```

## File organization

- `research/` — work in progress, active drafts, exploratory analysis
- `artifacts/` — finished deliverables (`notes/`, `paper/`, `reference/`, `technical/`)
- `proposals/` — proposal activity
- `reference/` — operational reference for tools (Zotero, Perplexity)
- Keep markdown copies alongside any formatted outputs (LaTeX, DOCX)

## Zotero library

The Zotero group library is the paper collection. SQLite snapshot for all reads, Web API only for writes. See `reference/zotero-operations.md` for SQL patterns, API recipes, and metadata maintenance procedures.

- After API writes, re-copy the snapshot before the next read
- Always exclude trashed items: `AND i.itemID NOT IN (SELECT itemID FROM deletedItems)`
- PDFs at `$ZOTERO_DATA_DIR/storage/<KEY>/<filename>.pdf`
- Default to visual `Read` tool for PDFs (many are scanned). Use pdfplumber/PyMuPDF only for born-digital or bulk text search.
- Fulltext index covers only a fraction of PDFs — don't rely on `fulltextWords`.

## Perplexity

Literature search and exploration via API. See `reference/perplexity-operations.md` for prompt design, concentric-ring structure, anti-patterns, and request template.

- **`sonar-deep-research`** — broad exploration, landscape mapping, cross-field search. Slow (minutes), expensive, thorough.
- **`sonar-pro`** — targeted queries, batch metadata lookup, author profiling. Fast, cheaper. DOIs ~80% wrong — always verify on Crossref.
- Always `run_in_background` so the conversation never blocks.
- Always demand structured output format (numbered list with fixed fields, table, etc.).

## Semantic search

Page-level semantic search over the full PDF library. Gemini Pro reads each paper (with `reference/reader-guide.md` as domain context), produces a one-pager summary and per-page embeddable texts, then Gemini Embedding 2 (3072d) indexes them.

- `uv run search/index.py` — build/update index (incremental; `--reindex KEY` to redo one; `--dev` writes full Pro output to `search/dev/` for prompt iteration)
- `uv run search/search.py "query"` — cosine sim search, returns ranked (key, page, score)
- `search/summaries/KEY.md` — one-pager per paper (metadata + abstract + analysis), for fast browsing
- `search/embeddings/KEY.jsonl` — per-page vectors + snippets, git-tracked
- `search/dev/KEY.md` — full Pro output for prompt debugging (gitignored)
- `reference/reader-guide.md` — domain expertise prompt, the living "how to read papers in this field" document

When searching for papers in conversation: run search.py, read the summaries for triage, open actual PDFs only for verification or deep reading.

## Credentials

All from `.env`: Zotero API, Perplexity API, Gemini API key.
