# Payload List Picker — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a PromptBreaker user pick which named payload lists (category+tier, combined-per-tier, model-specific, top-N) to run, driven by a manifest the pipeline publishes; the scan runs the deduped union of the ticked lists.

**Architecture:** Part A (`promptbreaker-payloads-repo`, Python) emits combined per-tier `.txt` lists plus a `payloads/lists.json` manifest. Part B (`prompt-breaker-tool`, Java/Montoya) gains a per-line reader, parses the manifest, shows a grouped multi-select picker, and fetches the selected list files (path-scoped, `risky/` hard-excluded) into the prompt library.

**Tech Stack:** Python 3.11 (pytest); Java 21 + Gradle 8.10 + Burp Montoya API (compileOnly).

**Spec:** `docs/superpowers/specs/2026-09-16-payload-list-picker-design.md`

## Global Constraints

- `payloads/risky/**` must NEVER appear in the manifest, the combined lists, or any fetch the extension performs. The extension hard-excludes any path containing `risky/` even if the manifest lists one.
- Manifest lives at `payloads/lists.json`; every entry is a real generated non-risky `.txt` file; each `count` equals that file's non-blank, non-`#` line count.
- Preserve existing pipeline sources and `.github/workflows/refresh-payloads.yml`; this change only adds outputs.
- Part B: Java 21; `montoya-api:2026.7` stays compileOnly; the ServiceLoader file `src/main/resources/META-INF/services/burp.api.montoya.BurpExtension` (contents `com.llminjector.LLMInjectorExtension`) must remain in the JAR. No unit-test harness exists; Part B per-task verification is `./gradlew compileJava` / `clean jar` compiling clean.
- Part A commands run from repo root with the committed venv: `venv/bin/python`, `venv/bin/pytest`.
- The tool fetches from GitHub `main`; Part A's outputs only reach Burp after the payloads branch is pushed/merged to `main` (a Finish step, confirmed with the human).

---

## Part A — Pipeline (`promptbreaker-payloads-repo`). Do first.

### Task 1: Emit combined per-tier lists (`payloads/all/all-<tier>.txt`)

**Files:**
- Modify: `scripts/fetch_and_build.py` (`main()`, after the per-category `write_tier_files` loop)
- Modify: `scripts/test_fetch_and_build.py`

**Interfaces:**
- Consumes: existing `write_tier_files(base_dir, filename_prefix, tiers, header_fields, generated_ts)`, `seen_fingerprints`, `fp_tier`, `PAYLOADS_DIR`.
- Produces: `payloads/all/all-{small,medium,large}.txt` (only tiers with content), each the deduped union of all categories' payloads at that tier.

- [ ] **Step 1: Write the failing test**

Add to `scripts/test_fetch_and_build.py`:
```python
def test_combined_tier_union_dedupes_across_categories():
    from fetch_and_build import build_combined_tiers
    seen = {"a": ("jail one", "jailbreak"), "b": ("exfil one", "exfil"),
            "c": ("jail one", "jailbreak")}  # dup text, different fp
    fp_tier = {"a": "small", "b": "small", "c": "medium"}
    combined = build_combined_tiers(seen, fp_tier)
    assert sorted(combined["small"]) == ["exfil one", "jail one"]
    assert combined["medium"] == ["jail one"]
```

- [ ] **Step 2: Run it, verify it fails**

Run: `venv/bin/pytest scripts/test_fetch_and_build.py::test_combined_tier_union_dedupes_across_categories -q`
Expected: FAIL — `ImportError: cannot import name 'build_combined_tiers'`.

- [ ] **Step 3: Implement `build_combined_tiers` and wire it into `main()`**

Add this helper near `write_tier_files` in `scripts/fetch_and_build.py`:
```python
def build_combined_tiers(seen_fingerprints, fp_tier):
    """All categories combined, grouped by tier: {tier: [unique texts]}."""
    combined = defaultdict(list)
    for fp, (text, _category) in seen_fingerprints.items():
        combined[fp_tier[fp]].append(text)
    return {tier: sorted(set(texts)) for tier, texts in combined.items()}
```
In `main()`, immediately after the `for category, tiers in by_category.items():` loop that writes the per-category files, add:
```python
    combined_tiers = build_combined_tiers(seen_fingerprints, fp_tier)
    write_tier_files(os.path.join(PAYLOADS_DIR, "all"), "all", combined_tiers,
                     {"category": "all"}, metadata["generated"])
```
(`write_tier_files` already stamps the `# Auto-generated` header, so `clean_generated_payload_files` will manage these on later runs.)

