"""Build semantic search index over Zotero PDF library.

For each paper: sends all pages as images to Gemini Pro with the reader guide
and Zotero metadata. Pro returns two things in one call:
  1. A one-pager summary (metadata + abstract + high-value supplement)
  2. Per-page embeddable text reductions optimized for semantic search

Output:
  summaries/<KEY>.md   — the one-pager, browsable by Claude
  embeddings/<KEY>.jsonl — per-page vectors for semantic search

Usage:
    uv run search/index.py                  # index all unindexed papers
    uv run search/index.py --reindex KEY    # force reindex a specific item
    uv run search/index.py --limit 5        # index at most 5 new papers
    uv run search/index.py --dry-run        # show what would be indexed
"""

import argparse
import base64
import json
import os
import re
import sqlite3
import sys
import time
from pathlib import Path

import fitz  # PyMuPDF
import numpy as np
from dotenv import load_dotenv
from google import genai

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

ZOTERO_DATA_DIR = Path(os.environ["ZOTERO_DATA_DIR"]).expanduser()
ZOTERO_LIBRARY_ID = int(os.environ["ZOTERO_LIBRARY_ID"])
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]

EMBEDDINGS_DIR = ROOT / "search" / "embeddings"
SUMMARIES_DIR = ROOT / "search" / "summaries"
READER_GUIDE = ROOT / "reference" / "reader-guide.md"
SQLITE_PATH = ROOT / "zotero-readonly.sqlite"

PRO_MODEL = "gemini-2.5-pro"
EMBEDDING_MODEL = "gemini-embedding-2-preview"
EMBEDDING_DIM = 3072
DPI = 300
MAX_PAGES_PER_CALL = 15  # split longer papers into batches

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def refresh_sqlite():
    """Copy fresh Zotero SQLite snapshot."""
    import shutil
    src = ZOTERO_DATA_DIR / "zotero.sqlite"
    shutil.copy2(src, SQLITE_PATH)


def get_pdf_items(conn):
    """Return list of (attachment_key, parent_key, title, path) for all PDFs."""
    cur = conn.execute("""
        SELECT i_att.key, i_par.key,
               (SELECT idv.value FROM itemData id
                JOIN itemDataValues idv ON id.valueID = idv.valueID
                JOIN fields f ON id.fieldID = f.fieldID
                WHERE id.itemID = ia.parentItemID AND f.fieldName = 'title') AS title,
               ia.path
        FROM itemAttachments ia
        JOIN items i_att ON ia.itemID = i_att.itemID
        LEFT JOIN items i_par ON ia.parentItemID = i_par.itemID
        WHERE i_att.libraryID = ?
          AND ia.contentType = 'application/pdf'
          AND ia.itemID NOT IN (SELECT itemID FROM deletedItems)
    """, (ZOTERO_LIBRARY_ID,))
    return cur.fetchall()


def get_item_metadata(conn, parent_key):
    """Fetch metadata for a parent item: returns dict of field->value."""
    cur = conn.execute("""
        SELECT f.fieldName, idv.value
        FROM itemData id
        JOIN itemDataValues idv ON id.valueID = idv.valueID
        JOIN fields f ON id.fieldID = f.fieldID
        JOIN items i ON id.itemID = i.itemID
        WHERE i.key = ? AND i.libraryID = ?
    """, (parent_key, ZOTERO_LIBRARY_ID))
    meta = dict(cur.fetchall())

    # Get creators
    cur2 = conn.execute("""
        SELECT c.firstName, c.lastName, ic.orderIndex
        FROM itemCreators ic
        JOIN creators c ON ic.creatorID = c.creatorID
        JOIN items i ON ic.itemID = i.itemID
        WHERE i.key = ? AND i.libraryID = ?
        ORDER BY ic.orderIndex
    """, (parent_key, ZOTERO_LIBRARY_ID))
    authors = [f"{row[0]} {row[1]}".strip() for row in cur2.fetchall()]
    meta["authors"] = authors
    return meta


