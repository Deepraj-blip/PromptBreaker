# PromptBreaker Part A ↔ Part B Integration (Minimal CSV Bridge)

## Context

PromptBreaker has two halves:
- **Part A** — the payload supply-chain repo (this repo,
  `promptbreaker-payloads-repo`): SecLists-style tiered payload lists +
  `payloads/models/**` + `metadata.json`/`payload_index.json`, auto-refreshed
  from public sources. Complete and merged to `main`.
- **Part B** — the Burp Suite extension (`com.llminjector.*`, Java/Montoya).
  We have it only as a compiled `prompt-breaker.jar`; there is no source
  project on hand. A CFR decompile (43 files, ~10.7k LOC) exists as a
  readable reference but is **not a buildable project** (no Gradle files,
  external Montoya dependency, decompiler artifacts that may not recompile
  as-is).

The goal of this work is to let Part B **consume** Part A's payloads. Part A
and Part B are intentionally separate (the runtime-fetch supply chain) — this
is an *integration*, not a literal merge into one artifact.

### Why the tool can't consume Part A today (established by reading the
decompiled `SourceFetcher.java`)

- Extraction modes are `WHOLE_FILE` (whole file → one prompt), `CODE_BLOCKS`
  (markdown fences/blockquotes), and `CSV` (one configured column). There is
  **no per-line mode**, so our newline-delimited tier files can't be ingested
  as many-payloads-per-file.
- `MIN_LEN = 40` drops any payload shorter than 40 characters — which guts the
  **small tier**, the one that matters most for tight token budgets.
- Only `.md/.markdown/.mkd/.txt/.text/.prompt` + `.csv` files are fetched;
  `.json` is ignored, so `metadata.json`/`payload_index.json` are invisible.
- `Prompt` (`name/content/category/source/enabled`) and `ScanConfig` have **no
  tier and no model** concept.
- CSV extraction assigns the *source's* single `category` to every row, so a
  mixed-category CSV would be mislabeled.

### Technique audit (recorded for the record; mostly future work)

The tool's transform/technique library is **exhaustive** — 195 techniques
across Case/Cipher/Concealment/Encoding/Format/Symbol/Technical/Unicode/
Visual/Substitution/Special/Framing, including every common encoding/cipher,
the full Unicode-evasion ("P4RS3LT0NGV3") family, and the modern jailbreak
*framing* techniques (Crescendo, Many-Shot, Skeleton Key, Payload-Split,
Refusal-Suppression, Prefix-Injection, Persona, Hypothetical, Completion,
Context-Dilution, Instruction-Override, Sandwich, Likert, Math, Story,
Few-Shot, Translation, plus JSON/XML/MD/code wrappers). Injection points cover
body, JSON-path, jsonstr, multipart, url-encoded form, raw prefix/suffix,
markers, and headers.

Genuinely missing items are **orchestration capabilities, not more
transforms**, and are all **out of scope for this integration** (future work):
1. Automated **multi-turn** attacks (Crescendo exists only as a manual
   copy-across-turns template; the scanner is single-request).
2. **Indirect / second-order** injection (planting a payload in data the LLM
   later ingests — RAG doc, page, tool output).
3. Attacker-LLM-in-the-loop adaptive generation (PAIR/TAP-style).
4. Output-side encoding evasion (asking the model to *reply* encoded).

(GCG-style adversarial suffixes need gradient/optimizer access — not
applicable to a black-box Burp tool.)

## Goals

- Get Part A's payloads — **including the short small-tier ones** — flowing
  into the Part B scanner, with correct per-payload categories.
- Establish a **buildable** Part B project so the extension can be changed at
  all, now and later.
- Keep the change **minimal**: no tier/model/budget selector UI yet (deferred),
  no new attack orchestration.
- Preserve the Part A ↔ Part B separation (runtime fetch), not a bundle.

## Non-goals (explicitly deferred)

- Tier / model / token-budget selection UI (the headline feature — a later
  project once payloads flow).
- Consuming `payload_index.json`/`metadata.json` or a manifest.
- Multi-turn, indirect injection, attacker-LLM, output-encoding evasion (audit
  items 1–4 above).
- Any UI polish, theming, or new tabs.

## Design

Three pieces, different risk profiles. **Piece 1 gates Piece 3** and is
partly a feasibility spike; **Piece 2 is independent** and can land first.

### Piece 1 — Reconstruct a buildable Part B project (gating spike)

Stand up a real project around the decompiled source and prove it builds to a
loadable extension.

- New project root: `/Users/ddhasmana1/promptbreaker/prompt-breaker-tool/`
  (outside this payload repo; its own git repo).
- Copy the decompiled sources (currently in session scratch) into
  `src/main/java/com/llminjector/**` — the durable home for them.
- Gradle build (`build.gradle`) with the Montoya API as a `compileOnly`
  dependency (`net.portswigger.burp.extensions:montoya-api`, a recent
  version), Java 21 toolchain (the jar was built with 21), producing a fat/
  plain jar loadable as a Burp extension.
- Iterate `./gradlew build`, fixing decompiler artifacts until it compiles
  (expected classes of fixes: synthetic `lambda$…` methods, enum-switch helper
  classes, raw-generic casts, `record` component quirks).