- [ ] **Step 4: Run the test, verify it passes**

Run: `venv/bin/pytest scripts/test_fetch_and_build.py::test_combined_tier_union_dedupes_across_categories -q`
Expected: PASS.

- [ ] **Step 5: Rebuild and eyeball the output**

Run: `venv/bin/python scripts/fetch_and_build.py && ls payloads/all/ && head -2 payloads/all/all-medium.txt`
Expected: `all-small.txt`, `all-medium.txt`, `all-large.txt` exist; file starts with the `# Auto-generated` header.

- [ ] **Step 6: Full test run + commit**

Run: `venv/bin/pytest scripts/test_fetch_and_build.py -q` → all PASS.
```bash
git add scripts/fetch_and_build.py scripts/test_fetch_and_build.py payloads/
git commit -m "Emit combined per-tier payload lists (payloads/all/all-<tier>.txt)"
```

### Task 2: Publish the `payloads/lists.json` manifest

**Files:**
- Modify: `scripts/fetch_and_build.py` (`main()`, after ALL `.txt` outputs are written)
- Modify: `scripts/test_fetch_and_build.py`

**Interfaces:**
- Consumes: the on-disk generated files under `PAYLOADS_DIR` (produced by Task 1 and existing writers).
- Produces: `payloads/lists.json` = `{"generated": <iso>, "lists": [ {id,name,path,category,tier,model,count,group}, ... ]}`, excluding anything under `risky/`.

- [ ] **Step 1: Write the failing test**

Add to `scripts/test_fetch_and_build.py`:
```python
def test_manifest_entry_from_path_classifies_and_counts(tmp_path):
    from fetch_and_build import manifest_entry_for
    p = tmp_path / "jailbreak-medium.txt"
    p.write_text("# Auto-generated\n# header\npayload one\n\npayload two\n")
    entry = manifest_entry_for(str(p), "payloads/jailbreak/jailbreak-medium.txt")
    assert entry["id"] == "jailbreak-medium"
    assert entry["category"] == "jailbreak" and entry["tier"] == "medium"
    assert entry["model"] is None and entry["group"] == "category"
    assert entry["count"] == 2
    assert entry["name"] == "Jailbreak — Medium"

def test_manifest_excludes_risky(tmp_path):
    from fetch_and_build import build_lists_manifest
    (tmp_path / "jailbreak").mkdir()
    (tmp_path / "jailbreak" / "jailbreak-small.txt").write_text("# Auto-generated\nx\n")
    (tmp_path / "risky").mkdir(); (tmp_path / "risky" / "jailbreak").mkdir()
    (tmp_path / "risky" / "jailbreak" / "jailbreak-risky-small.txt").write_text("# Auto-generated\nbad\n")
    manifest = build_lists_manifest(str(tmp_path), "2026-01-01T00:00:00Z")
    ids = [e["id"] for e in manifest["lists"]]
    assert "jailbreak-small" in ids
    assert all("risky" not in e["path"] for e in manifest["lists"])
```

- [ ] **Step 2: Run them, verify they fail**

Run: `venv/bin/pytest scripts/test_fetch_and_build.py -k manifest -q`
Expected: FAIL — `cannot import name 'manifest_entry_for'` / `'build_lists_manifest'`.

- [ ] **Step 3: Implement the manifest builder and wire it into `main()`**

