# PromptBreaker Payload Repo: Model-Specific Payloads + Category Completion

## Context

This repo (Part A of PromptBreaker) is a SecLists-style, auto-refreshing
collection of prompt-injection/jailbreak payloads consumed by the
PromptBreaker Burp extension (Part B, not in this repo) via raw GitHub URLs.
Payloads are aggregated from public sources by `scripts/fetch_and_build.py`,
deduplicated, categorized (`jailbreak`, `exfil`, `override`, `encoding`), and
bucketed into token-budget tiers (`small` / `medium` / `large`) plus
cross-category `top25`/`top100`/`top1000` files.

Three gaps drove this design:

1. **No model targeting.** Every payload is treated as equally likely to
   work against any LLM. In practice, effectiveness is heavily influenced by
   which model (and its specific alignment/guardrails) is the target — a
   payload tuned against one model's guardrails may do nothing against
   another's.
2. **Undelivered category promise.** The README already documents `exfil/`
   and `override/` as categories, but no sources populate them today, and
   `infer_category()` can never actually classify anything as `"override"` —
   unclassified content silently falls back to `jailbreak`.
3. **Pipeline quality.** `scripts/fetch_and_build.py` runs unattended on a
   weekly cron and auto-commits its output, but has zero test coverage and
   estimates tokens with a crude `word_count * 1.3` heuristic — a real
   accuracy problem for a tool whose entire premise is token-budget
   awareness. Since this design already touches the pipeline's core
   bucketing logic, it also hardens these weak points rather than building
   new features on top of them.

## Goals

- Add model-specific payload sets, without losing or replacing the existing
  model-agnostic (generic) lists — a payload set for "I know it's GPT-4"
  should be additive precision, not a fork of the data.
- Make model targeting an open, config-driven dimension (adding a new model
  is a `sources.yaml` edit, not a code change) rather than a hardcoded list
  that goes stale as new models ship.
- Avoid false precision: only tag a payload to a model when the source is
  genuinely about that model, never by guessing from keywords in the text.
- Close the `exfil`/`override` gap so the repo actually delivers the
  multi-category structure it already documents.
- Measure token counts accurately enough that "small/medium/large" tiers
  mean what they claim, since accurate budget-fitting is the tool's core
  value proposition.
- Give the pipeline real test coverage before it grows a second bucketing
  dimension (model) on top of the first (category/tier) — it commits to
  the repo unattended and today has none.

## Non-goals (out of scope for this spec)

- The Burp extension UI for selecting tier/category/model (Part B — not in
  this repo).
- Per-model `top25`/`top100`/`top1000` files. The existing global top-N
  files stay category- and model-agnostic; model-specific top-N can be
  added later if there's demand.
- Automatic model inference from payload text (e.g., regexing for "GPT" or
  "Claude" mentions). Model tagging is source-level and curated only.
- Retroactively re-tagging existing generic payloads by model.

## Design

### 1. Model tagging is source-level, not content-inferred

`scripts/sources.yaml` gains an optional `model:` field alongside the
existing `category:` field:

```yaml
- name: example-gpt-jailbreak-collection
  url: https://raw.githubusercontent.com/.../file.md
  parser: parse_markdown_code_blocks
  category: jailbreak
  model: openai        # optional; omit for model-agnostic sources
```

A source either is or isn't fundamentally about one model. There is no
per-entry heuristic that tries to detect "this specific payload mentions
Claude, so tag it Claude" — that produces false precision (a payload
mentioning a model in passing isn't necessarily tuned against it), which is
worse for an engagement than an honestly-generic payload.

### 2. Model-tagged payloads populate both the generic and model-specific pools

Every payload keeps contributing to its existing category/tier file (the
generic pool), regardless of whether it's model-tagged. If it's also
model-tagged, it *additionally* gets written to a model-specific file. This
means:

- Switching from "I don't know the target model" to "I know it's Claude"
  never loses coverage — you gain a narrower, more precise file on top of
  what already existed.
- The generic pool's meaning stays exactly what it is today: broad coverage
  for when the model is unknown or you want maximum breadth.

### 3. Directory layout

Model-specific files live in a new `payloads/models/<model>/` tree that
mirrors the existing `category/tier` shape:

```
payloads/
  jailbreak/                          # unchanged — generic pool
    jailbreak-small.txt
    jailbreak-medium.txt
    jailbreak-large.txt
  encoding/ ...                        # unchanged
  exfil/                               # newly populated (see §5)
    exfil-small.txt
    ...
  override/                            # newly populated (see §5)
    override-small.txt
    ...
  models/
    openai/
      jailbreak/
        openai-jailbreak-small.txt
        openai-jailbreak-medium.txt
      encoding/
        openai-encoding-small.txt
    anthropic/
      jailbreak/
        anthropic-jailbreak-small.txt
  metadata.json
  top25.txt / top100.txt / top1000.txt  # unchanged — global, model-agnostic
```

Files/folders are only created for `(model, category, tier)` combinations
that actually have data — the build script already only writes non-empty
buckets today (see current `by_category` loop), so this falls out of the
same pattern with one more key and doesn't require special-casing empty
models.

