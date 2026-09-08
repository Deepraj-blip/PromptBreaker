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
                                # extension's job, not baked in here
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

## Sources aggregated (see scripts/fetch_and_build.py)

- NVIDIA garak probe modules (dan.py, encoding.py, continuation.py, etc.)
- PayloadsAllTheThings / Prompt Injection
- OWASP LLM Top 10 example set
- (add more in `scripts/sources.yaml` — see below)

Deliberately NOT included by default: raw "liberation"/jailbreak archives
whose prompts are tuned to elicit actually harmful compliance (malware,
weapons, etc.) rather than to test whether a guardrail holds. If you want
to hand-curate specific entries from sources like that for an authorized
engagement, add them manually to `payloads/manual/` — they won't be
auto-fetched.

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
