# Payload List Picker — Manifest-Driven Multi-Select

_Design spec — 2026-09-16_

## Context

Supersedes the **tool-consumption half** of
`2026-09-16-consolidate-tool-sources-design.md`. That spec's other two
outcomes stand and are already merged to the feature branches:

- ✅ The Burp extension consumes only the PromptBreaker source (8 external
  runtime sources dropped).
- ✅ The pipeline was expanded (override 23→47, exfil 11→31).

What changes: instead of the extension fetching one flat `promptbreaker.csv`
and showing a `tier` column, the user **selects which payload lists to run**
from a named, grouped, multi-select picker. The tiered (`small`/`medium`/
`large`) and model-specific lists exist precisely so a user can choose the
right set for a token budget or a target model; a flat blob defeats that.

### Why the extension can't do this today

- Extraction modes are `WHOLE_FILE`, `CODE_BLOCKS`, `CSV` — **no per-line
  mode**, so newline-delimited `.txt` lists (one payload per line) can't be
  read as many-payloads-per-file.
- Fetch pulls **every** matching file in the whole repo tree — no way to
  fetch a single chosen list, and a naive `.txt` fetch would ingest
  `payloads/risky/**` and duplicate `payloads/models/**`.
- There is no catalog of "available lists" for the tool to present.

## Goals

1. The pipeline publishes a **manifest** (`payloads/lists.json`) enumerating
   every real, selectable list with a human-readable name and a payload count.
2. The pipeline emits **combined per-tier lists** (all categories at one size).
3. The extension presents a **grouped, multi-select picker** built from the
   manifest and runs the **deduped union** of the ticked lists.
4. `payloads/risky/**` is never listed or fetched by this feature.

## Non-goals

- Exposing `risky/` (stays opt-in via manual "Add source…" pointed at a raw
  file — unchanged, out of scope here).
- A new ranking/tiering algorithm — reuse the pipeline's existing token-tiering
  and top-N ranking.
- Removing the flat `promptbreaker.csv` output (kept; other tools consume it).

## The list catalog (built from what the pipeline really emits)

The pipeline only writes a list file where real data exists, so the catalog is
**data-driven via the manifest** — never a hardcoded grid. Groups:

- **A. Category + size** — `jailbreak-{small,medium,large}`, `exfil-{small,medium}`,
  `override-{small,medium,large}`, `encoding-{small,medium}` (whatever exists).
- **B. Size, all categories combined** — NEW: `all-{small,medium,large}`.
- **C. Model-targeted** — `payloads/models/<model>/<category>/<model>-<category>-<tier>.txt`
  (e.g. OpenAI · Jailbreak — Medium), only where data exists.
- **D. Ranked quick-test** — `top25`, `top100`, `top1000`.

Display names are explicit, e.g. `"Jailbreak — Medium (60)"`,
`"All categories — Small (22)"`, `"OpenAI · Jailbreak — Large (2)"`,
`"Top 100 (ranked by cross-source agreement)"`.

## Design

### Part A — pipeline (`promptbreaker-payloads-repo`)

**A1. Combined per-tier lists.** After the per-category tier files are written,
write `payloads/all/all-small.txt`, `all-medium.txt`, `all-large.txt` — the
union of every category's payloads at that tier, deduped, one per line, same
`#`-comment/blank-line convention. Generated only for tiers that have content.
`risky/` payloads are excluded (they already are, upstream of tier files).

**A2. Manifest `payloads/lists.json`.** An object:
```json
{
  "generated": "<iso8601>",
  "lists": [
    {"id": "jailbreak-medium", "name": "Jailbreak — Medium",
     "path": "payloads/jailbreak/jailbreak-medium.txt",
     "category": "jailbreak", "tier": "medium", "model": null, "count": 60,
     "group": "category"},
    {"id": "all-small", "name": "All categories — Small",
     "path": "payloads/all/all-small.txt", "category": null, "tier": "small",
     "model": null, "count": 22, "group": "combined"},
    {"id": "openai-jailbreak-large", "name": "OpenAI · Jailbreak — Large",
     "path": "payloads/models/openai/jailbreak/openai-jailbreak-large.txt",
     "category": "jailbreak", "tier": "large", "model": "openai", "count": 2,
     "group": "model"},
    {"id": "top100", "name": "Top 100 (ranked by cross-source agreement)",
     "path": "payloads/top100.txt", "category": null, "tier": null,
     "model": null, "count": 100, "group": "ranked"}
  ]
}
```
`group` ∈ {`category`, `combined`, `model`, `ranked`}. One entry per real list;
`risky/` never included. `count` = non-blank, non-`#` lines in the file.
Written every build (after the files it references exist) and committed like the
other artifacts. The weekly cron regenerates it.

