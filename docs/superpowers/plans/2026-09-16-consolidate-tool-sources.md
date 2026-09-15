# Consolidate Tool Payload Sources + Expand Thin Categories — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Burp extension consume only PromptBreaker's own aggregated `promptbreaker.csv` (surfacing its `tier`/`model` columns), and expand the pipeline to strengthen the thin exfil/override categories — with the pipeline's external harvesting and weekly refresh preserved.

**Architecture:** Two repos. Part A (`promptbreaker-payloads-repo`, Python) harvests upstream sources at build time into a standalone `promptbreaker.csv`. Part B (`prompt-breaker-tool`, Java/Montoya) fetches that one CSV at runtime. This change deepens Part A's override/exfil coverage and prunes Part B's 8 redundant runtime sources.

**Tech Stack:** Python 3.11 (requests, pyyaml, tiktoken, pytest); Java 21 + Gradle 8.10 + Burp Montoya API (compileOnly).

**Spec:** `docs/superpowers/specs/2026-09-16-consolidate-tool-sources-design.md`

## Global Constraints

- Part A ships a **standalone** `promptbreaker.csv` — no runtime external fetch by the tool or the CSV. External harvesting lives only in `scripts/fetch_and_build.py` (build time).
- **`payloads/risky/**` must never enter `promptbreaker.csv`** — quarantine is enforced by the existing pipeline; every Part A change must re-verify it holds.
- Preserve every currently-working source in `scripts/sources.yaml` and the weekly cron `.github/workflows/refresh-payloads.yml`. This change only *adds* sources.
- Part B: Java 21 toolchain; `montoya-api:2026.7` stays `compileOnly`; the ServiceLoader file `src/main/resources/META-INF/services/burp.api.montoya.BurpExtension` (contents: `com.llminjector.LLMInjectorExtension`) must remain in the JAR.
- Part B has **no unit-test harness** (no JUnit, no `src/test`). Its per-task verification is `./gradlew compileJava` (compiles clean) plus the final manual Burp reload in Task 6. Do not add a test framework as part of this plan.
- Part A commands run from the repo root using the committed venv: `venv/bin/python`, `venv/bin/pytest`.

---

## Part A — Pipeline (`promptbreaker-payloads-repo`). Do this repo first.

### Task 1: Strengthen override/exfil by widening the SPML slice

SPML (`spml-chatbot-injections`) is the pipeline's dedicated override/exfil source, currently capped at `limit: 60`. Raising the cap deepens the two thin categories through a source whose parser + classification already work.

**Files:**
- Modify: `scripts/sources.yaml` (the `spml-chatbot-injections` entry, `limit:` field)
- Regenerated (by the build): `payloads/promptbreaker.csv`, `payloads/**/*.txt`, `payloads/payload_index.json`, `payloads/metadata.json`

**Interfaces:**
- Consumes: existing `parse_spml_injections`, `infer_category`, quarantine (`is_harmful_compliance`), and output writers — all unchanged.
- Produces: a rebuilt payload set with higher override/exfil counts.

- [ ] **Step 1: Record the baseline counts**

Run:
```bash
cd promptbreaker-payloads-repo
venv/bin/python -c "import json;m=json.load(open('payloads/metadata.json'));c=m['categories'];print('override',sum(c.get('override',{}).values()),'exfil',sum(c.get('exfil',{}).values()),'total',m['total_unique_payloads'])"
```
Expected (2026-09-13 baseline): `override 23 exfil 11 total 291`. Note the actual numbers you see.

- [ ] **Step 2: Raise the SPML cap**

In `scripts/sources.yaml`, in the `- name: spml-chatbot-injections` block, change:
```yaml
    limit: 60
```
to:
```yaml
    limit: 150
```
Leave every other field and every other source untouched.

- [ ] **Step 3: Rebuild the payload set**

Run: `venv/bin/python scripts/fetch_and_build.py`
Expected: it prints per-source `Fetched N raw entries from spml-chatbot-injections` with N > 60, finishes without a non-zero exit, and reports no `source_errors`. If the network is unavailable it will log per-source errors — in that case stop and report; do not commit a half-built set.

- [ ] **Step 4: Verify counts rose and quarantine held**

Run:
```bash
venv/bin/python -c "import json;m=json.load(open('payloads/metadata.json'));c=m['categories'];print('override',sum(c.get('override',{}).values()),'exfil',sum(c.get('exfil',{}).values()),'total',m['total_unique_payloads'])"
venv/bin/python -c "import csv;rows=list(csv.DictReader(open('payloads/promptbreaker.csv')));import glob;risky=set();[risky.update(open(f).read().split(chr(10))) for f in glob.glob('payloads/risky/**/*.txt',recursive=True)];bad=[r for r in rows if r['prompt'] in risky and r['prompt'].strip()];print('risky payloads leaked into CSV:',len(bad))"
```
Expected: override and/or exfil are **higher** than the Step 1 baseline; `risky payloads leaked into CSV: 0`.

