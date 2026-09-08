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
        if "|" in span:
            continue  # markdown table cell artifact
        if span[0].islower() and not any(c in span for c in "(_="):
            continue  # sentence fragment cut off mid-prose, not code-like
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


MODEL_HEADER_MAP = {
    "chatgpt": "openai", "gpt": "openai",
    "claude": "anthropic",
    "gemini": "google", "bard": "google",
    "llama": "meta",
    "grok": "xai",
    "deepseek": "deepseek",
}


def parse_markdown_by_model_sections(raw_text):
    """
    For markdown organized with per-model `##` headings (optionally under
    a `#` top-level heading), extracts payload spans per section using the
    same rules as parse_markdown_code_blocks, tagging each with the model
    named in its own `##` heading (a structural signal from the document,
    not a content guess) and, when the enclosing `#` heading mentions
    "leak", overriding category to "exfil" (system-prompt leaking is a
    data-exfiltration technique regardless of which model it targets).
    """
    sections = []
    current_h1 = None
    current_h2 = None
    current_lines = []

    def flush():
        if current_h2 is not None and current_lines:
            sections.append((current_h1, current_h2, "\n".join(current_lines)))

    for line in raw_text.splitlines():
        h1_match = re.match(r"^#\s+(.*)", line)
        h2_match = re.match(r"^##\s+(.*)", line)
        if h1_match:
            flush()
            current_h1 = h1_match.group(1).strip()
            current_h2 = None
            current_lines = []
            continue
        if h2_match:
            flush()
            current_h2 = h2_match.group(1).strip()
            current_lines = []
            continue
        current_lines.append(line)
    flush()

    results = []
    for h1_title, h2_title, section_text in sections:
        model = None
        lowered_h2 = h2_title.lower()
        for key, tag in MODEL_HEADER_MAP.items():
            if key in lowered_h2:
                model = tag
                break
        category_override = "exfil" if (h1_title and "leak" in h1_title.lower()) else None
        for span in parse_markdown_code_blocks(section_text):
            results.append({"text": span, "model": model, "category": category_override})
    return results


