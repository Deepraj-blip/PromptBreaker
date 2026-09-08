# PromptBreaker Payload Lists

SecLists-style tiered prompt-injection payload lists, auto-refreshed from
public LLM security research sources. Consumed directly by the PromptBreaker
Burp extension (raw GitHub URLs), or any other tool that wants a plain
newline-delimited payload list.

## Layout

```
payloads/
  jailbreak/
    jailbreak-top25.txt
    jailbreak-top100.txt
    jailbreak-top1000.txt
    jailbreak-small.txt        # < 15 tokens each, for tight token budgets
    jailbreak-medium.txt       # 15-60 tokens each
    jailbreak-large.txt        # 60+ tokens, multi-step/roleplay scenarios
  exfil/
    exfil-top25.txt
    exfil-top100.txt
    exfil-small.txt
    exfil-medium.txt
  override/
    ...
  encoding/
    ...                        # base64/rot13/unicode-smuggling bypass variants
  metadata.json                 # per-file: source breakdown, token stats, last updated
```

Each `*-topN.txt` file is ranked by a simple heuristic (source diversity +
frequency across source lists) — not a rigorous effectiveness ranking, just
"seen across the most independent sources first." Treat top25/100 as a
quick smoke-test tier and the size-based files (small/medium/large) as the
token-budget-aware tiers for real engagements.

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