- [ ] **Step 5: Run the test suite**

Run: `venv/bin/pytest scripts/test_fetch_and_build.py -q`
Expected: all tests PASS (no test asserts the old count, so widening the slice must not break any).

- [ ] **Step 6: Commit**

```bash
git add scripts/sources.yaml payloads/
git commit -m "Widen SPML slice to strengthen override/exfil categories"
```

---

### Task 2: Add a verified independent source for exfil/override

Task 1 deepens one synthetic source; this adds an *independent* source so cross-source agreement (`source_count`) and diversity improve. Per this repo's convention, an upstream URL is verified against the live repo at build time and **removed if it yields nothing** (sources.yaml already documents this "fail loudly / drop dead sources" rule).

**Files:**
- Modify: `scripts/sources.yaml` (append one source block)
- Modify: `README.md` ("Sources aggregated" list — only if the source is kept)
- Regenerated: the `payloads/**` artifacts

**Interfaces:**
- Consumes: an existing parser (`parse_markdown_code_blocks` for a markdown payload bank, or `parse_garak_python_module` for a garak probe module). No new parser unless a kept source needs one.
- Produces: additional deduped exfil/override payloads in the rebuilt set.

- [ ] **Step 1: Add one candidate source**

Append to the `sources:` list in `scripts/sources.yaml` (try this candidate first — garak's XSS/exfil probe module, which yields data-exfiltration "print/echo/leak" style payloads via the existing garak parser):
```yaml
  # NVIDIA garak XSS/data-exfiltration probes — markup/echo payloads that try
  # to make the model emit attacker-controlled or leaked content. Exfil-leaning.
  - name: garak-xss-probes
    url: https://raw.githubusercontent.com/NVIDIA/garak/main/garak/probes/xss.py
    parser: parse_garak_python_module
    category: exfil
```

- [ ] **Step 2: Rebuild and read the per-source yield**

Run: `venv/bin/python scripts/fetch_and_build.py 2>&1 | grep -Ei "garak-xss|error"`
Expected: a line `Fetched N raw entries from garak-xss-probes`. Record N.

- [ ] **Step 3: Decide keep-or-revert**

- If **N ≥ 5** and the rebuild reports no `source_errors` for it: keep the source. Continue to Step 4.
- If **N is 0**, the URL 404s, or it raises a parser/`source_errors` entry: the source is a miss. Remove the block you added in Step 1 from `scripts/sources.yaml`, rebuild once more to restore a clean set, and skip to Step 6 (Task 2 becomes a no-op addition — that is an acceptable outcome; Task 1 already delivered the coverage gain).

- [ ] **Step 4: Verify quarantine still holds and counts didn't regress**

Run:
```bash
venv/bin/python -c "import json;m=json.load(open('payloads/metadata.json'));print('total',m['total_unique_payloads'],'errors',m['source_errors'])"
venv/bin/python -c "import csv,glob;rows=list(csv.DictReader(open('payloads/promptbreaker.csv')));risky=set();[risky.update(open(f).read().split(chr(10))) for f in glob.glob('payloads/risky/**/*.txt',recursive=True)];print('leaked:',len([r for r in rows if r['prompt'] in risky and r['prompt'].strip()]))"
```
Expected: `total` ≥ the Task 1 total; `errors []`; `leaked: 0`.

- [ ] **Step 5: Update the README source list (kept source only)**

In `README.md`, under "## Sources aggregated (see scripts/sources.yaml)", add one bullet:
```markdown
- NVIDIA garak XSS/exfil probes — data-exfiltration / markup-echo payloads
```

- [ ] **Step 6: Run tests and commit**

Run: `venv/bin/pytest scripts/test_fetch_and_build.py -q` → all PASS.
```bash
git add scripts/sources.yaml README.md payloads/
git commit -m "Add garak XSS/exfil probe source (or revert if no yield)"
```

---

## Part B — Burp extension (`prompt-breaker-tool`). Do after Part A.

All paths below are relative to `prompt-breaker-tool/`.

### Task 3: Prune the runtime source list to PromptBreaker only

**Files:**
- Modify: `src/main/java/com/llminjector/fetch/PromptSource.java` (the `BUILT_IN` field)

**Interfaces:**
- Consumes: nothing new.
- Produces: `PromptSource.BUILT_IN` now a single-element list; `PromptSource.promptBreaker()` still returns the `PromptBreaker` entry (it searches `BUILT_IN` by `repo().equals("PromptBreaker")`, which still matches).

- [ ] **Step 1: Replace the BUILT_IN list**

In `PromptSource.java`, replace the entire `public static final List<PromptSource> BUILT_IN = List.of(...);` assignment with just the PromptBreaker source:
```java
    public static final List<PromptSource> BUILT_IN = List.of(
        new PromptSource("PromptBreaker payloads (promptbreaker.csv)", "Deepraj-blip", "PromptBreaker", "main", "jailbreak", Extraction.CSV, "prompt"));
```
Leave the constructors, `promptBreaker()`, `wholeFile()`, and the `Extraction` enum unchanged.

- [ ] **Step 2: Compile**

Run: `./gradlew compileJava`
Expected: `BUILD SUCCESSFUL`. (`AutoFetcher.promptBreaker()` and `ExtensionState.allSources()` still resolve — they only reference the PromptBreaker entry and the list, both still present.)

- [ ] **Step 3: Commit**

```bash
git add src/main/java/com/llminjector/fetch/PromptSource.java
git commit -m "Drop 8 external runtime sources; consume only PromptBreaker list"
```

---

### Task 4: Thread `tier` and `model` from the CSV through model, persistence, and UI

`promptbreaker.csv` already has `prompt,category,tier,model,source_count`. The reader ignores `tier`/`model` today. This task captures them and shows Tier in the library. One cohesive deliverable — the pieces must land together to be coherent and to compile.

**Files:**
- Modify: `src/main/java/com/llminjector/model/Prompt.java`
- Modify: `src/main/java/com/llminjector/fetch/SourceFetcher.java` (`addCsv`)
- Modify: `src/main/java/com/llminjector/burpglue/ExtensionState.java` (4 (de)serialization sites)
- Modify: `src/main/java/com/llminjector/ui/PromptsTab.java` (table model + search filter)

**Interfaces:**
- Consumes: `Prompt(String name, String content, String category, String source)` (unchanged 4-arg constructor).
- Produces: `Prompt.tier()`, `Prompt.model()`, `Prompt.setTier(String)`, `Prompt.setModel(String)` (nullable; default `null`).

- [ ] **Step 1: Add `tier`/`model` to `Prompt`**

In `Prompt.java`, after the `source` field add:
```java
    private String tier;
    private String model;
```
After the `source()` getter add:
```java
    public String tier() {
        return this.tier;
    }

    public String model() {
        return this.model;
    }
```
After the `setSource` setter add:
```java
    public void setTier(String string) {
        this.tier = string;
    }

    public void setModel(String string) {
        this.model = string;
    }
```

- [ ] **Step 2: Read the columns in `SourceFetcher.addCsv`**

In `SourceFetcher.java`, add an exact-match column helper next to `categoryIndex`:
```java
    private static int colByName(List<String> header, String name) {
        for (int i = 0; i < header.size(); ++i) {
            if (header.get(i).strip().equalsIgnoreCase(name)) {
                return i;
            }
        }
        return -1;
    }
```
In `addCsv`, after `int catCol = categoryIndex(header);` add:
```java
        int tierCol = colByName(header, "tier");
        int modelCol = colByName(header, "model");
```
Replace the single `out.add(new Prompt(...));` line with:
```java
                Prompt prompt = new Prompt(labelFor(path) + " #" + (n + 1), p, category,
                        src.repo().toLowerCase() + ":" + path);
                if (tierCol >= 0 && tierCol < row.size() && !row.get(tierCol).isBlank()) {
                    prompt.setTier(row.get(tierCol).strip());
                }
                if (modelCol >= 0 && modelCol < row.size() && !row.get(modelCol).isBlank()) {
                    prompt.setModel(row.get(modelCol).strip());
                }
                out.add(prompt);
```
Ensure `com.llminjector.model.Prompt` is already imported (it is).

- [ ] **Step 3: Persist `tier`/`model` (backward-compatible)**

In `ExtensionState.java`, at each of the two **write** sites (`promptToJson` ~line 120 and `promptsJson` ~line 165), after the `"source"` put add:
```java
        node.put("tier", Json.Node.string(prompt.tier() == null ? "" : prompt.tier()));
        node.put("model", Json.Node.string(prompt.model() == null ? "" : prompt.model()));
```
(In `promptsJson` the local variable is `node2` — use `node2.put(...)` there to match the surrounding code.)

At each of the two **read** sites (`promptFromJson` ~line 130 and `loadPrompts` ~line 146), after the `Prompt` is constructed and before it is added to the list, add:
```java
        String tier = ExtensionState.str(node, "tier");
        if (tier != null && !tier.isEmpty()) {
            prompt.setTier(tier);
        }
        String model = ExtensionState.str(node, "model");
        if (model != null && !model.isEmpty()) {
            prompt.setModel(model);
        }
```
(In `loadPrompts` the node variable is `node2` and the prompt variable is `prompt` — read from `node2`. `ExtensionState.str` returns `""` for a missing key, so old persisted prompts load with `null` tier/model — the required migration behavior.)

- [ ] **Step 4: Add a Tier column and make it searchable in `PromptsTab`**

In `PromptsTab.java`, in `PromptTableModel`, change:
```java
        private final String[] cols = new String[]{"On", "Name", "Source", "Category"};
```
to:
```java
        private final String[] cols = new String[]{"On", "Name", "Source", "Category", "Tier"};
```
In `getValueAt`, after `case 3:` block add:
```java
                case 4: {
                    return prompt.tier() == null ? "" : prompt.tier();
                }
```
In the search `RowFilter.include` (the line building the searchable string), change:
```java
                String string2 = (prompt.name() + " " + prompt.content() + " " + prompt.category()).toLowerCase();
```
to:
```java
                String string2 = (prompt.name() + " " + prompt.content() + " " + prompt.category()
                        + " " + (prompt.tier() == null ? "" : prompt.tier())
                        + " " + (prompt.model() == null ? "" : prompt.model())).toLowerCase();
```
(The existing column-width setup only sizes columns 0–3; the new column 4 auto-sizes — no change needed. `getColumnClass`/`isCellEditable` already treat every non-zero column as a read-only String, so Tier is correctly non-editable text.)

- [ ] **Step 5: Build the JAR and verify the ServiceLoader file survives**

Run:
```bash
./gradlew clean jar
unzip -p build/libs/prompt-breaker-ui-update.jar META-INF/services/burp.api.montoya.BurpExtension
```
Expected: `BUILD SUCCESSFUL`, and the `unzip` prints `com.llminjector.LLMInjectorExtension`.

- [ ] **Step 6: Commit**

```bash
git add src/main/java/com/llminjector/model/Prompt.java src/main/java/com/llminjector/fetch/SourceFetcher.java src/main/java/com/llminjector/burpglue/ExtensionState.java src/main/java/com/llminjector/ui/PromptsTab.java
git commit -m "Surface CSV tier/model columns in prompt library"
```

---

### Task 5: Manual end-to-end verification in Burp

No code changes — this is the acceptance gate the spec's "Testing (Part B)" calls for. Requires Burp Suite (installed at `/Applications/Burp Suite.app`).

- [ ] **Step 1: Load the freshly built JAR**

In Burp → Extensions → Add → Java → `prompt-breaker-tool/build/libs/prompt-breaker-ui-update.jar` (remove any prior copy first). Errors tab must be empty; the PromptBreaker suite tab must appear.

- [ ] **Step 2: Verify the source list is pruned**

Prompts tab → Config → enable "fetch prompts from GitHub / URLs" → the Fetch dropdown must list **only** "PromptBreaker payloads (promptbreaker.csv)" — no elder-plinius / verazuo / etc.

- [ ] **Step 3: Verify tier data and a scan**

Fetch the PromptBreaker source. Expected: the library repopulates and the new **Tier** column shows `small`/`medium`/`large` values; searching e.g. `small` filters by tier. Then re-run the lab-endpoint scan from earlier (Scanner → existing target → Run scan) and confirm Results/History still populate. Report the loaded/enabled counts and a screenshot into `UI images/`.

---

## Self-Review

**Spec coverage:**
- Goal 1 (drop 8, tool consumes only our CSV) → Task 3. ✓
- Goal 2 (surface tier/model) → Task 4 (model, reader, persistence, UI). ✓
- Goal 3 (cover more — exfil/override) → Tasks 1 & 2. ✓
- Spec "keep pipeline external harvesting + weekly cron" → Global Constraints + Tasks 1/2 only add sources; cron file untouched. ✓
- Spec "risky/ never in CSV" → verified in Task 1 Step 4 and Task 2 Step 4. ✓
- Spec "persistence migration defaults null" → Task 4 Step 3 (ExtensionState.str returns "" → null). ✓
- Spec "ServiceLoader must remain" → Task 4 Step 5 asserts it. ✓
- Spec non-goal (no raw .txt, no tier-filter control, no engine change) → not implemented. ✓

**Placeholder scan:** No TBD/TODO; every code step has concrete code. Task 2's keep-or-revert is an empirical branch with explicit thresholds, not a placeholder. ✓

**Type consistency:** `setTier`/`tier`/`setModel`/`model` used identically across Prompt.java, SourceFetcher.addCsv, ExtensionState (4 sites), and PromptsTab. `colByName(List<String>,String)` defined in Task 4 Step 2 before use. `BUILT_IN` single-element list still satisfies `promptBreaker()` and `allSources()`. ✓