- **Success criteria:** `./gradlew build` produces a jar with no errors, and
  (manual, user-run) the jar loads in Burp without an extension error.
- **Spike nature / fallback:** if the decompile needs disproportionate manual
  reconstruction to compile, that is a finding — **stop and switch to
  obtaining the real source** rather than grinding. Record the effort and the
  blocker.
- **Verification I can do here:** `./gradlew build` success + `jar tf` shows
  the expected classes. **Verification only the user can do:** loading the jar
  into Burp and confirming it initializes.

### Piece 2 — Part A CSV export (independent, fully testable here)

Add a CSV emitter to the payload pipeline so the whole corpus is available as a
single tool-consumable file.

- A new function in `scripts/fetch_and_build.py` (e.g. `write_payload_csv`),
  invoked from `main()` right after the `payload_index.json` write, writing
  `payloads/promptbreaker.csv`. It consumes the in-memory `payload_index`
  list already built there (do not re-read the JSON, do not recompute).
- Columns (header row): `prompt,category,tier,model,source_count`.
  - One row per (payload, model) the way `payload_index.json` already
    enumerates entries: a payload tagged with N models yields N rows (model
    column set); an untagged payload yields one row with an empty `model`.
    `tier`/`category`/`source_count` come straight from the existing
    `payload_index.json` build (reuse `build_payload_index`'s data — do not
    recompute).
  - Proper CSV quoting (Python `csv` module) so payloads containing commas/
    quotes/newlines are safe.
- The tool's current CSV extractor reads only the `prompt` column, so this file
  works with the tool **today**; the extra columns are inert until the later
  UI reads them.
- Wired into the pipeline so the weekly refresh regenerates it; committed like
  the other generated artifacts.
- **Tests** (pytest, in the existing `scripts/test_fetch_and_build.py`):
  header row correct; a multi-model payload produces one row per model; an
  untagged payload produces one row with empty model; embedded comma/quote/
  newline is round-trip-safe; row count matches `payload_index` expansion.

### Piece 3 — Part B minimal consume-enhancement (needs Piece 1 built)

Three small, surgical changes to the reconstructed tool source:

1. **Relax the length floor for CSV.** `SourceFetcher`'s `MIN_LEN = 40`
   currently drops short payloads in all extraction paths. Exempt the CSV path
   from `MIN_LEN` (keep only a non-blank guard: skip rows whose stripped prompt
   is empty), leaving the `WHOLE_FILE`/`CODE_BLOCKS` behavior unchanged so the
   existing built-in sources aren't affected.
2. **Read a `category` column in CSV extraction.** In `addCsv`, if the CSV
   header contains a `category` column, use the per-row value instead of the
   source's single `category`; fall back to the source category when absent.
   (Optionally also read `tier`/`model` into the `Prompt` — only if it's a
   trivial carry; otherwise leave for the UI project, per non-goals.)
3. **Add PromptBreaker as a built-in source.** Add a `PromptSource.BUILT_IN`
   entry pointing at the payload repo (owner/repo/branch, `Extraction.CSV`,
   `csvColumn = "prompt"`, targeting `promptbreaker.csv`).
   - Note: `SourceFetcher` walks the whole tree and fetches every `.csv`; since
     the repo has exactly one CSV (`promptbreaker.csv`), this resolves cleanly.
     If verazuo-style multi-CSV ambiguity ever arises, revisit; not a concern
     for this repo now.

- **Verification I can do here:** `./gradlew build` still succeeds; unit-level
  reasoning about `addCsv`. **User-run:** load jar in Burp → Prompts tab →
  fetch the PromptBreaker source → confirm payloads appear, short ones
  included, categories correct.

## Data flow (after this work)

```
Part A pipeline  ──emits──▶  payloads/promptbreaker.csv  (committed, on GitHub)
                                        │  raw.githubusercontent fetch (tool CSV mode)
                                        ▼
Part B SourceFetcher.addCsv  ──▶  List<Prompt>(content, category)  ──▶  Prompts tab ──▶ ScanEngine
```

Tier/model columns are carried in the CSV but unused by the tool until the
deferred selector UI is built.

## Ordering

1. **Piece 1 (spike) + Piece 2 in parallel.** Piece 2 is independent of the
   tool; Piece 1 proves the build. Do not spec/plan Piece 3 in executable
   detail until Piece 1 yields a building jar.
2. **Piece 3** once Piece 1 builds.

If Piece 1's fallback triggers (decompile won't build reasonably), Piece 3 is
blocked pending real source; Piece 2 still stands on its own (the CSV is useful
to any consumer).

## Testing / validation

- Piece 2: `pytest scripts/` green, including the new CSV tests; a real
  pipeline run produces a well-formed `promptbreaker.csv` whose row count and
  categories match `payload_index.json`.
- Piece 1: `./gradlew build` succeeds; `jar tf` lists `com/llminjector/…`
  classes; user confirms Burp load.
- Piece 3: `./gradlew build` succeeds after the edits; user confirms in Burp
  that the PromptBreaker source fetches payloads (short ones present, per-row
  categories correct).

## Repos touched

- **This repo** (`promptbreaker-payloads-repo`): Piece 2 (CSV export + tests),
  and this spec.
- **New repo** (`prompt-breaker-tool`): Pieces 1 and 3 (reconstructed buildable
  project + minimal consume changes).
