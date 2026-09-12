# PromptBreaker Payload Lists

SecLists-style tiered prompt-injection payload lists, auto-refreshed from
public LLM security research sources. Consumed directly by the PromptBreaker
Burp extension (raw GitHub URLs), or any other tool that wants a plain
newline-delimited payload list.

## Layout

```
payloads/
  jailbreak/
    jailbreak-small.txt        # < 15 tokens each, for tight token budgets
    jailbreak-medium.txt       # 15-60 tokens each
    jailbreak-large.txt        # 60+ tokens, multi-step/roleplay scenarios
  exfil/
    exfil-small.txt / -medium.txt / -large.txt
  override/
    override-small.txt / -medium.txt / -large.txt
                                # not yet populated (no current source
                                # yields override-category content)
  encoding/
    encoding-small.txt / -medium.txt / -large.txt
                                # "decode-and-execute this" style templates
                                # from upstream sources (garak etc.);
                                # runtime encoding transforms are the Burp
                                # extension's job, not baked in here.
                                # Upstream {placeholder} tokens are rendered
                                # to concrete example values at build time so
                                # each entry is a usable standalone payload
  risky/
    <category>/
      <category>-risky-<tier>.txt
                                # QUARANTINED harmful-compliance payloads
                                # (malware/weapons/etc.). Kept for authorized
                                # engagements but excluded from the generic
                                # category files, top-N rankings, and
                                # promptbreaker.csv. Every file carries a loud
                                # HANDLE-WITH-PRECAUTION banner. Opt in by
                                # pointing your tool at these files directly
  models/
    <model>/
      <category>/
        <model>-<category>-<tier>.txt
                                # only created where real data exists;
                                # e.g. payloads/models/openai/jailbreak/
                                # openai-jailbreak-small.txt
                                # a payload here ALSO appears in the
                                # matching generic payloads/<category>/
                                # file -- model-specific files are always
                                # additive precision, never a fork
  payload_index.json           # one entry per unique payload: category,
                                # tier, models, source_count (confidence
                                # proxy = independent-source agreement),
                                # sources. Additive -- the plain .txt
                                # files are unaffected.
  metadata.json                # per-category and per-model: tier counts,
                                # source errors, total unique payloads
  top25.txt / top100.txt / top1000.txt
                                # global, category- and model-agnostic,
                                # ranked by cross-source agreement
```

Each `*-topN.txt` file is ranked by a simple heuristic (source diversity +
frequency across source lists) — not a rigorous effectiveness ranking, just
"seen across the most independent sources first." Treat top25/100 as a
quick smoke-test tier and the size-based files (small/medium/large) as the
token-budget-aware tiers for real engagements.

Token counts (used for small/medium/large tiering) are measured with
`tiktoken`'s `o200k_base` encoding as a consistent baseline across every
payload. This is an approximation, not an exact per-model count -- Claude,
Llama, and others use different tokenizers -- but it's a real measurement
rather than a word-count guess, and the goal is a consistent basis for
comparison, not perfect accuracy for any one model.

One payload per line. Blank lines and lines starting with `#` are ignored.

## Sources aggregated (see scripts/sources.yaml)

- PayloadsAllTheThings / Prompt Injection (markdown)
- verazuo/jailbreak_llms — "In-The-Wild Jailbreak Prompts" dataset, CCS'24
  (CSV; capped via `limit:` to keep the list curated)
- 0xk1h0/ChatGPT_DAN — DAN / persona jailbreak prompts (markdown)
- NVIDIA garak encoding probes (Python module → encoding templates)
- langgptai/LLM-Jailbreaks — per-model sectioned jailbreak prompts (markdown)
- (add more in `scripts/sources.yaml` — see below)

Sources are only kept when they actually yield parseable payloads. Two were
removed for yielding nothing usable: garak's `continuation.py` (builds prompts
at runtime from an external data file, and its content is hate-speech
completion, not prompt injection) and the OWASP LLM01 markdown (explanatory
prose, not a payload bank). A source that stops resolving should be fixed or
removed rather than left silently returning zero.

Harmful-compliance payloads — prompts tuned to elicit actually harmful
compliance (malware, weapons, etc.) rather than to test whether a guardrail
holds — are detected at ingestion (`is_harmful_compliance()`) and
**quarantined** into `payloads/risky/`, not silently dropped. They stay out
of the generic category files, the top-N rankings, and `promptbreaker.csv`,
so nothing pulls them in by default; each risky file carries a loud
authorized-use-only banner. Point your tool at those files explicitly when
an authorized engagement calls for them. You can also hand-curate entries
into `payloads/manual/` — those are never auto-fetched or auto-cleaned.

## Model-specific tagging

`scripts/sources.yaml` sources may optionally set a `model:` field
(e.g. `model: openai`) when the *entire source* is fundamentally about
one model. There's no keyword-based inference from payload text --
guessing "this payload mentions Claude, so tag it Claude" produces false
precision, which is worse for an engagement than an honestly-generic
payload.

For markdown documents that are themselves organized per-model (e.g. `##
ChatGPT`, `## Claude` headings), use `parser:
parse_markdown_by_model_sections` instead of `parse_markdown_code_blocks`
-- it tags each entry with the model named in its own section heading (a
structural signal from the document's author), and treats a top-level
heading containing "leak" as a system-prompt-leaking section, overriding
that section's category to `exfil` regardless of model.

A model-tagged payload always populates both its generic
`payloads/<category>/` file and its `payloads/models/<model>/<category>/`
file.

## Updating

Runs automatically via `.github/workflows/refresh-payloads.yml` (weekly,
Sundays 03:00 UTC). To run manually:

```bash
pip install -r scripts/requirements.txt
python3 scripts/fetch_and_build.py
```

This fetches all configured sources, deduplicates (normalized-text
fingerprint, same algorithm as the Burp extension's PayloadManager), buckets
by length into small/medium/large, ranks top25/100/1000, and writes/commits
the updated .txt files plus `metadata.json`.

## Encoding category

The `encoding` category holds "decode this and act on it" style payloads
sourced from upstream research (e.g. garak's encoding probes). It does NOT
contain mechanically-encoded variants of other payloads: wrapping a
payload in base64/ROT13/homoglyph/leetspeak at request time is the
PromptBreaker Burp extension's job, so baking those variants into these
static lists would be redundant.