PARSERS = {
    "parse_garak_python_module": parse_garak_python_module,
    "parse_markdown_code_blocks": parse_markdown_code_blocks,
    "parse_json_array": parse_json_array,
    "parse_markdown_by_model_sections": parse_markdown_by_model_sections,
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

# Terms indicating a request to actually PRODUCE harmful artifacts
# (malware, weapons, drugs, abuse material) rather than to test whether a
# guardrail holds. Payloads matching are dropped at ingestion per the
# README exclusion policy. Best-effort keyword heuristic — deliberately
# does NOT catch persona/roleplay/policy-bypass jailbreaks (DAN etc.),
# which are legitimate guardrail tests.
HARMFUL_COMPLIANCE_TERMS = [
    "malware", "ransomware", "keylogger", "spyware", "rootkit", "botnet",
    "trojan horse", "phishing", "phishy",
    "napalm", "explosive", "pipe bomb", "how to make a bomb", "firearm",
    "bioweapon", "chemical weapon", "nerve agent",
    "methamphetamine", "synthesize meth", "cocaine", "fentanyl", "heroin",
    "child sexual", "csam",
]


def is_harmful_compliance(text):
    lowered = text.lower()
    return any(term in lowered for term in HARMFUL_COMPLIANCE_TERMS)


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


def write_tier_files(base_dir, filename_prefix, tiers, header_fields, generated_ts):
    os.makedirs(base_dir, exist_ok=True)
    header_bits = " ".join("%s=%s" % (k, v) for k, v in header_fields.items())
    counts = {}
    for tier, texts in tiers.items():
        texts = sorted(set(texts))
        out_path = os.path.join(base_dir, "%s-%s.txt" % (filename_prefix, tier))
        with open(out_path, "w") as f:
            f.write("# Auto-generated by scripts/fetch_and_build.py - do not edit by hand\n")
            f.write("# %s tier=%s count=%d generated=%s\n" % (header_bits, tier, len(texts), generated_ts))
            for t in texts:
                f.write(t.replace("\n", " ") + "\n")
        counts[tier] = len(texts)
        print("Wrote %d payloads to %s" % (len(texts), out_path))
    return counts


def build_payload_index(seen_fingerprints, fingerprint_sources, fingerprint_models, fp_tier):
    index = []
    for fp, (text, category) in seen_fingerprints.items():
        index.append({
            "text": text,
            "category": category,
            "tier": fp_tier[fp],
            "models": sorted(fingerprint_models.get(fp, set())),
            "source_count": len(fingerprint_sources[fp]),
            "sources": sorted(fingerprint_sources[fp]),
        })
    index.sort(key=lambda entry: (entry["category"], entry["tier"], entry["text"]))
    return index


REGRESSION_DROP_THRESHOLD = 0.5


def check_not_regressed(total_unique, metadata_path):
    if total_unique == 0:
        raise SystemExit("Refusing to write: total_unique_payloads is 0 (all sources failed?)")
    if os.path.exists(metadata_path):
        with open(metadata_path) as f:
            previous = json.load(f)
        previous_total = previous.get("total_unique_payloads", 0)
        if previous_total > 0 and total_unique < previous_total * (1 - REGRESSION_DROP_THRESHOLD):
            raise SystemExit(
                "Refusing to write: total_unique_payloads dropped from %d to %d (>%.0f%% drop)"
                % (previous_total, total_unique, REGRESSION_DROP_THRESHOLD * 100)
            )


def main():
    with open(SOURCES_FILE) as f:
        config = yaml.safe_load(f)

    all_entries = []
    fingerprint_sources = defaultdict(set)
    fingerprint_models = defaultdict(set)
    seen_fingerprints = {}
    errors = []

    for source in config["sources"]:
        entries, err = fetch_source(source)
        if err:
            errors.append(err)
            print("WARNING: %s" % err)
            continue
        print("Fetched %d raw entries from %s" % (len(entries), source["name"]))
        for raw_entry in entries:
            if isinstance(raw_entry, dict):
                text, entry_model, entry_category = raw_entry["text"], raw_entry.get("model"), raw_entry.get("category")
            else:
                text, entry_model, entry_category = raw_entry, None, None
            text = text.strip()
            if not text or len(text) > 2000:
                continue
            if is_harmful_compliance(text):
                continue
            category = entry_category or infer_category(text, source.get("category"))
            model = entry_model or source.get("model")
            fp = fingerprint(text)
            fingerprint_sources[fp].add(source["name"])
            if model:
                fingerprint_models[fp].add(model)
            if fp not in seen_fingerprints:
                seen_fingerprints[fp] = (text, category)

    total_unique = len(seen_fingerprints)
    print("Total unique payloads after dedup: %d" % total_unique)

    check_not_regressed(total_unique, os.path.join(PAYLOADS_DIR, "metadata.json"))

    fp_tier = {}
    for fp, (text, category) in seen_fingerprints.items():
        tokens = estimate_tokens(text)
        fp_tier[fp] = "small" if tokens <= SMALL_MAX_TOKENS else ("medium" if tokens <= MEDIUM_MAX_TOKENS else "large")

    by_category = defaultdict(lambda: defaultdict(list))
    for fp, (text, category) in seen_fingerprints.items():
        by_category[category][fp_tier[fp]].append(text)

    by_model = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
    for fp, models in fingerprint_models.items():
        text, category = seen_fingerprints[fp]
        for model in models:
            by_model[model][category][fp_tier[fp]].append(text)

    ranked_all = sorted(seen_fingerprints.keys(),
                         key=lambda fp: -len(fingerprint_sources[fp]))

    os.makedirs(PAYLOADS_DIR, exist_ok=True)
    metadata = {
        "generated": datetime.now(timezone.utc).isoformat(),
        "total_unique_payloads": total_unique,
        "source_errors": errors,
        "categories": {},
        "models": {},
    }

    for category, tiers in by_category.items():
        cat_dir = os.path.join(PAYLOADS_DIR, category)
        metadata["categories"][category] = write_tier_files(
            cat_dir, category, tiers, {"category": category}, metadata["generated"])

    for model, cats in by_model.items():
        model_meta = {}
        for category, tiers in cats.items():
            cat_dir = os.path.join(PAYLOADS_DIR, "models", model, category)
            filename_prefix = "%s-%s" % (model, category)
            model_meta[category] = write_tier_files(
                cat_dir, filename_prefix, tiers, {"model": model, "category": category}, metadata["generated"])
        metadata["models"][model] = model_meta

    for n in (25, 100, 1000):
        top_fps = ranked_all[:n]
        out_path = os.path.join(PAYLOADS_DIR, "top%d.txt" % n)
        with open(out_path, "w") as f:
            f.write("# Auto-generated - top %d payloads by cross-source agreement\n" % n)
            for fp in top_fps:
                text, category = seen_fingerprints[fp]
                f.write(text.replace("\n", " ") + "\n")
        print("Wrote top%d.txt (%d payloads)" % (n, len(top_fps)))

    payload_index = build_payload_index(seen_fingerprints, fingerprint_sources, fingerprint_models, fp_tier)
    with open(os.path.join(PAYLOADS_DIR, "payload_index.json"), "w") as f:
        json.dump(payload_index, f, indent=2)
    print("Wrote payload_index.json (%d entries)" % len(payload_index))

    with open(os.path.join(PAYLOADS_DIR, "metadata.json"), "w") as f:
        json.dump(metadata, f, indent=2)

    print("Done.")


if __name__ == "__main__":
    main()