def render_pages(pdf_path, dpi=DPI):
    """Render each page of a PDF as PNG bytes. Returns list of (page_num, png_bytes)."""
    doc = fitz.open(pdf_path)
    pages = []
    for i in range(doc.page_count):
        pix = doc[i].get_pixmap(dpi=dpi)
        pages.append((i, pix.tobytes("png")))
    doc.close()
    return pages


def encode_vector_b64(vec):
    """Encode a float32 numpy vector as base64 float16."""
    f16 = vec.astype(np.float16).tobytes()
    return base64.b64encode(f16).decode("ascii")


def format_metadata_header(meta, parent_key):
    """Format metadata into the header Pro sees and the summary file uses."""
    authors = ", ".join(meta.get("authors", [])) or "(unknown authors)"
    title = meta.get("title", "(untitled)")
    date = meta.get("date", "")
    pub = meta.get("publicationTitle", meta.get("proceedingsTitle", ""))
    doi = meta.get("DOI", "")
    abstract = meta.get("abstractNote", "")

    header = f"Title: {title}\n"
    header += f"Authors: {authors}\n"
    if date:
        header += f"Date: {date}\n"
    if pub:
        header += f"Publication: {pub}\n"
    if doi:
        header += f"DOI: {doi}\n"
    header += f"Zotero key: {parent_key}\n"

    return header, abstract


# ---------------------------------------------------------------------------
# Gemini calls
# ---------------------------------------------------------------------------

PAPER_PROMPT = """\
You are reading a research paper to produce two outputs. The paper's metadata and \
abstract (from the publisher) are provided below, followed by page images.

METADATA:
{metadata}

ABSTRACT:
{abstract}

This paper has {num_pages} pages (images follow). Produce exactly two sections:

=== ONE-PAGER ===
A structured summary for fast browsing by a research manager. Include:
- The metadata and abstract above (reproduced as-is). For the author line, include \
the well-known citation name if it differs from the first author (e.g. add \
"(LCTS Bordeaux group)" or "(ATLAS collaboration)"). Also include affiliation \
and funding source in brief (e.g. "LCTS, Univ. Bordeaux; CNRS funded" or \
"ORNL; DOE-funded"). These enable searching by group, lab, or program.
- Then ~500 tokens of YOUR additional analysis: key methods with actual parameters \
(temperature, pressure, gas composition, flow rates, precursor ratios like H2/MTS alpha), \
key quantitative results the abstract omits, what the important figures show, \
limitations or caveats, and how this work relates to the broader field.
Total one-pager should be under 800 tokens (including abstract).

=== EMBEDDABLES ===
For each page, produce an EXPANSIVE text for semantic search embedding. \
The embedding model accepts up to 8192 tokens per page — USE that budget. \
These texts exist purely for search indexing, so more words = more search surface. \
The reader searching this index is a CVD/CVI domain expert who searches by exact \
technical terminology, process conditions, specific values, and figure details.

CRITICAL RULES:
- PREFER QUOTING OVER PARAPHRASING. If a claim or description is well-represented \
by the author's own words, quote it directly rather than rephrasing. Your job is to \
make the paper findable, not to rewrite it. Paraphrasing loses the exact terminology \
a domain expert will search for. When in doubt, quote generously.
- TRANSCRIBE full figure captions verbatim. Every word on a figure label matters — \
the author put it there because it's important ("ball flowmeter", "shutoff valve", \
"mass flow controller" — all searchable terms).
- For process conditions: ALWAYS spell out T, P, total flow, precursor partial \
pressures, carrier gas ratios (e.g. alpha = H2/MTS), residence time, deposition \
rate — with numbers and units. These are primary search keys.
- For figures and plots: transcribe every readable axis label, legend entry, and \
annotation. Then report data ranges, visible features (peaks, plateaus, slopes, \
transitions), quantitative values, and the physical interpretation. \
READ THE FIGURE LIKE A SCIENTIST: report slopes/power-law exponents, note what \
dashed vs solid lines represent, describe error bars or confidence bands and how \
wide they are, describe whether scatter looks random or systematic, note outliers, \
and state whether the data convincingly supports the claimed trend or not.
- For tables: use your judgment. Simple tables can be reproduced in full. \
Complex tables with many columns, sub-headers, or merged cells are better \
described by their pattern and key values — a garbled markdown table is worse \
than a clear prose summary. Either way, always state what the results MEAN, \
not just what the column headers are.
- For schematics and apparatus diagrams: transcribe every labeled component. \
Describe the full structure from start to finish — left to right, top to bottom, \
inside to outside. Do not skip or summarize; if a schematic shows a layer stacking \
sequence, name every layer in order. If it shows a process flow, name every step. \
The schematic IS the author's model — transcribe it completely.
- For references pages: be brief. List cited works in author-year format with \
titles. Do NOT write interpretive paragraphs about why each was cited — that's \
your speculation, not paper content. Save the token budget for content pages.
- EXPAND NUMBERED CITATIONS inline. "[23]" is useless for search — write \
"Naslain et al. 1993" instead. Use the reference list to resolve numbers to \
author-year format wherever citations appear in the embeddable text.

Use the context of the full paper to interpret each page. Every page should have a \
thorough embeddable — do not skimp even on text-heavy pages.

Start every page's embeddable with a one-line header: citation name, year, journal, \
page number. Use the name people actually cite — a well-known group or collaboration \
name beats the alphabetically-first author (e.g. "ATLAS" not "Aad", "LCTS Bordeaux" \
not "Bertrand" if that's how the community refers to the work). \
Example: "Bertrand 1999, J. Am. Ceram. Soc., p. 2467"

Format:
[PAGE 1]
<embeddable text>
[PAGE 2]
<embeddable text>
..."""


