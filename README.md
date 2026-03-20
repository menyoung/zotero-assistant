# Zotero Research Assistant (Claude Code)

A Claude Code project for research and metadata maintenance on a Zotero library. Domain context is loaded from `team-context` tagged items in the library at session start.

## Prerequisites

- [Claude Code](https://docs.anthropic.com/en/docs/claude-code) installed
- [Zotero](https://www.zotero.org/) with your target library synced locally
- `.env` file with configuration (see below)

## Setup

1. Copy `.env.example` to `.env`
2. Fill in your values:

**`ZOTERO_DATA_DIR`** — where Zotero stores its data (sqlite database, PDF storage). This is an arbitrary path set by each user. To find yours: Zotero > Settings > Advanced > Files and Folders > Data Directory Location. Defaults to `~/Zotero` on macOS/Linux.

**`ZOTERO_API_BASE`** — the API endpoint for your library:
  - Group library: `https://api.zotero.org/groups/<group_id>`
  - Personal library: `https://api.zotero.org/users/<user_id>`

**`ZOTERO_LIBRARY_ID`** — the SQLite `libraryID` for your target library. `1` = personal (My Library), `2` = first group library. Check with: `SELECT libraryID, type, GROUP_CONCAT(key) FROM libraries`.

**`ZOTERO_API_KEY`** — create at https://www.zotero.org/settings/keys. Grant read/write access to the target library.

3. Run `claude` in this directory. Claude loads `CLAUDE.md` as its project prompt, reads `.env`, copies a fresh SQLite snapshot, and reads `team-context` tagged items for domain context.

## Team context note

Domain context (what the team is building, process description, key people, relevance criteria) lives in the Zotero library as **standalone notes tagged `team-context`**. Claude reads them at session start via SQLite.

**To edit:** Find the note in Zotero (search for the `team-context` tag), click it, edit in place. Changes take effect next time anyone starts a Claude Code session.

**To rewrite from scratch:** Draft the new version in a Claude.ai conversation, then paste it into the Zotero note (or create a new standalone note, tag it `team-context`, and delete the old one). The tag is what Claude searches for, not the item key.

## What you can do

### Research
Ask a research question. Claude will search the library, read PDFs, and pull from the web. For deeper gaps, workshop a Perplexity deep research query together — Claude sends it in the background and keeps the conversation going.

### Metadata maintenance
Point Claude at items with bad metadata, or ask it to scan for problems.

### Reports
Ask for a deliverable — decision memo, literature review, process spec, experimental plan, comparison table. Tell Claude the format early so the research is shaped by what you need.

## Files

| File | Purpose |
|---|---|
| `CLAUDE.md` | Project prompt — workflows, SQL patterns, API patterns |
| `.env` | Configuration and API keys (gitignored) |
| `.env.example` | Template for `.env` with setup instructions |
| `zotero-readonly.sqlite` | Read-only snapshot of the Zotero database (refreshed each session, gitignored) |
| `research/` | Research deliverables and outputs from past sessions (gitignored) |