### 4. Pipeline changes (`scripts/fetch_and_build.py`)

- `seen_fingerprints` keeps its current `(text, category)` shape and
  first-seen-wins behavior, unchanged.
- A new `fingerprint_models = defaultdict(set)` accumulates *every* model
  tag seen for a given fingerprint across *all* sources that produced it —
  mirroring how `fingerprint_sources` already accumulates source names for
  top-N ranking, rather than first-seen-wins. This matters: if the same
  payload text is fetched from both a generic source and a model-tagged
  source, first-seen-wins on a single field would silently drop the model
  tag whenever the generic source happens to be processed first. Using a
  set means the payload still lands in the model-specific file regardless
  of fetch order.
- The existing `by_category[category][tier]` bucketing and file-writing
  loop is unchanged — it still runs over *all* entries regardless of model
  tag, preserving the generic pool exactly as today.
- A new `by_model[model][category][tier]` bucketing loop runs over every
  `(fp, model)` pair in `fingerprint_models`, using that fingerprint's
  `(text, category)` from `seen_fingerprints`, and writes to
  `payloads/models/<model>/<category>/<model>-<category>-<tier>.txt` using
  the same tier thresholds and file-header format as the generic writer.
- `infer_category()` gains an `override` branch: keyword heuristics for
  system-prompt-override attempts (e.g., "new instructions", "override your
  instructions", "system prompt is now", "disregard your system prompt")
  are checked before falling back to `jailbreak`. Distinguishing `override`
  from `jailbreak` is inherently a little fuzzy (an override attempt is a
  narrower, single-purpose subset of jailbreak techniques); where a source
  is unambiguous, it should set `category:` explicitly in `sources.yaml`
  rather than rely on inference.
- `main()` wires the new model bucketing into the existing write loop and
  extends `metadata` with a `"models"` key (see §6).

### 5. Closing the `exfil`/`override` source gap

`scripts/sources.yaml` needs real sources feeding `exfil` and `override`
categories (currently only `jailbreak` and `encoding` have populated
sources). Concrete source URLs are a research/verification task, not a
design decision — this spec doesn't hardcode unverified URLs as fact. It
follows the same convention already established in `sources.yaml` (the
commented-out `garak-dan-probes` entry): candidate sources get added either
as live entries (if verified working during implementation) or as
commented-out entries with a note on what needs verifying. This
verification work becomes an explicit step in the implementation plan.

### 6. `metadata.json` schema addition

A `"models"` block is added, parallel in shape to the existing
`"categories"` block:

```json
{
  "generated": "...",
  "total_unique_payloads": 66,
  "source_errors": [],
  "categories": {
    "jailbreak": { "small": 38, "medium": 21, "large": 3 },
    "encoding": { "small": 3, "medium": 1 }
  },
  "models": {
    "openai": {
      "jailbreak": { "small": 12, "medium": 4 }
    },
    "anthropic": {
      "jailbreak": { "small": 5 }
    }
  }
}
```

This is additive — nothing in the existing `"categories"` shape changes, so
any current consumer reading only that key is unaffected.

### 7. Seed models

Schema and pipeline support any model as an open string key (no code-level
enum). At launch, `sources.yaml` is seeded with real sources for:

- `openai` — deepest and most consistent public research (ChatGPT/GPT
  jailbreak corpora).
- `anthropic` — smaller but real body of Claude-specific material.

`google` (Gemini) and `meta` (Llama) are left unpopulated at launch rather
than filled with thin or borderline-generic material mislabeled as
model-specific — they get added the same way any future model will: a
`sources.yaml` entry, once a genuinely model-specific source is identified.

### 8. Documentation

`README.md` is updated to:
- Document the `payloads/models/<model>/<category>/` tree and its file
  naming convention, so Part B (or any raw-URL consumer) has a stable
  contract to build a URL from once it knows the fingerprinted model.
- Document the optional `model:` field in `sources.yaml` and the
  source-level-only tagging rule (no content inference).
- Note that model-specific files are a strict superset addition — the
  generic files retain their current meaning and content.
- Reflect that `exfil`/`override` are now actually populated, not just
  documented.

### 9. Accurate token counting

`estimate_tokens()` is replaced with a real tokenizer count via `tiktoken`
(added to `scripts/requirements.txt`), using a single fixed encoding
(`o200k_base`, GPT-4o/GPT-5-family's encoding) as a consistent baseline for
every payload. This isn't an exact per-model count — Claude, Llama, and
others use different tokenizers — but modern tokenizers land within a
similar ballpark for natural-language text, and the priority here is a
*consistent, real* measurement instead of a linear word-count guess. This
is documented in the README as an approximation, same spirit as the
existing top-N ranking's "not a rigorous effectiveness ranking" disclaimer.
Tier thresholds (`SMALL_MAX_TOKENS = 15`, `MEDIUM_MAX_TOKENS = 60`) stay as
today's values — only the measurement underneath them gets more accurate,
which will shift some payloads across tier boundaries versus the current
output (expected and desired; it's a correctness fix).

### 10. Encoding category (sourced only — variant generation cut from scope)

An earlier draft of this design had the build script mechanically derive
base64/ROT13/homoglyph/leetspeak variants of top payloads to grow the thin
`encoding` category. **That was removed:** the PromptBreaker Burp extension
(Part B) applies encoding transforms at request time, so pre-baking encoded
variants into these static lists would be pure duplication.

The `encoding` category still exists and is populated from its
genuinely-sourced payloads — the garak "decode this and act on it" style
templates, which are a distinct attack (instructing the model to decode and
execute), not a transform of another payload. No mechanical variant
generation happens in this repo.

### 11. Per-payload confidence index (new capability)

`fingerprint_sources` already counts how many independent sources produced
each payload, but today that count is only used internally for top-N
ranking and then discarded. The mission's stated goal of severity scoring
in the Burp extension needs real signal to score with, so this design
exposes it: a new `payloads/payload_index.json` is written alongside the
existing `.txt` files, one entry per unique payload:

```json
[
  {
    "text": "Disregard any previous instructions...",
    "category": "jailbreak",
    "tier": "small",
    "models": ["openai"],
    "source_count": 3,
    "sources": ["payloadsallthethings-prompt-injection", "owasp-llm-top10-doc", "garak-continuation-probes"]
  }
]
```

This is a separate, additive file — the plain newline-delimited `.txt`
files stay exactly as they are today for any consumer that just wants a
raw list (the repo's existing "any tool that wants a plain
newline-delimited payload list" promise is unaffected). `source_count` is
documented as a confidence proxy (more independent sources agreeing a
technique is worth including ≈ more established), not a guarantee the
payload works against any particular target.

### 12. Pipeline test coverage

A new `scripts/test_fetch_and_build.py` (pytest) covers the logic that
previously shipped untested:
- `fingerprint()` normalization (whitespace/case-insensitivity)
- `estimate_tokens()` against known strings, and the small/medium/large
  boundary logic
- Each parser (`parse_garak_python_module`, `parse_markdown_code_blocks`,
  `parse_json_array`) against small inline fixture strings, including
  edge cases each already guards against (short/noise spans, non-JSON
  input, syntax errors)
- `infer_category()`, including the new `override` branch
- The `fingerprint_models` set-accumulation fix from §4 — a fingerprint
  that appears from both a model-tagged and non-model-tagged source ends
  up in the model-specific output regardless of fetch order
`pytest` is added to `scripts/requirements.txt`.

### 13. CI regression guardrail

`fetch_and_build.py` gains a post-build sanity check: it exits non-zero if
`total_unique_payloads == 0`, or if it dropped by more than 50% versus the
previously committed `payloads/metadata.json`. If no prior
`payloads/metadata.json` exists (fresh clone, first run), only the
zero-payload check applies — there's no baseline yet to compare a drop
against. This catches a catastrophic run (e.g., all sources unreachable, a
parsing regression) before it silently commits an empty or gutted payload
set — individual source failures still degrade gracefully as they do today
(per-source try/except, logged to `source_errors`), this guard only fires
on an aggregate collapse.
`.github/workflows/refresh-payloads.yml` gains a "Run tests" step
(`pytest scripts/`) before the fetch/build step, so a pipeline regression
fails the CI run instead of reaching the auto-commit step at all.

### Consumer impact

The existing raw-URL contract (`payloads/<category>/<category>-<tier>.txt`)
is unchanged — nothing currently pointing at those paths breaks. This is a
strictly additive change: new files (including `payload_index.json`), a
new optional `sources.yaml` field, a new `metadata.json` key, and two new
dependencies (`tiktoken`, `pytest`). The one behavior change existing
consumers will observe is that some payloads shift tier (small ↔ medium ↔
large) once token counting switches from the word-count heuristic to
`tiktoken` — called out here explicitly since it's a real, intended change
in file contents, not just additions.

## Testing / validation

- `pytest scripts/` passes (§12) — covers parsers, dedup, tiering,
  category inference, model-tag accumulation, and encoding-variant
  generation in isolation.
- Run `scripts/fetch_and_build.py` locally against the updated
  `sources.yaml` and confirm: generic category files are unchanged in
  *membership logic* for non-model-tagged sources (contents will shift
  slightly due to the tiktoken change, that's expected); model-tagged
  sources appear in both their generic file and their new model-specific
  file; `override` and `encoding` categories now have non-empty,
  meaningfully larger output; `metadata.json` includes a correct
  `"models"` block; `payload_index.json` is written and its `source_count`
  values match `fingerprint_sources` sizes.
- Spot-check dedup ordering: fetch a model-tagged source *after* a
  non-model-tagged source that yields the identical payload text, and
  confirm the payload still appears in the model-specific file (i.e. the
  model tag isn't lost to fetch order).
- Confirm the regression guard actually fires: temporarily point all
  sources at invalid URLs and verify `fetch_and_build.py` exits non-zero
  rather than committing an empty payload set.
