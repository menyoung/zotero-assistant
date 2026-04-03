"""Semantic search over Zotero library embeddings.

Usage:
    uv run search/search.py "crack deflection at multilayer interfaces"
    uv run search/search.py "Arrhenius activation energy SiC from MTS" --top 20
    uv run search/search.py "thermal conductivity anisotropy pyrolytic graphite" --verbose
"""

import argparse
import base64
import json
import os
import sqlite3
import sys
from pathlib import Path

import numpy as np
from dotenv import load_dotenv
from google import genai

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
ZOTERO_LIBRARY_ID = int(os.environ["ZOTERO_LIBRARY_ID"])

EMBEDDINGS_DIR = ROOT / "search" / "embeddings"
SQLITE_PATH = ROOT / "zotero-readonly.sqlite"

EMBEDDING_MODEL = "gemini-embedding-2-preview"
EMBEDDING_DIM = 3072

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def decode_vector_b64(b64_str):
    raw = base64.b64decode(b64_str)
    return np.frombuffer(raw, dtype=np.float16).astype(np.float32)


def load_index():
    """Load all embeddings into memory. Returns (keys, pages, texts, vectors)."""
    keys = []
    pages = []
    texts = []
    vecs = []

    for jsonl_path in sorted(EMBEDDINGS_DIR.glob("*.jsonl")):
        item_key = jsonl_path.stem
        with open(jsonl_path) as f:
            for line in f:
                rec = json.loads(line)
                keys.append(item_key)
                pages.append(rec["page"])
                texts.append(rec["text"])
                vecs.append(decode_vector_b64(rec["vec"]))

    if not vecs:
        return [], [], [], np.array([])

    return keys, pages, texts, np.stack(vecs)


def get_titles(conn, item_keys):
    """Look up titles for a set of item keys."""
    placeholders = ",".join("?" for _ in item_keys)
    cur = conn.execute(f"""
        SELECT i.key,
               (SELECT idv.value FROM itemData id
                JOIN itemDataValues idv ON id.valueID = idv.valueID
                JOIN fields f ON id.fieldID = f.fieldID
                WHERE id.itemID = i.itemID AND f.fieldName = 'title') AS title
        FROM items i
        WHERE i.key IN ({placeholders})
          AND i.libraryID = ?
    """, list(item_keys) + [ZOTERO_LIBRARY_ID])
    return dict(cur.fetchall())


def embed_query(client, query):
    """Embed a single query string."""
    response = client.models.embed_content(
        model=EMBEDDING_MODEL,
        contents=[query],
        config=genai.types.EmbedContentConfig(
            output_dimensionality=EMBEDDING_DIM,
        ),
    )
    return np.array(response.embeddings[0].values, dtype=np.float32)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Semantic search over Zotero library")
    parser.add_argument("query", help="Natural language search query")
    parser.add_argument("--top", type=int, default=10, help="Number of results (default: 10)")
    parser.add_argument("--verbose", action="store_true", help="Show text snippets")
    args = parser.parse_args()

    # Load index
    keys, pages, texts, matrix = load_index()
    if len(keys) == 0:
        print("No embeddings found. Run search/index.py first.")
        sys.exit(1)

    print(f"Loaded {len(keys)} page embeddings from {len(set(keys))} papers")

    # Embed query
    client = genai.Client(api_key=GEMINI_API_KEY)
    q_vec = embed_query(client, args.query)

    # Cosine similarity
    norms = np.linalg.norm(matrix, axis=1)
    q_norm = np.linalg.norm(q_vec)
    sims = (matrix @ q_vec) / (norms * q_norm + 1e-10)

    # Rank and deduplicate (best page per paper)
    best_per_paper = {}
    for idx in np.argsort(sims)[::-1]:
        key = keys[idx]
        if key not in best_per_paper:
            best_per_paper[key] = (idx, sims[idx])
        if len(best_per_paper) >= args.top:
            break

    # Look up titles
    try:
        conn = sqlite3.connect(SQLITE_PATH)
        titles = get_titles(conn, set(best_per_paper.keys()))
        conn.close()
    except Exception:
        titles = {}

    # Print results
    print(f"\nQuery: {args.query}\n")
    print(f"{'#':>3}  {'Score':>5}  {'Key':<10} {'Page':>4}  Title")
    print("-" * 80)

    for rank, (key, (idx, score)) in enumerate(best_per_paper.items(), 1):
        title = titles.get(key, "(unknown)")
        page = pages[idx] + 1  # 1-indexed for display
        print(f"{rank:>3}  {score:>5.3f}  {key:<10} p{page:>3}  {title}")
        if args.verbose:
            snippet = texts[idx][:200].replace("\n", " ")
            print(f"          {snippet}")
            print()


if __name__ == "__main__":
    main()