def read_paper(client, page_images, reader_guide_text, metadata_header, abstract,
               title="", num_pages=None):
    """Send all page images to Pro. Returns (one_pager_text, {page_num: embeddable})."""

    prompt = PAPER_PROMPT.format(
        metadata=metadata_header,
        abstract=abstract or "(no abstract available)",
        num_pages=num_pages or len(page_images),
    )

    user_parts = [genai.types.Part(text=prompt)]

    for page_num, png_bytes in page_images:
        user_parts.append(genai.types.Part(
            text=f"--- Page {page_num + 1} ---"
        ))
        user_parts.append(genai.types.Part(
            inline_data=genai.types.Blob(mime_type="image/png", data=png_bytes)
        ))

    response = client.models.generate_content(
        model=PRO_MODEL,
        contents=[genai.types.Content(parts=user_parts)],
        config=genai.types.GenerateContentConfig(
            system_instruction=reader_guide_text,
            max_output_tokens=65536,
        ),
    )

    raw = response.text or ""
    one_pager, embeddables = parse_pro_output(raw)
    return one_pager, embeddables, raw


def read_paper_batched(client, page_images, reader_guide_text, metadata_header,
                       abstract, title=""):
    """For long papers: first batch gets one-pager + first pages, remaining batches
    get embeddables only with abstract as context."""
    first_batch = page_images[:MAX_PAGES_PER_CALL]
    one_pager, embeddables, raw = read_paper(
        client, first_batch, reader_guide_text, metadata_header, abstract,
        title=title, num_pages=len(page_images),
    )

    if len(page_images) <= MAX_PAGES_PER_CALL:
        return one_pager, embeddables, raw

    # Remaining batches: include first 2 pages as context
    raw_parts = [raw]
    context_pages = page_images[:2]
    for start in range(MAX_PAGES_PER_CALL, len(page_images), MAX_PAGES_PER_CALL):
        batch = page_images[start:start + MAX_PAGES_PER_CALL]
        batch_with_context = context_pages + batch

        _, batch_embeddables, batch_raw = read_paper(
            client, batch_with_context, reader_guide_text, metadata_header,
            abstract, title=title, num_pages=len(page_images),
        )
        raw_parts.append(batch_raw)

        for page_num in batch_embeddables:
            if page_num >= start:
                embeddables[page_num] = batch_embeddables[page_num]

    return one_pager, embeddables, "\n\n".join(raw_parts)


