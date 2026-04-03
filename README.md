# Zotero Research Assistant (Claude Code)

A Claude Code project for research and metadata maintenance on a Zotero library, with page-level semantic search powered by Gemini.

## Prerequisites

- [Claude Code](https://docs.anthropic.com/en/docs/claude-code) installed
- [Zotero](https://www.zotero.org/) with your target library synced locally
- `.env` file with configuration (see below)

## Setup

1. Copy `.env.example` to `.env`
2. Fill in your values:

**`ZOTERO_DATA_DIR`** — where Zotero stores its data (sqlite database, PDF storage). To find yours: Zotero > Settings > Advanced > Files and Folders > Data Directory Location.

**`ZOTERO_API_BASE`** — the API endpoint for your library:
  - Group library: `https://api.zotero.org/groups/<group_id>`
  - Personal library: `https://api.zotero.org/users/<user_id>`

**`ZOTERO_LIBRARY_ID`** — the SQLite `libraryID` for your target library. `1` = personal, `2` = first group library.

**`ZOTERO_API_KEY`** — create at https://www.zotero.org/settings/keys. Grant read/write access to the target library.

**`GEMINI_API_KEY`** — create at https://aistudio.google.com/apikey. Used for paper reading (Gemini Pro) and embeddings (Gemini Embedding 2).

**`PERPLEXITY_API_KEY`** — for literature search via Perplexity API.

3. Run `claude` in this directory.

## Semantic search

Page-level semantic search over your full PDF library. Gemini Pro reads each paper with a domain-expert prompt (`reference/reader-guide.md`), produces a one-pager summary and per-page embeddable texts, then Gemini Embedding 2 indexes them for cosine-similarity search.

```bash
# Build/update the index (incremental — skips already-indexed papers)
uv run search/index.py

# Index a specific paper
uv run search/index.py --reindex ITEMKEY

# Inspect what Pro wrote (for prompt iteration)
uv run search/index.py --reindex ITEMKEY --dev

# Search
uv run search/search.py "crack deflection at multilayer interfaces"
uv run search/search.py "Arrhenius activation energy SiC from MTS" --top 20 --verbose
```

### How it works

1. **Index:** For each paper, all pages are rendered as images and sent to Gemini Pro in a single API call, along with Zotero metadata and the reader guide. Pro returns a structured one-pager summary and per-page text reductions optimized for embedding.
2. **Embed:** Each page's text is embedded with Gemini Embedding 2 (3072 dimensions).
3. **Search:** Query is embedded, cosine similarity finds the best matching pages, results are ranked by paper with the best-matching page shown.
4. **Read:** Claude opens the actual PDFs for the top hits — the index finds the paper, Claude reads it.

### Prompt iteration

The reader guide (`reference/reader-guide.md`) teaches Pro how to read papers in your field. The `--dev` flag writes Pro's full output to `search/dev/` for inspection. The iteration cycle:

1. `uv run search/index.py --reindex KEY --dev`
2. Review `search/dev/KEY.md`
3. Update `reference/reader-guide.md` or the prompt in `search/index.py`
4. Re-run and compare

## Team context

Domain context (what the team is building, relevance criteria) lives in the Zotero library as **standalone notes tagged `team-context`**. Claude reads them at session start via SQLite.

## Files

| File | Purpose |
|---|---|
| `CLAUDE.md` | Project prompt |
| `.env` | Configuration and API keys (gitignored) |
| `zotero-readonly.sqlite` | Read-only snapshot of the Zotero database (refreshed each session, gitignored) |
| `search/index.py` | Build and update the semantic search index |
| `search/search.py` | Query the index from the command line |
| `search/embeddings/` | Per-page embedding vectors (gitignored) |
| `search/summaries/` | One-pager paper summaries (gitignored) |
| `search/dev/` | Full Pro output for prompt debugging (gitignored) |
| `reference/reader-guide.md` | Domain expertise prompt for Gemini Pro (not tracked — write your own for your field) |
| `reference/zotero-operations.md` | SQL patterns and API recipes for Zotero |
| `reference/perplexity-operations.md` | Perplexity API prompt design and usage |
