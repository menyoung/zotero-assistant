# Zotero Operations Reference

Operational recipes for the Zotero group library. Read this file when doing library maintenance or metadata fixes.

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

**Fulltext index caveat:** Zotero's `fulltextWords` covers only a fraction of PDFs. Don't rely on it for comprehensive searches.

## API patterns

- PATCH returns 204 on success; only send changed fields
- Always GET first to get the current `version`
- `If-Unmodified-Since-Version: {version}` header required on writes
- `creators`: `[{"creatorType": "author", "firstName": "J.", "lastName": "Smith"}]`
- Single-name: `{"creatorType": "author", "name": "Organization Name"}`
- **Patents use `issueDate`/`filingDate`, not `date`** — PATCH with `date` on a patent returns 400
- Inventors on patents: `{"creatorType": "inventor", ...}` not `"author"`

## Batch operations with subagents

### Workflow

1. **Gather items.** Query SQLite for collection items + subcollection items. Get PDF paths.
2. **Pre-fetch metadata.** GET each item from the API (title, creators, date, abstract, reportNumber, DOI, itemType, version). Embed in the agent prompt so agents only need the Read tool.
3. **Partition strictly.** Split items across agents with no key overlap. ~8–12 items per agent.
4. **Launch Sonnet agents.** Agents read PDFs, compare against pre-fetched metadata, return structured proposals. They do NOT write to the API.
5. **Review and patch.** Parent reviews proposals and does all PATCH calls — single writer, no conflicts.

### Agent instructions — what works

- **Use Sonnet, not Haiku.** Haiku lacks chemistry knowledge for subscripts, formula spacing, case conventions. Sonnet is the floor for scientific text.
- **Broad mandate, not checklists.** Tell agents: "You are a materials scientist. If anything looks wrong — fix it. Use your judgment." Narrow OCR-pattern lists cause agents to check boxes instead of thinking.
- **PDFs have cover pages.** Don't say "read page 1." Say: "Scan through cover pages, blanks, disclaimers until you find the abstract or summary." Old reports often have 3–5 pages of front matter.
- **Synthesized abstracts are OK.** Many old reports have no labeled abstract. Agents should use the summary, introduction opening, or scope statement. Note the source in the proposal.
- **Return structured format.** Each item should come back as: KEY, ABSTRACT_STATUS (MISSING/NEEDS_FIX/CLEAN), PROPOSED_ABSTRACT, METADATA_ISSUES.

### What agents should check (beyond abstract)

Agents should verify all metadata against the actual PDF, not just the abstract. Common problems found in this library:

- **Truncated or incomplete author lists.** Zotero auto-import often captures only the first author. Check the PDF title page for the full list.
- **Mangled author names.** OCR artifacts in names (e.g., "J R G Gou" for "J. R. C. Gough"), missing first initials, wrong name order.
- **Wrong or missing report numbers.** Especially for national lab reports (ORNL, LASL, Dragon Project D.P. Reports).
- **Wrong dates.** Distribution date vs. writing date vs. conference date — use the most specific correct date from the PDF.
- **Wrong itemType.** Dissertations filed as journal articles, reports filed as books, etc.
- **PDF attached to wrong parent.** Verify the PDF content matches the item metadata. Flag if the PDF is a completely different document.
- **Missing DOI, journal name, volume, pages** for journal articles.
- **Title mismatches.** Truncated titles, OCR-garbled titles, titles with HTML artifacts.

### Practical notes

- **Unicode filenames break the Read tool.** Curly quotes, accented characters, etc. in PDF filenames cause ENOENT errors. Workaround: `cp /path/to/storage/KEY/*.pdf /tmp/cleanname.pdf` then read from `/tmp/`.
- **Include subcollections.** When working on a collection, always query for child collections and include their items.
- **Background agents may lack Bash permissions.** Pre-fetching metadata into the prompt avoids this entirely — agents only need the Read tool for PDFs.

## Metadata maintenance

**Fix a specific item:**
1. Find in SQLite — note its `key`
2. GET via API to get current `version`
3. Look up correct metadata (Crossref, OSTI, Google Scholar, Perplexity `sonar-pro` for batches)
4. PATCH with corrected fields + version header
5. Verify with another GET

**Scan for problems:**
Query SQLite for: missing title, missing authors, missing date, ALL CAPS titles, number-only titles, missing DOI on journal articles, missing publication title. Always exclude trashed items.

**Adopt orphan PDFs:**
1. Read page 1 (visual `Read` tool) to identify
2. POST to `/items` to create parent with correct metadata
3. PATCH attachment to set `parentItem`

**Collection management:**
- Create: POST `/collections` with `{"name": "...", "parentCollection": "PARENT_KEY"}`
- Rename: PATCH `/collections/KEY` with `{"name": "..."}`
- Add to collection: PATCH item's `collections` array (merge, don't replace)