def parse_pro_output(text):
    """Parse Pro's output into (one_pager, {page_num: embeddable})."""
    one_pager = ""
    embeddables = {}

    # Split on the two section markers
    one_pager_match = re.search(
        r'===\s*ONE-PAGER\s*===\s*\n(.*?)(?====\s*EMBEDDABLES\s*===)',
        text, re.DOTALL
    )
    embeddables_match = re.search(
        r'===\s*EMBEDDABLES\s*===\s*\n(.*)',
        text, re.DOTALL
    )

    if one_pager_match:
        one_pager = one_pager_match.group(1).strip()

    if embeddables_match:
        emb_text = embeddables_match.group(1)
        parts = re.split(r'\[PAGE\s+(\d+)\]', emb_text)
        for i in range(1, len(parts) - 1, 2):
            page_num = int(parts[i]) - 1  # 0-indexed
            desc = parts[i + 1].strip()
            if desc:
                embeddables[page_num] = desc

    # Fallback: if markers weren't found, try to salvage
    if not one_pager and not embeddables and text.strip():
        # Try parsing as page-only output (no one-pager section)
        parts = re.split(r'\[PAGE\s+(\d+)\]', text)
        if len(parts) > 2:
            for i in range(1, len(parts) - 1, 2):
                page_num = int(parts[i]) - 1
                desc = parts[i + 1].strip()
                if desc:
                    embeddables[page_num] = desc
        else:
            one_pager = text.strip()

    return one_pager, embeddables


def embed_texts(client, texts):
    """Embed a list of texts with Gemini Embedding 2. Returns list of numpy arrays."""
    if not texts:
        return []

    results = []
    for i in range(0, len(texts), 100):
        batch = texts[i:i + 100]
        response = client.models.embed_content(
            model=EMBEDDING_MODEL,
            contents=batch,
            config=genai.types.EmbedContentConfig(
                output_dimensionality=EMBEDDING_DIM,
            ),
        )
        for emb in response.embeddings:
            results.append(np.array(emb.values, dtype=np.float32))
    return results


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

DEV_DIR = ROOT / "search" / "dev"


def index_paper(client, reader_guide_text, conn, attachment_key, parent_key,
                title, pdf_path, dev=False):
    """Index a single paper. Returns number of pages indexed."""
    key = parent_key or attachment_key
    emb_file = EMBEDDINGS_DIR / f"{key}.jsonl"
    sum_file = SUMMARIES_DIR / f"{key}.md"

    # Get metadata
    meta = get_item_metadata(conn, key) if parent_key else {}
    metadata_header, abstract = format_metadata_header(meta, key)

    # Render pages
    pages = render_pages(pdf_path)
    if not pages:
        print(f"  SKIP {key}: no pages in PDF")
        return 0

    # Read paper with Pro (one call for short papers, batched for long)
    t0 = time.time()
    if len(pages) <= MAX_PAGES_PER_CALL:
        one_pager, embeddables, raw = read_paper(
            client, pages, reader_guide_text, metadata_header, abstract,
            title=title,
        )
    else:
        one_pager, embeddables, raw = read_paper_batched(
            client, pages, reader_guide_text, metadata_header, abstract,
            title=title,
        )
    read_time = time.time() - t0

    # Dev mode: write full Pro output for inspection
    if dev:
        DEV_DIR.mkdir(exist_ok=True)
        dev_file = DEV_DIR / f"{key}.md"
        with open(dev_file, "w") as f:
            f.write(f"# {title}\n\n")
            f.write(f"**Key:** {key}  \n")
            f.write(f"**Pages:** {len(pages)}  \n")
            f.write(f"**Read time:** {read_time:.1f}s\n\n")
            f.write("---\n\n")
            f.write("## One-pager\n\n")
            f.write(one_pager + "\n\n")
            f.write("---\n\n")
            f.write("## Embeddables\n\n")
            for p in sorted(embeddables.keys()):
                f.write(f"### Page {p + 1}\n\n")
                f.write(embeddables[p] + "\n\n")
        print(f"  DEV → {dev_file}")

    # Write one-pager summary
    with open(sum_file, "w") as f:
        f.write(one_pager + "\n")

    # Embed all page texts
    page_nums = sorted(embeddables.keys())
    texts = [embeddables[p] for p in page_nums]

    t0 = time.time()
    vectors = embed_texts(client, texts)
    embed_time = time.time() - t0

    # Write embeddings JSONL
    with open(emb_file, "w") as f:
        for page_num, text, vec in zip(page_nums, texts, vectors):
            record = {
                "page": page_num,
                "text": text[:500],
                "vec": encode_vector_b64(vec),
            }
            f.write(json.dumps(record) + "\n")

    print(f"  OK {key}: {len(page_nums)} pages, read {read_time:.1f}s, embed {embed_time:.1f}s")
    return len(page_nums)


