# Zotero Research & Metadata Assistant

Research assistant for a Zotero library. Two modes: conversational research and metadata maintenance. Domain context is loaded from `team-context` tagged items in the library at session start.

## Python

Always use `uv run` for Python commands. This project is managed by uv with a `.venv` and `pyproject.toml`. Never use bare `python3`.

## Setup — do this at session start

1. Read `.env` for all configuration (data directory, API credentials, library IDs)
2. Refresh the SQLite snapshot: `cp $ZOTERO_DATA_DIR/zotero.sqlite ./zotero-readonly.sqlite`
3. Read all `team-context` tagged items from the library — use them to guide research relevance, paper summarization, and Perplexity queries throughout the session. Find via SQL: `SELECT i.key, n.note FROM items i JOIN itemNotes n ON i.itemID = n.itemID JOIN itemTags it ON i.itemID = it.itemID JOIN tags t ON it.tagID = t.tagID WHERE t.name = 'team-context' AND i.libraryID = $ZOTERO_LIBRARY_ID`
4. Use that snapshot for all reads — finding papers by title, author, abstract, collection, tag
5. Use the Zotero Web API only for writes
6. **After API writes, re-copy the snapshot before the next read** — the local SQLite doesn't reflect API changes until Zotero syncs

**Fulltext index:** Zotero's built-in fulltext search covers only a fraction of PDFs. Don't rely on `fulltextWords` for comprehensive searches.

**PDFs:** Stored at `$ZOTERO_DATA_DIR/storage/<KEY>/<filename>.pdf`.

**PDF reading:** Default to the `Read` tool (visual/multimodal) — many papers are scanned older PDFs with charts, micrographs, and figures that text extraction would miss. Use Python text extraction (pdfplumber/PyMuPDF via `uv run`) only for obviously clean/born-digital PDFs, or when bulk-searching text across many files.

**Credentials:** All read from `.env`.
- Zotero Web API: base URL `$ZOTERO_API_BASE`, header `Zotero-API-Key: $ZOTERO_API_KEY`
- Perplexity API: `https://api.perplexity.ai/chat/completions`, key `$PERPLEXITY_API_KEY`
- Google AI (Gemini): key `$GEMINI_API_KEY`, use for bulk paper classification with Gemini Flash

## Search tool selection

Use the right tool for the right job:
- **Google Scholar** — for delivering a specific known paper to the user's browser. Use exact title in quotes, nothing else.
- **Perplexity `sonar-deep-research`** — for broad subject searches, literature landscape mapping, cross-field exploration. Expensive but thorough. Always `run_in_background`.
- **Perplexity `sonar-pro`** — for quick targeted queries, batch metadata lookup, author profiling. Fast but DOIs are frequently hallucinated (~80% wrong) — never open Perplexity DOI links without verifying on Crossref first.
- **Crossref API** — for verifying DOIs, getting abstracts, author searches by name. Free, no key needed. False matches common with generic titles — always check author names match.
- **OSTI API** — for DOE/national lab reports.

## Research workflow

Conversational loop — no stages, no handoffs. Tools: SQLite, PDF Read, web search, Perplexity (`sonar-deep-research` / `sonar-pro`), Crossref, and asking the user for paywalled/internal resources.

**Library coverage check:** Early in any research thread, assess how well the library covers the topic. If coverage is thin, lean on web search and Perplexity sooner.

**Perplexity execution:** Always use `run_in_background` so the conversation never blocks. Draft the query, workshop it with the user if needed, send it, and resume immediately.

**Perplexity model selection:**
- `sonar-deep-research`: broad exploration, cross-field searches, "find everything about X." Slow (minutes), expensive, thorough.
- `sonar-pro`: targeted queries, batch metadata lookup ("find correct metadata for these 10 items"), author profiling. Fast, cheaper, but hallucinate DOIs.

### Perplexity prompt design — concentric rings

Structure every query in three priority rings:

- **Ring 1 — "More like this" (~60%):** Name 1-3 specific papers from the library. Ask for follow-on work by same groups, citing papers, replication/contradiction studies, closely related work. Highest-value hits.
- **Ring 2 — "Same tech, different application" (~30%):** Adjacent applications of the same materials/processes in other industries. Same process, different journals.
- **Ring 3 — "Cross-disciplinary analogues" (~10%):** Only if rings 1-2 are exhausted or the user asks. Name specific mechanisms, not broad fields.

**Anti-patterns:**
- Don't say "go beyond these authors" without first saying "find more from their orbit"
- Don't list 4+ adjacent fields — leads to superficial touring
- Don't ask 5 sub-questions in one query — use `sonar-pro` follow-ups
- Don't over-exclude — long exclusion lists make Perplexity give up early. Anchor with specific citation trails instead
- Best prompts: 60% specific search tasks, 30% context, 10% exclusions