**A3. Tests.** Extend `scripts/test_fetch_and_build.py`: `lists.json` lists every
generated non-risky `.txt` and no risky one; each entry's `count` matches its
file; `all-<tier>` equals the deduped union of that tier's category files.

### Part B — extension (`prompt-breaker-tool`)

**B1. Per-line extraction.** Add `Extraction.PER_LINE` (or equivalent): read a
`.txt` file as one payload per line, trimming blanks and `#`-comment lines. Wire
it into `SourceFetcher` alongside `WHOLE_FILE`/`CODE_BLOCKS`/`CSV`.

**B2. Manifest fetch + picker.** When fetching the PromptBreaker source, first
pull `payloads/lists.json` (via existing `GitHubApi.fetchRaw`). Render a
**multi-select picker** grouped A→D (Category+size, Combined, Model, Ranked),
each row `name (count)` with its `id`. Persist the ticked `id` set in
`ScanConfig`.

**B3. Path-scoped union fetch.** On fetch/run, for each ticked list read its
`path` from the manifest, `fetchRaw` it, per-line parse, and union into the
library **deduped by payload text**. Each resulting `Prompt` carries its
`category`/`tier`/`model` from the manifest entry (reuse the `tier`/`model`
fields already added to `Prompt`). Never fetch a path under `payloads/risky/`
(guard even if a manifest were tampered with).

**B4. Disposition of the prior CSV consumption.** The flat `promptbreaker.csv`
source is replaced as the *primary* path by the picker. Keep the `Prompt`
`tier`/`model` fields and the Tier column (still informative). The single
`promptbreaker.csv` BUILT_IN entry is removed from the fetch path in favor of
the manifest; `AutoFetcher`'s daily refresh now refreshes via the manifest
(fetch the currently-ticked lists, or default to `all-medium` on first run).

**B5. Testing.** No unit harness exists; verification is `./gradlew clean jar`
compiling clean, the ServiceLoader file present in the JAR, then manual Burp:
picker shows the grouped named lists, ticking two lists loads their deduped
union, Tier populated, a scan runs, and `risky/` never appears.

### Data flow

```
upstreams ─(build)→ fetch_and_build.py ─→ per-category tier .txt
                                        ─→ payloads/all/all-<tier>.txt   (A1)
                                        ─→ payloads/lists.json           (A2)
                                              │ committed, pushed to main
                                              ▼
   extension: fetch lists.json → picker → fetchRaw(selected paths) → per-line
              → deduped union → library (enabled) → Scanner runs them
```

## Risks / open questions

- **First-run / AutoFetcher default:** with no ticked lists yet, default the
  daily refresh + first fetch to `all-medium` so the library is never empty.
  (Ruling if unspecified: default `all-medium`.)
- **Manifest/tree drift:** the extension trusts `lists.json` `path`s but hard-
  excludes any `payloads/risky/` path regardless, so a stale/tampered manifest
  can't pull quarantined content.
- **Two-repo ordering:** ship Part A (manifest + combined lists on `main`) before
  Part B relies on it; until `lists.json` exists on `main`, the picker shows an
  explanatory empty state.
- **GitHub push required:** the tool fetches from `main`; the expanded lists +
  manifest only reach Burp once the payloads branch is pushed/merged to `main`.

## Sequencing

1. Part A: combined lists + manifest + tests; rebuild; commit; **push to main**.
2. Part B: per-line mode → manifest fetch + picker → union fetch/run → replace
   CSV path; rebuild JAR; manual Burp verification.