def main():
    parser = argparse.ArgumentParser(description="Index Zotero PDFs for semantic search")
    parser.add_argument("--reindex", metavar="KEY", help="Force reindex a specific item key")
    parser.add_argument("--limit", type=int, help="Max number of papers to index")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be indexed")
    parser.add_argument("--dev", action="store_true",
                        help="Write full Pro output to search/dev/<KEY>.md for inspection")
    args = parser.parse_args()

    # Setup
    EMBEDDINGS_DIR.mkdir(exist_ok=True)
    SUMMARIES_DIR.mkdir(exist_ok=True)
    refresh_sqlite()

    reader_guide_text = READER_GUIDE.read_text()
    client = genai.Client(api_key=GEMINI_API_KEY)

    conn = sqlite3.connect(SQLITE_PATH)
    items = get_pdf_items(conn)

    print(f"Found {len(items)} PDFs in library")

    # Determine what to index
    already_indexed = {p.stem for p in EMBEDDINGS_DIR.glob("*.jsonl")}
    to_index = []

    for att_key, par_key, title, path in items:
        key = par_key or att_key
        if args.reindex:
            if key != args.reindex and att_key != args.reindex:
                continue
        elif key in already_indexed:
            continue

        # Resolve PDF path
        if path and path.startswith("storage:"):
            filename = path[len("storage:"):]
            pdf_path = ZOTERO_DATA_DIR / "storage" / att_key / filename
        else:
            pdf_path = ZOTERO_DATA_DIR / "storage" / att_key
            pdfs = list(pdf_path.glob("*.pdf")) if pdf_path.is_dir() else []
            pdf_path = pdfs[0] if pdfs else None

        if pdf_path and pdf_path.exists():
            to_index.append((att_key, par_key, title or "(untitled)", pdf_path))

    print(f"To index: {len(to_index)} papers (already indexed: {len(already_indexed)})")

    if args.dry_run:
        for att_key, par_key, title, pdf_path in to_index[:20]:
            key = par_key or att_key
            print(f"  {key}: {title}")
        if len(to_index) > 20:
            print(f"  ... and {len(to_index) - 20} more")
        conn.close()
        return

    if args.limit:
        to_index = to_index[:args.limit]

    # Index
    total_pages = 0
    errors = 0
    for i, (att_key, par_key, title, pdf_path) in enumerate(to_index):
        key = par_key or att_key
        print(f"[{i+1}/{len(to_index)}] {key}: {title}")
        try:
            n = index_paper(client, reader_guide_text, conn, att_key, par_key,
                           title, pdf_path, dev=args.dev)
            total_pages += n
        except Exception as e:
            print(f"  ERROR {key}: {e}")
            errors += 1
        if i < len(to_index) - 1:
            time.sleep(2)

    conn.close()
    print(f"\nDone. Indexed {total_pages} pages from {len(to_index) - errors} papers. Errors: {errors}")


if __name__ == "__main__":
    main()
