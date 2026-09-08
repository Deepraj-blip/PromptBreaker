#!/usr/bin/env python3
"""
Fetches payloads from configured sources (scripts/sources.yaml), dedupes
them (normalized-text fingerprint - same approach as the Burp extension's
PayloadManager), buckets by length into small/medium/large, ranks
top25/100/1000 by cross-source frequency, and writes the tiered .txt files
plus metadata.json.

Run manually:
    python3 scripts/fetch_and_build.py

Run via cron/GitHub Actions: see .github/workflows/refresh-payloads.yml
"""

import os
import re
import sys
import json
import hashlib
import ast
from collections import defaultdict, Counter
from datetime import datetime, timezone

import yaml
import requests
import tiktoken

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PAYLOADS_DIR = os.path.join(REPO_ROOT, "payloads")
SOURCES_FILE = os.path.join(REPO_ROOT, "scripts", "sources.yaml")

SMALL_MAX_TOKENS = 15
MEDIUM_MAX_TOKENS = 60
# large = anything above medium

TIKTOKEN_ENCODING = "o200k_base"
_TOKEN_ENCODER = tiktoken.get_encoding(TIKTOKEN_ENCODING)


def estimate_tokens(text):
    return max(1, len(_TOKEN_ENCODER.encode(text)))


def fingerprint(text):
    normalized = " ".join(text.strip().lower().split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


# ---------- parsers: one per source "shape" ----------

def parse_garak_python_module(raw_text):
    """
    garak probe modules define prompt lists as Python string-list literals,
    typically assigned to a class attribute like `prompts = [...]`. We parse
    the module as an AST and pull out any top-level or class-level list of
    string literals that looks like a prompt bank, rather than executing
    the module (avoids running arbitrary code from a fetched file).
    """
    results = []
    try:
        tree = ast.parse(raw_text)
    except SyntaxError:
        return results

    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            value = node.value
            if isinstance(value, (ast.List, ast.Tuple)):
                strings = [elt.value for elt in value.elts
                           if isinstance(elt, ast.Constant) and isinstance(elt.value, str)]
                # Heuristic: only treat as a prompt bank if entries look like
                # sentences/phrases (not e.g. short flags or single words)
                strings = [s for s in strings if len(s.split()) >= 3]
                results.extend(strings)
    return results


def parse_markdown_code_blocks(raw_text):
    """
    Extracts actual payload text from a markdown file (e.g. the
    PayloadsAllTheThings README). Payloads there show up in two common
    forms:
      1. inline backtick-quoted spans within a bullet point, e.g.
         * Force output: `Disregard any previous instructions and...`
      2. quoted spans (single/double quotes) within a bullet point, used
         less consistently but still common in this repo's style
    We target both rather than whole bullet lines or fenced code blocks,
    since full lines/blocks pull in headings, table borders, and
    descriptive prose that aren't actual payloads.
    """
    results = []

    # inline `code` spans
    for span in re.findall(r"`([^`\n]{15,500})`", raw_text):
        results.append(span)

    # "quoted" or 'quoted' spans of reasonable payload length
    for span in re.findall(r'"([^"\n]{20,500})"', raw_text):
        results.append(span)
    for span in re.findall(r"'([^'\n]{20,500})'", raw_text):
        results.append(span)

    cleaned = []
    for span in results:
        span = span.strip()
        if span.startswith(("http", "$", "#", "//")):
            continue
        if len(span.split()) < 4:
            continue
        cleaned.append(span)

    # fenced code blocks - useful for multi-line payload templates, but
    # filter aggressively since these often contain shell/markup noise
    for block in re.findall(r"```[a-zA-Z]*\n(.*?)```", raw_text, re.DOTALL):
        for line in block.splitlines():
            line = line.strip()
            words = line.split()
            if (len(words) >= 5
                    and not line.startswith(("$", "#", "//", "curl", "python", "|", "-", "*"))
                    and "http" not in line):
                cleaned.append(line)

    return cleaned


def parse_json_array(raw_text):
    try:
        data = json.loads(raw_text)
    except ValueError:
        return []
    if isinstance(data, list):
        return [str(x) for x in data if isinstance(x, str)]
    return []


PARSERS = {
    "parse_garak_python_module": parse_garak_python_module,
    "parse_markdown_code_blocks": parse_markdown_code_blocks,
    "parse_json_array": parse_json_array,
}


def fetch_source(source):
    try:
        resp = requests.get(source["url"], timeout=20,
                             headers={"User-Agent": "promptbreaker-payload-fetcher/1.0"})
        resp.raise_for_status()
        parser = PARSERS[source["parser"]]
        entries = parser(resp.text)
        return entries, None
    except Exception as e:
        return [], "%s: %s" % (source["name"], str(e))


OVERRIDE_KEYWORDS = [
    "override your instructions", "override the system prompt",
    "new instructions supersede", "system prompt is now",
    "disregard your system prompt", "your new instructions are",
]
JAILBREAK_KEYWORDS = ["ignore", "disregard", "jailbreak", "dan", "developer mode"]
EXFIL_KEYWORDS = ["reveal", "print your", "repeat everything", "leak", "exfiltrate"]
ENCODING_KEYWORDS = ["base64", "rot13", "unicode", "encode"]


def infer_category(text, default_category):
    if default_category:
        return default_category
    lowered = text.lower()
    if any(k in lowered for k in OVERRIDE_KEYWORDS):
        return "override"
    if any(k in lowered for k in JAILBREAK_KEYWORDS):
        return "jailbreak"
    if any(k in lowered for k in EXFIL_KEYWORDS):
        return "exfil"
    if any(k in lowered for k in ENCODING_KEYWORDS):
        return "encoding"
    return "jailbreak"  # safe default bucket


def main():
    with open(SOURCES_FILE) as f:
        config = yaml.safe_load(f)

    all_entries = []  # list of (text, category, source_name)
    fingerprint_sources = defaultdict(set)  # fingerprint -> set of source names (for top-N ranking)
    seen_fingerprints = {}  # fingerprint -> (text, category)
    errors = []

    for source in config["sources"]:
        entries, err = fetch_source(source)
        if err:
            errors.append(err)
            print("WARNING: %s" % err)
            continue
        print("Fetched %d raw entries from %s" % (len(entries), source["name"]))
        for text in entries:
            text = text.strip()
            if not text or len(text) > 2000:  # skip empty/absurdly long outliers
                continue
            category = infer_category(text, source.get("category"))
            fp = fingerprint(text)
            fingerprint_sources[fp].add(source["name"])
            if fp not in seen_fingerprints:
                seen_fingerprints[fp] = (text, category)

    total_unique = len(seen_fingerprints)
    print("Total unique payloads after dedup: %d" % total_unique)

    # bucket by category, then by length tier
    by_category = defaultdict(lambda: defaultdict(list))  # category -> tier -> [text]
    for fp, (text, category) in seen_fingerprints.items():
        tokens = estimate_tokens(text)
        tier = "small" if tokens <= SMALL_MAX_TOKENS else ("medium" if tokens <= MEDIUM_MAX_TOKENS else "large")
        by_category[category][tier].append(text)

    # rank for topN files: more independent sources agreeing = ranked higher
    ranked_all = sorted(seen_fingerprints.keys(),
                         key=lambda fp: -len(fingerprint_sources[fp]))

    os.makedirs(PAYLOADS_DIR, exist_ok=True)
    metadata = {
        "generated": datetime.now(timezone.utc).isoformat(),
        "total_unique_payloads": total_unique,
        "source_errors": errors,
        "categories": {},
    }

    for category, tiers in by_category.items():
        cat_dir = os.path.join(PAYLOADS_DIR, category)
        os.makedirs(cat_dir, exist_ok=True)
        cat_meta = {}
        for tier, texts in tiers.items():
            texts = sorted(set(texts))  # stable order for clean diffs
            out_path = os.path.join(cat_dir, "%s-%s.txt" % (category, tier))
            with open(out_path, "w") as f:
                f.write("# Auto-generated by scripts/fetch_and_build.py - do not edit by hand\n")
                f.write("# category=%s tier=%s count=%d generated=%s\n" % (
                    category, tier, len(texts), metadata["generated"]))
                for t in texts:
                    f.write(t.replace("\n", " ") + "\n")
            cat_meta[tier] = len(texts)
            print("Wrote %d payloads to %s" % (len(texts), out_path))
        metadata["categories"][category] = cat_meta

    # top25 / top100 / top1000 across ALL categories combined, ranked by source agreement
    for n in (25, 100, 1000):
        top_fps = ranked_all[:n]
        out_path = os.path.join(PAYLOADS_DIR, "top%d.txt" % n)
        with open(out_path, "w") as f:
            f.write("# Auto-generated - top %d payloads by cross-source agreement\n" % n)
            for fp in top_fps:
                text, category = seen_fingerprints[fp]
                f.write(text.replace("\n", " ") + "\n")
        print("Wrote top%d.txt (%d payloads)" % (n, len(top_fps)))

    with open(os.path.join(PAYLOADS_DIR, "metadata.json"), "w") as f:
        json.dump(metadata, f, indent=2)

    print("Done.")


if __name__ == "__main__":
    main()
