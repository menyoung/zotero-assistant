# Perplexity Operations Reference

How to use Perplexity API for research queries. Read this file when running literature searches or exploration.

## Model selection

- **`sonar-deep-research`**: Broad exploration, landscape mapping, cross-field searches, "find everything about X." Slow (minutes), expensive, thorough.
- **`sonar-pro`**: Targeted queries, batch metadata lookup, author profiling. Fast, cheaper. DOIs ~80% wrong — verify on Crossref before opening.

## Execution

Always `run_in_background` so the conversation never blocks. Draft query, workshop with user if needed, send, resume immediately.

## Prompt design — concentric rings

Structure queries in three priority rings:

- **Ring 1 — "More like this" (~60%):** Name 1-3 library papers. Ask for follow-on work, citing papers, same groups.
- **Ring 2 — "Same tech, different application" (~30%):** Adjacent applications of same processes in other industries.
- **Ring 3 — "Cross-disciplinary" (~10%):** Only if rings 1-2 exhausted. Name specific mechanisms, not broad fields.

## Anti-patterns

- Don't say "go beyond these authors" without first saying "find more from their orbit"
- Don't list 4+ adjacent fields — leads to superficial touring
- Don't ask 5 sub-questions in one query — use `sonar-pro` follow-ups
- Don't over-exclude — long exclusion lists make Perplexity give up early. Anchor with specific citation trails instead
- Best prompts: 60% specific search tasks, 30% context, 10% exclusions

## Request template

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

Always end with explicit formatting instructions (numbered list with fixed fields per item, or markdown table, or JSON-like structure). Don't let Perplexity return freeform prose.