**Request template:**
```markdown
## Perplexity Research Request
**Question:** [1-2 sentences, specific and narrow]
**We already know (skip these):** [brief — just authors/years]
**Concrete search tasks (highest priority):**
- Find papers citing [Author Year, Journal Vol:Pages] that discuss [topic]
- Find post-[year] work by [group/institution] on [topic]
- Search [specific application literature] for [specific measurement/property]
**Adjacent applications (if needed):** [1-2 named communities]
**Stay in:** [positive framing of domain]
```

## Metadata maintenance

Use SQLite for all reads. Web API only for writes.

**Fix a specific item:**
1. Find the item in SQLite — note its `key`
2. GET the item via API to get its current `version` (required for optimistic locking)
3. Look up correct metadata — for batch fixes, use Perplexity `sonar-pro` with multiple broken titles in one query. For individual items, web search (OSTI.gov, DOI lookup, Google Scholar, publisher sites)
4. PATCH with corrected fields + `If-Unmodified-Since-Version: {version}` header
5. Verify the fix with another GET

**Scan for problems:**
1. Query SQLite for items with: missing title, missing authors, missing date, ALL CAPS titles, number-only titles, missing DOI on journal articles, missing publication title
2. **Always exclude trashed items:** add `AND i.itemID NOT IN (SELECT itemID FROM deletedItems)` to all scan queries. When using the API, skip items with `deleted: 1`.
3. Present as table, ask which to fix

**Proactive flagging:** If you encounter metadata problems during research (e.g., while looking up a paper for a research question), flag them immediately and propose a fix. Don't wait for an explicit metadata task.

**Adopt orphan PDFs:**
1. Read page 1 of the PDF (visual `Read` tool) to identify what it is
2. Create a parent item via POST to `/items` with correct itemType and metadata
3. PATCH the attachment to set `parentItem` to the new item's key

**Collection management:**
- Create: POST to `/collections` with `{"name": "...", "parentCollection": "PARENT_KEY"}`
- Rename: PATCH `/collections/KEY` with `{"name": "..."}`
- Add item to collection: PATCH item with `collections` array (merge with existing, don't replace)

**API notes:**
- PATCH returns 204 on success; only send changed fields
- `creators`: `[{"creatorType": "author", "firstName": "J.", "lastName": "Smith"}]`
- Single-name: `{"creatorType": "author", "name": "Organization Name"}`
- Always GET first to get the current `version`
- **Patents use `issueDate`/`filingDate`, not `date`** — PATCH with `date` on a patent returns 400
- Inventors on patents: `{"creatorType": "inventor", ...}` not `"author"`

## SQL patterns

All queries use `$ZOTERO_LIBRARY_ID` from `.env`.

**Find papers by keyword:**
```sql
SELECT i.key, title_v.value, substr(abs_v.value, 1, 200)
FROM items i
JOIN itemData td ON i.itemID = td.itemID AND td.fieldID = 1
JOIN itemDataValues title_v ON td.valueID = title_v.valueID
LEFT JOIN itemData ad ON i.itemID = ad.itemID AND ad.fieldID = 2
LEFT JOIN itemDataValues abs_v ON ad.valueID = abs_v.valueID
WHERE i.libraryID = $ZOTERO_LIBRARY_ID
AND (lower(title_v.value) LIKE '%keyword%'
  OR lower(COALESCE(abs_v.value,'')) LIKE '%keyword%')
```

**Get PDF path from item key:**
```sql
SELECT ia_item.key as storage_key, ia.path
FROM itemAttachments ia
JOIN items ia_item ON ia.itemID = ia_item.itemID
WHERE ia.parentItemID = (SELECT itemID FROM items WHERE key = 'ITEMKEY' AND libraryID = $ZOTERO_LIBRARY_ID)
AND ia.contentType = 'application/pdf'
```
PDF lives at: `$ZOTERO_DATA_DIR/storage/<storage_key>/<filename from path after "storage:">`

## Gemini Flash classification

For bulk paper classification (tags, summaries, collection assignment), use Google Gemini Flash via the API. Pattern:
1. Read PDF page 1-2 (visual or text extraction)
2. Send to Gemini with the summarization criteria from the `team-context` note (relevance assessment, extraction checklist)
3. Write tags to item via PATCH, create a note with the summary, add to appropriate collections

Tag the AI-generated notes with `ai-summary` so they're distinguishable from human notes.

## Report format

Not prescribed. Ask early what the user needs: decision memo, literature review, process spec, experimental plan, comparison table, etc. Shape the research around the deliverable, not the other way around.
