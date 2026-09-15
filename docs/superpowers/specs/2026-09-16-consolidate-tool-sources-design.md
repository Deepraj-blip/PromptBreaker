# Consolidate Tool Payload Sources + Expand Thin Categories

_Design spec — 2026-09-16_

## Context

PromptBreaker is a two-repo supply chain:

- **Part A — `promptbreaker-payloads-repo`** (Python pipeline): `scripts/fetch_and_build.py`
  + `scripts/sources.yaml` harvest public LLM-security sources at **build time**, then
  dedupe (with provenance), classify by category, tier by token count, **quarantine
  harmful-compliance payloads into `payloads/risky/`**, rank top-N, and emit the
  standalone artifacts: `payloads/promptbreaker.csv`, tiered `.txt` files,
  `payload_index.json`, `metadata.json`. Refreshed weekly by
  `.github/workflows/refresh-payloads.yml` (cron: Sundays 03:00 UTC).
- **Part B — `prompt-breaker-tool`** (Java/Montoya Burp extension, `com.llminjector.*`):
  loads a JAR into Burp, presents a Prompts library + Scanner, and fetches prompt
  sources from GitHub at runtime.

This work follows `2026-09-08-part-a-b-integration-design.md` (the CSV bridge that lets
Part B consume Part A's `promptbreaker.csv`).

### The problem

Part B hardcodes **9 sources** in `PromptSource.BUILT_IN` — the PromptBreaker source
**plus 8 unrelated third-party repos** (`elder-plinius/L1B3RT4S`, `elder-plinius/CL4R1T4S`,
`CyberAlbSecOP/Awesome_GPT_Super_Prompting`, `0xk1h0/ChatGPT_DAN`,
`TakSec/Prompt-Hacking-Resources`, `jujumilk3/leaked-system-prompts`,
`verazuo/jailbreak_llms`, `f/awesome-chatgpt-prompts`) that the tool fetches **raw at
runtime**. This is:

1. **Redundant** — `ChatGPT_DAN` and `verazuo/jailbreak_llms` are already aggregated by
   Part A's pipeline (better processed).
2. **Noisy / low-value** — `f/awesome-chatgpt-prompts` is persona prompts ("act as a
   chef"), not attacks; whole-file sources dump giant blobs.
3. **A safety gap** — runtime raw fetch applies **none** of Part A's dedup, tiering, or
   **harmful-compliance quarantine**. It reaches around the very safety boundary the
   pipeline exists to enforce.

Separately, Part A's coverage is lopsided: **jailbreak is strong; exfil/override/encoding
are thin** (see Coverage Audit below).

## Goals

1. Part B consumes **only** the PromptBreaker aggregated list. Drop the 8 external sources
   from the tool's runtime.
2. Part B surfaces the **`tier`** and **`model`** metadata already present in
   `promptbreaker.csv` (currently ignored).
3. Part A's list **covers more** — strengthen the thin categories (exfil / override /
   encoding) by adding legit upstream sources to the pipeline.

## Non-goals / Out of scope

- Rebuilding the fetcher to consume raw tiered `.txt` files (rejected: the fetcher has no
  path-scoping, so a whole-tree `.txt` fetch would ingest `payloads/risky/**` and duplicate
  `payloads/models/**`, breaking quarantine). The CSV — which already excludes `risky/` —
  is the safe contract.
- Adding external jailbreak padding (e.g. L1B3RT4S). Jailbreak is already the strongest
  category; this would add breadth where we least need it.
- A dedicated tier-filter UI control (YAGNI — folded into existing search unless requested).
- Changing the transform/technique engine.

## Key decision: self-contained list, built from external sources

**The shipped artifact (`promptbreaker.csv`) is standalone** — no external links, nothing
fetched at runtime by the tool or from the CSV. **The pipeline keeps harvesting from legit
external upstreams at build time** to *produce and refresh* that standalone list; the weekly
cron and all pipeline source connections are **preserved** (Goal 3 *adds* sources, never
removes the good ones). "No external in the tool" and "keep the update chain" are both
satisfied: external only ever appears in Part A's build step, never in Part B's runtime.

## Design

### Part B — Burp extension (`prompt-breaker-tool`)

1. **`fetch/PromptSource.java`** — reduce `BUILT_IN` to the single `PromptBreaker` source
   (`owner=Deepraj-blip, repo=PromptBreaker, branch=main, Extraction.CSV, csvColumn=prompt`).
   Removing the 8 collapses both the manual Fetch dropdown and `AutoFetcher`'s daily refresh
   to your list alone. `AutoFetcher.PLACEHOLDER_OWNER` logic is untouched.
2. **`fetch/SourceFetcher.java` (`addCsv`)** — it reads `prompt` + `category` today and
   ignores `tier`/`model`. Add column lookups for **`tier`** and **`model`** (blank when
   absent, so non-PromptBreaker CSVs a user adds via "Add source…" still work). `source_count`
   stays ignored.
3. **`model/Prompt.java`** — add `tier` and `model` fields (nullable), threaded through the
   constructor and persistence in `ExtensionState`. Backward-compatible: missing keys load as
   null.
4. **`ui/PromptsTab.java`** — add a **Tier** column to the library table and include tier +
   model text in the existing search filter. Optional Model column if cheap.

**Testing (Part B):** rebuild JAR (`./gradlew clean jar`), reload in Burp, verify: dropdown
shows only PromptBreaker; library count sane (~28 built-in defaults + fetched CSV); Tier
column populated (small/medium/large); a scan still runs. The ServiceLoader registration
(`META-INF/services/burp.api.montoya.BurpExtension`, added this session) must remain present.

### Part A — pipeline (`promptbreaker-payloads-repo`), Approach B

Add upstream sources to `scripts/sources.yaml` that yield the **thin** categories, then
rebuild. Candidates (final set + real yields determined by a pipeline run; `fetch_and_build.py`
already fails **per-source**, not globally, so a dead/low-yield URL is non-fatal and simply
logged):

- **exfil / indirect-injection**: NVIDIA garak data-leak / XSS-exfil probe modules;
  PayloadsAllTheThings "Prompt Injection" exfil-labeled blocks (already partially ingested —
  widen extraction).
- **override**: garak `promptinject` probes; widen the `spml-chatbot-injections` slice
  (`limit:` is currently 60; SPML already classifies override/exfil).
- **encoding**: pull more of garak `encoding.py`'s templates beyond the current 4.

Each new source declares `name/url/parser/category` per the existing schema; new parsers (if
a source needs one) go in `fetch_and_build.py` alongside the existing `parse_*` functions. All
new payloads flow through the existing **dedupe → classify → tier → quarantine → rank**
stages unchanged, so `risky/` stays excluded from `promptbreaker.csv`.

**Testing (Part A):** extend `scripts/test_fetch_and_build.py` for any new parser; run the
build; assert exfil/override/encoding counts rise in `metadata.json` and that `risky/`
payloads remain absent from `promptbreaker.csv`. Commit regenerated artifacts; the weekly cron
then keeps them fresh.

### Contract / data flow (unchanged shape, fewer sources)

```
external upstreams ──(build time only)──▶ fetch_and_build.py
        │ (weekly cron + manual)                 │
        └── sources.yaml ────────────────────────┘
                                                  ▼
                    promptbreaker.csv  (standalone: prompt,category,tier,model,source_count)
                                                  │  raw GitHub fetch (api + raw.githubusercontent)
                                                  ▼
                    Burp extension  ── reads prompt/category/tier/model ──▶ Prompts tab / Scanner
```

No runtime external calls anywhere in Part B beyond fetching your one CSV.

## Coverage Audit (findings, 2026-09-16)

Current Part A set: **291 unique payloads** (+ 28 built-in defaults in the tool = 319 loaded).

| Category | Payloads | State |
|---|---|---|
| jailbreak | 252 | strong |
| override | 23 | thin |
| exfil | 11 | thin |
| encoding | 5 | intentionally light (runtime transforms cover encoding) |

Provenance: verazuo 178, SPML 60, PayloadsAllTheThings 29, ChatGPT_DAN 17, langgptai 6,
garak 4.

The 8 dropped sources: 2 already in the pipeline (ChatGPT_DAN, verazuo), 1 non-attack
(awesome-chatgpt-prompts), 2 leaked-system-prompt reference corpora (CL4R1T4S, jujumilk3 —
not injection payloads), 3 additive-but-jailbreak (L1B3RT4S, Awesome_GPT_Super_Prompting,
TakSec). Conclusion: **dropping the 8 loses no meaningful attack coverage**; the real gaps
(exfil/override/encoding) are addressed by Approach B instead.

## Risks / open questions

- **Approach B yields are unknown until a pipeline run.** Mitigation: per-source error
  isolation already exists; we keep whatever parses cleanly and log the rest. If a target
  category stays thin after adding sources, that's a reported finding, not a silent failure.
- **Persistence migration:** existing Burp installs have persisted prompts without `tier`/
  `model`. Loading must default them to null, not error.
- **Two-repo change ordering:** ship Part A first (so the CSV carries tier/model — it already
  does) then Part B; the tool tolerates missing columns either way.

## Implementation sequencing

1. Part A: add sources, rebuild, verify counts + quarantine, commit artifacts.
2. Part B: prune `BUILT_IN`, add tier/model to reader + model + UI, rebuild JAR, reload,
   verify in Burp.
3. Final: manual end-to-end test in Burp against the lab endpoint.