Add to `scripts/fetch_and_build.py`:
```python
_CATEGORY_DISPLAY = {"jailbreak": "Jailbreak", "exfil": "Exfil",
                     "override": "Override", "encoding": "Encoding",
                     "all": "All categories"}

def _count_payload_lines(abs_path):
    n = 0
    with open(abs_path) as f:
        for line in f:
            s = line.strip()
            if s and not s.startswith("#"):
                n += 1
    return n

def manifest_entry_for(abs_path, rel_path):
    """Classify one generated .txt (rel_path is repo-relative) into a manifest entry, or None to skip."""
    parts = rel_path.split("/")            # e.g. payloads/jailbreak/jailbreak-medium.txt
    fname = parts[-1]
    list_id = fname[:-4] if fname.endswith(".txt") else fname
    count = _count_payload_lines(abs_path)
    tier = None
    for t in ("small", "medium", "large"):
        if list_id.endswith("-" + t):
            tier = t
            break
    if parts[1] == "models" and len(parts) == 5:      # payloads/models/<model>/<cat>/<file>
        model, category = parts[2], parts[3]
        name = "%s · %s — %s" % (model.upper() if model in ("openai",) else model.capitalize(),
                                 _CATEGORY_DISPLAY.get(category, category.capitalize()),
                                 tier.capitalize() if tier else "")
        return {"id": list_id, "name": name.strip(" —"), "path": rel_path,
                "category": category, "tier": tier, "model": model, "count": count, "group": "model"}
    if parts[1] == "all":                              # payloads/all/all-<tier>.txt
        name = "All categories — %s" % (tier.capitalize() if tier else "")
        return {"id": list_id, "name": name.strip(" —"), "path": rel_path,
                "category": None, "tier": tier, "model": None, "count": count, "group": "combined"}
    if fname.startswith("top") and fname[3:-4].isdigit():   # payloads/top<N>.txt
        n = fname[3:-4]
        return {"id": list_id, "name": "Top %s (ranked by cross-source agreement)" % n,
                "path": rel_path, "category": None, "tier": None, "model": None,
                "count": count, "group": "ranked"}
    if len(parts) == 3 and tier:                        # payloads/<cat>/<cat>-<tier>.txt
        category = parts[1]
        name = "%s — %s" % (_CATEGORY_DISPLAY.get(category, category.capitalize()),
                            tier.capitalize())
        return {"id": list_id, "name": name, "path": rel_path, "category": category,
                "tier": tier, "model": None, "count": count, "group": "category"}
    return None

def build_lists_manifest(payloads_dir, generated_ts):
    lists = []
    for root, _dirs, files in os.walk(payloads_dir):
        if os.sep + "risky" in os.sep + os.path.relpath(root, payloads_dir):
            continue
        for name in sorted(files):
            if not name.endswith(".txt"):
                continue
            abs_path = os.path.join(root, name)
            rel_path = os.path.join("payloads", os.path.relpath(abs_path, payloads_dir)).replace(os.sep, "/")
            entry = manifest_entry_for(abs_path, rel_path)
            if entry and entry["count"] > 0:
                lists.append(entry)
    order = {"category": 0, "combined": 1, "model": 2, "ranked": 3}
    lists.sort(key=lambda e: (order[e["group"]], e["path"]))
    return {"generated": generated_ts, "lists": lists}
```
In `main()`, AFTER `metadata.json` is written and BEFORE `prune_empty_dirs`, add:
```python
    manifest = build_lists_manifest(PAYLOADS_DIR, metadata["generated"])
    with open(os.path.join(PAYLOADS_DIR, "lists.json"), "w") as f:
        json.dump(manifest, f, indent=2)
    print("Wrote lists.json (%d selectable lists)" % len(manifest["lists"]))
```
(The `risky/` skip uses a path check; `manifest_entry_for` returns None for anything that doesn't match a known shape, so unexpected files are ignored, not mis-listed.)

- [ ] **Step 4: Run the manifest tests, verify they pass**

Run: `venv/bin/pytest scripts/test_fetch_and_build.py -k manifest -q`
Expected: PASS.

- [ ] **Step 5: Rebuild and verify the real manifest**

Run: `venv/bin/python scripts/fetch_and_build.py && venv/bin/python -c "import json;m=json.load(open('payloads/lists.json'));print(len(m['lists']),'lists');import collections;print(collections.Counter(e['group'] for e in m['lists']));print('risky present:', any('risky' in e['path'] for e in m['lists']))"`
Expected: a list count > 0, all four groups represented (category/combined/model/ranked), `risky present: False`.

- [ ] **Step 6: Full test run + commit**

Run: `venv/bin/pytest scripts/test_fetch_and_build.py -q` → all PASS.
```bash
git add scripts/fetch_and_build.py scripts/test_fetch_and_build.py payloads/
git commit -m "Publish payloads/lists.json manifest of selectable lists"
```

---

## Part B — Extension (`prompt-breaker-tool`). Do after Part A. Paths relative to `prompt-breaker-tool/`.

### Task 3: Add a per-line extraction mode

**Files:**
- Modify: `src/main/java/com/llminjector/fetch/PromptSource.java` (enum)
- Modify: `src/main/java/com/llminjector/fetch/SourceFetcher.java`

**Interfaces:**
- Produces: `PromptSource.Extraction.PER_LINE`; `SourceFetcher` handles it, emitting one `Prompt` per non-blank, non-`#` line.

- [ ] **Step 1: Add the enum constant**

In `PromptSource.java`, change `public static enum Extraction { WHOLE_FILE, CODE_BLOCKS, CSV; }` to include `PER_LINE`:
```java
    public static enum Extraction {
        WHOLE_FILE,
        CODE_BLOCKS,
        CSV,
        PER_LINE;
    }
```

- [ ] **Step 2: Handle PER_LINE in `SourceFetcher`**

In `SourceFetcher.fetch`, the file-type gate currently is `boolean bl = promptSource.extraction() == PromptSource.Extraction.CSV;` and picks `.csv` vs text files. PER_LINE reads `.txt`, which `isTextFile` already accepts, so no gate change is needed. In the `switch (promptSource.extraction())` block add a case:
```java
                    case PER_LINE: {
                        this.addPerLine(arrayList, promptSource, blob.path(), string2);
                        break;
                    }
```
Add the method (near `addCsv`):
```java
    private void addPerLine(List<Prompt> out, PromptSource src, String path, String raw) {
        int n = 0;
        for (String line : raw.split("\n")) {
            String p = line.strip();
            if (p.isEmpty() || p.startsWith("#")) {
                continue;
            }
            out.add(new Prompt(labelFor(path) + " #" + (++n), p, src.category(),
                    src.repo().toLowerCase() + ":" + path));
        }
    }
```

- [ ] **Step 3: Compile**

Run: `./gradlew compileJava`
Expected: BUILD SUCCESSFUL.

- [ ] **Step 4: Commit**

```bash
git add src/main/java/com/llminjector/fetch/PromptSource.java src/main/java/com/llminjector/fetch/SourceFetcher.java
git commit -m "Add PER_LINE extraction for newline-delimited payload lists"
```

### Task 4: Manifest model + parser

**Files:**
- Create: `src/main/java/com/llminjector/fetch/PayloadList.java`
- Create: `src/main/java/com/llminjector/fetch/ListsManifest.java`

**Interfaces:**
- Consumes: `com.llminjector.core.json.Json` (`Json.parse(String)`, `Node.get`, `Node.items`, `Node.asString`).
- Produces:
  - `record PayloadList(String id, String name, String path, String category, String tier, String model, int count, String group)`
  - `ListsManifest.parse(String json) -> List<PayloadList>` (skips any entry whose `path` contains `risky/`).

- [ ] **Step 1: Create `PayloadList`**

```java
package com.llminjector.fetch;

public record PayloadList(String id, String name, String path, String category,
                          String tier, String model, int count, String group) {
}
```

- [ ] **Step 2: Create `ListsManifest` parser**

```java
package com.llminjector.fetch;

import com.llminjector.core.json.Json;
import java.util.ArrayList;
import java.util.List;

public final class ListsManifest {
    private ListsManifest() {
    }

    public static List<PayloadList> parse(String json) {
        List<PayloadList> out = new ArrayList<>();
        Json.Node root = Json.parse(json);
        Json.Node lists = root.get("lists");
        if (lists == null || !lists.isArray()) {
            return out;
        }
        for (Json.Node n : lists.items()) {
            String path = str(n, "path");
            if (path == null || path.contains("risky/")) {   // hard exclusion
                continue;
            }
            out.add(new PayloadList(str(n, "id"), str(n, "name"), path,
                    str(n, "category"), str(n, "tier"), str(n, "model"),
                    intOf(n, "count"), str(n, "group")));
        }
        return out;
    }

    private static String str(Json.Node node, String key) {
        Json.Node v = node.get(key);
        return v != null && v.isString() ? v.asString() : null;
    }

    private static int intOf(Json.Node node, String key) {
        Json.Node v = node.get(key);
        try {
            return v == null ? 0 : Integer.parseInt(v.asString().trim());
        } catch (RuntimeException e) {
            return 0;
        }
    }
}
```
(If `Json.Node.asString()` throws for a numeric node rather than returning its digits, catch it — `intOf` already does. Verify against `Json.Node` while implementing; if numbers are stored via `Node.number(String)`, `asString()` returns the digits.)

- [ ] **Step 3: Compile**

Run: `./gradlew compileJava`
Expected: BUILD SUCCESSFUL.

- [ ] **Step 4: Commit**

```bash
git add src/main/java/com/llminjector/fetch/PayloadList.java src/main/java/com/llminjector/fetch/ListsManifest.java
git commit -m "Add payload-list manifest model and parser"
```

### Task 5: Persist selected list ids in `ScanConfig`

**Files:**
- Modify: `src/main/java/com/llminjector/scan/ScanConfig.java`
- Modify: `src/main/java/com/llminjector/burpglue/ExtensionState.java` (config (de)serialization)

**Interfaces:**
- Produces: `ScanConfig.selectedListIds` (`List<String>`, default `["all-medium"]`), persisted and reloaded.

- [ ] **Step 1: Add the field**

In `ScanConfig.java`, after `autoFetchDaily`, add:
```java
    public List<String> selectedListIds = new ArrayList<>(List.of("all-medium"));
```
Ensure `java.util.ArrayList` is imported (it is used elsewhere; add the import if missing).

- [ ] **Step 2: Persist it**

In `ExtensionState.java`, find where scalar config fields are written and read (near `autoFetchDaily`, e.g. `node.put("autoFetchDaily", ...)` and `ExtensionState.boolVal(node, "autoFetchDaily", ...)`). Add a string-array write and read for `selectedListIds`, following the pattern already used for the other `List<String>` config fields (`bodyFields`/`endpointPatterns`) in the same file — locate that existing pattern and mirror it exactly for `selectedListIds` (write as a JSON array of strings; read back into a new `ArrayList<>`, leaving the default when the key is absent).

- [ ] **Step 3: Compile**

Run: `./gradlew compileJava`
Expected: BUILD SUCCESSFUL.

- [ ] **Step 4: Commit**

```bash
git add src/main/java/com/llminjector/scan/ScanConfig.java src/main/java/com/llminjector/burpglue/ExtensionState.java
git commit -m "Persist selected payload-list ids in ScanConfig"
```

### Task 6: Manifest-driven multi-select picker + union fetch

**Files:**
- Modify: `src/main/java/com/llminjector/ui/PromptsTab.java`

**Interfaces:**
- Consumes: `GitHubApi.fetchRaw(owner, repo, branch, path)`, `ListsManifest.parse`, `PayloadList`, `PromptSource.promptBreaker()`, existing `addUnique(List<Prompt>)` / `refresh()` / `ctx.persist()`, and the new `Extraction.PER_LINE`.
- Produces: replaces the single-source combo + "Fetch from GitHub (selected)" flow with: a **"Load lists…"** action that fetches `payloads/lists.json` from the PromptBreaker repo, shows a grouped multi-select dialog (checkboxes grouped by `group`: category / combined / model / ranked; each row `name (count)`; pre-checked from `ScanConfig.selectedListIds`), and on OK fetches each ticked list's `path` via a `PER_LINE` `PromptSource` and merges the deduped union into the library.

- [ ] **Step 1: Add a helper that fetches the manifest**

In `PromptsTab.java` add:
```java
    private java.util.List<PayloadList> fetchManifest() throws Exception {
        PromptSource src = PromptSource.promptBreaker();
        String json = new GitHubApi("").fetchRaw(src.owner(), src.repo(), src.branch(), "payloads/lists.json");
        return ListsManifest.parse(json);
    }
```

- [ ] **Step 2: Add the union-fetch for selected ids**

```java
    private void fetchSelectedLists(java.util.List<PayloadList> manifest, java.util.Set<String> ids) {
        PromptSource base = PromptSource.promptBreaker();
        this.ctx.log.accept("Fetching " + ids.size() + " list(s)…");
        new Thread(() -> {
            GitHubApi api = new GitHubApi("");
            SourceFetcher fetcher = new SourceFetcher(api);
            java.util.ArrayList<Prompt> collected = new java.util.ArrayList<>();
            for (PayloadList pl : manifest) {
                if (!ids.contains(pl.id()) || pl.path().contains("risky/")) {
                    continue;
                }
                PromptSource listSrc = new PromptSource(pl.name(), base.owner(), base.repo(),
                        base.branch(), pl.category(), PromptSource.Extraction.PER_LINE, "prompt");
                try {
                    java.util.List<Prompt> got = fetcher.fetchSingle(listSrc, pl.path(), this.ctx.log);
                    for (Prompt p : got) {
                        p.setTier(pl.tier());
                        p.setModel(pl.model());
                    }
                    collected.addAll(got);
                } catch (Exception e) {
                    this.ctx.log.accept(pl.id() + ": FAILED — " + e.getMessage());
                }
            }
            SwingUtilities.invokeLater(() -> {
                int added = this.addUnique(collected);
                this.ctx.state.config.selectedListIds = new java.util.ArrayList<>(ids);
                this.refresh();
                this.ctx.persist();
                JOptionPane.showMessageDialog(this, "Loaded " + collected.size()
                        + " payloads from " + ids.size() + " list(s) (" + added + " new after de-dup).");
            });
        }, "promptbreaker-lists").start();
    }
```

- [ ] **Step 3: Add `fetchSingle` to `SourceFetcher`** (fetch ONE path, not the whole tree)

In `SourceFetcher.java`:
```java
    public java.util.List<Prompt> fetchSingle(PromptSource src, String path, java.util.function.Consumer<String> log) throws Exception {
        java.util.ArrayList<Prompt> out = new java.util.ArrayList<>();
        String raw = this.api.fetchRaw(src.owner(), src.repo(), src.branch(), path);
        switch (src.extraction()) {
            case PER_LINE: this.addPerLine(out, src, path, raw); break;
            case CSV: this.addCsv(out, src, path, raw); break;
            case WHOLE_FILE: this.addWholeFile(out, src, path, raw); break;
            case CODE_BLOCKS: this.addCodeBlocks(out, src, path, raw); break;
        }
        log.accept(src.label() + ": " + out.size() + " payloads.");
        return out;
    }
```
(Commit this with Task 6 since Task 6 is its only caller.)

- [ ] **Step 4: Build the grouped multi-select dialog and wire a "Load lists…" button**

Replace the `sourceBox` + `fetchBtn` ("Fetch from GitHub (selected)") wiring in `buildButtons`/`fetchRow` with a single **"Load lists…"** button (keep `fetchAllBtn`/`urlBtn`/`applyGithubFetchEnabled` gating behavior — the new button obeys the same `enableGithubFetch` gate). On click, off-thread call `fetchManifest()`, then on the EDT show a modal dialog:
  - A scrollable panel of `JCheckBox` rows, **grouped** with a bold `Theme.label` header per `group` in order category → combined → model → ranked; each checkbox text is `pl.name() + " (" + pl.count() + ")"`; checked when `ScanConfig.selectedListIds.contains(pl.id())`.
  - OK / Cancel. On OK, collect the checked `PayloadList.id()`s into a `Set<String>` and call `fetchSelectedLists(manifest, ids)`. On empty manifest, show "No lists found — is lists.json published on the source branch?".

Follow the existing Swing idioms in this file (`Theme.button`, `Theme.scroll`, `JOptionPane.showConfirmDialog` with a custom panel, `SwingUtilities.invokeLater`). This is the one design-involved step; keep the dialog construction in a private helper `showListPicker(java.util.List<PayloadList> manifest)`.

- [ ] **Step 5: Build the JAR and verify the ServiceLoader file**

Run:
```bash
./gradlew clean jar
unzip -p build/libs/prompt-breaker-ui-update.jar META-INF/services/burp.api.montoya.BurpExtension
```
Expected: BUILD SUCCESSFUL; prints `com.llminjector.LLMInjectorExtension`.

- [ ] **Step 6: Commit**

```bash
git add src/main/java/com/llminjector/ui/PromptsTab.java src/main/java/com/llminjector/fetch/SourceFetcher.java
git commit -m "Add manifest-driven multi-select payload-list picker"
```

### Task 7: AutoFetcher refreshes via the manifest (default all-medium)

**Files:**
- Modify: `src/main/java/com/llminjector/fetch/AutoFetcher.java`

**Interfaces:**
- Consumes: `fetchManifest`-equivalent logic (manifest fetch + `fetchSingle` per selected id), `ScanConfig.selectedListIds`.
- Produces: the daily refresh loads the union of `selectedListIds` (or `all-medium` if empty) instead of the whole CSV.

- [ ] **Step 1: Point the daily refresh at the selected lists**

In `AutoFetcher.runOnce`, replace the `new SourceFetcher(...).fetch(source, log)` call (which fetches the whole CSV) with: fetch `payloads/lists.json` via `GitHubApi.fetchRaw`, `ListsManifest.parse` it, pick the entries whose `id` is in `ctx.state.config.selectedListIds` (default to the single `all-medium` entry when the config list is empty or none match), and for each call `fetcher.fetchSingle(listSrc, entry.path(), log)` building a `PER_LINE` `PromptSource` off `PromptSource.promptBreaker()` (same as Task 6 Step 2). Keep the existing `merge(...)` call and the `prefix`/enabled-preservation logic; the `prefix` stays `source.repo().toLowerCase() + ":"` so previously-fetched PromptBreaker prompts are replaced. Preserve the `autoFetchDaily`/`MIN_AGE_MS`/`PLACEHOLDER_OWNER` guards unchanged.

- [ ] **Step 2: Compile**

Run: `./gradlew compileJava`
Expected: BUILD SUCCESSFUL.

- [ ] **Step 3: Commit**

```bash
git add src/main/java/com/llminjector/fetch/AutoFetcher.java
git commit -m "Auto-fetch refreshes selected lists via manifest (default all-medium)"
```

### Task 8: Manual end-to-end verification in Burp

No code changes — acceptance gate. Requires Burp and Part A pushed to `main` (Finish step).

- [ ] **Step 1: Load the freshly built JAR** (Extensions → remove old → Add → Java → `build/libs/prompt-breaker-ui-update.jar`); Errors tab empty; PromptBreaker tab present.
- [ ] **Step 2:** Config → enable "fetch prompts from GitHub / URLs". Prompts tab → **Load lists…** → the dialog shows grouped, named lists with counts (Category, Combined, Model, Ranked). No `risky` entry appears.
- [ ] **Step 3:** Tick two lists (e.g. "Jailbreak — Medium" + "All categories — Small"), OK. The library loads their deduped union; Tier column populated; heading count matches.
- [ ] **Step 4:** Run the lab-endpoint scan; confirm Results/History populate. Screenshot into `UI images/` and report.

---

## Self-Review

**Spec coverage:**
- Manifest (`lists.json`) → Task 2. ✓
- Combined per-tier lists → Task 1. ✓
- Per-line reader → Task 3. ✓
- Manifest model/parse with risky exclusion → Task 4 (+ hard exclusion again in Tasks 6/`fetchSelectedLists`). ✓
- Multi-select grouped picker + union fetch → Task 6. ✓
- selectedListIds persistence → Task 5. ✓
- AutoFetcher default all-medium → Task 7 (+ ScanConfig default in Task 5). ✓
- risky/ never listed or fetched → Task 2 (`build_lists_manifest` skip), Task 4 (`ListsManifest.parse` skip), Task 6 (`fetchSelectedLists` skip). ✓
- ServiceLoader retained → Task 6 Step 5 asserts. ✓
- Prior tier/model fields reused → Task 6 Step 2 sets them. ✓

**Placeholder scan:** Task 5 Step 2 and Task 6 Step 4 / Task 7 Step 1 describe UI/persistence work by pointing at an exact existing pattern in the same file rather than pasting full code — justified because the correct code is "mirror the sibling `List<String>` field" / "follow this file's Swing idioms," and the interfaces (method names, the grouping, the default) are fully specified. All algorithmic/parsing code is given in full.

**Type consistency:** `PayloadList` accessors (`id/name/path/category/tier/model/count/group`) used identically in `ListsManifest`, `fetchSelectedLists`, and the picker. `fetchSingle(PromptSource, String, Consumer<String>)` defined in Task 6 Step 3, called in Task 6 Step 2 and Task 7. `Extraction.PER_LINE` defined in Task 3 before use. `selectedListIds` defined in Task 5 before use in Tasks 6/7. `build_combined_tiers`/`manifest_entry_for`/`build_lists_manifest` defined before their `main()` wiring.
