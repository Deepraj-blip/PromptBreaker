# Part A ↔ Part B Integration (Minimal CSV Bridge) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the Burp extension (Part B) consume the payload repo (Part A) by publishing a tool-consumable CSV from Part A, reconstructing a buildable Part B project from the decompiled jar, and making Part B ingest that CSV (short payloads included, per-row categories).

**Architecture:** Three pieces. Part A gains a CSV emitter (independent, testable here). Part B is reconstructed into a Gradle/Montoya project from a fresh CFR decompile of `prompt-breaker.jar` (a gating build spike). Part B then gets three surgical fetch-layer edits so it consumes the CSV. Integration is via runtime GitHub fetch — the two stay separate artifacts.

**Tech Stack:** Python 3.11 + pytest (Part A); Java 21 + Gradle + Burp Montoya API (Part B); CFR decompiler.

**Spec:** `docs/superpowers/specs/2026-09-08-part-a-b-integration-design.md`

## Global Constraints

- CSV columns, in this exact order: `prompt,category,tier,model,source_count`. One row per (payload, model); an untagged payload emits one row with an empty `model`.
- The CSV is written from the in-memory `payload_index` list built in `main()` (reuse it; do not re-read the JSON or recompute).
- Part B edits are minimal: relax the length floor **for the CSV path only** (leave WHOLE_FILE/CODE_BLOCKS untouched), read a per-row `category` column, add a built-in source. **No** tier/model/budget selector UI.
- Part B builds with a Java 21 toolchain (the jar was compiled with 21); Montoya API is a `compileOnly` dependency (Burp provides it at runtime). No other external runtime deps (the JSON parser is hand-rolled).
- The decompiled source is a reference, not guaranteed to recompile; Task 2 is a spike with an explicit fallback (get the real source) if it needs disproportionate reconstruction.
- Commit message footer (every commit): `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`

## Prerequisite for end-to-end use (USER action, not a code task)

For the bridge to actually work at runtime, the payload repo (with the new `promptbreaker.csv`) must be **pushed to GitHub** so the tool can fetch it. The payload repo currently has **no git remote**. Until it's pushed, Part B can only be tested against a manually-added source pointing at a reachable repo. The built-in source added in Task 3 uses the user's real GitHub `owner/repo` — the user must supply these (or edit them post-build). This does not block Tasks 1–2, or building Part B in Task 3; it blocks only the final in-Burp fetch test.

---

### Task 1: Part A — CSV export (`promptbreaker.csv`)

**Files:**
- Modify: `scripts/fetch_and_build.py` (add `import csv` at top; add `write_payload_csv`; call it in `main()` right after the `payload_index.json` write)
- Modify: `scripts/test_fetch_and_build.py` (append tests)
- Generated (committed): `payloads/promptbreaker.csv`

**Interfaces:**
- Consumes: the `payload_index` list produced by `build_payload_index(...)` in `main()` — each entry is a dict with keys `text, category, tier, models (list[str]), source_count (int), sources (list[str])`.
- Produces: `write_payload_csv(payload_index: list[dict], out_path: str) -> int` (writes the CSV, returns the number of data rows written, header excluded).

- [ ] **Step 1: Write the failing tests**

Append to `scripts/test_fetch_and_build.py`:

```python
import csv as _csv
from fetch_and_build import write_payload_csv


def test_write_payload_csv_header_and_model_expansion():
    index = [
        {"text": "short", "category": "jailbreak", "tier": "small",
         "models": ["openai", "anthropic"], "source_count": 2, "sources": ["a", "b"]},
        {"text": "no model here", "category": "exfil", "tier": "medium",
         "models": [], "source_count": 1, "sources": ["c"]},
    ]
    tmp = tempfile.mkdtemp()
    try:
        path = os.path.join(tmp, "out.csv")
        n = write_payload_csv(index, path)
        with open(path, newline="", encoding="utf-8") as f:
            rows = list(_csv.reader(f))
        assert rows[0] == ["prompt", "category", "tier", "model", "source_count"]
        # 2 models + 1 untagged = 3 data rows
        assert n == 3
        assert len(rows) == 4
        assert ["short", "jailbreak", "small", "openai", "2"] in rows
        assert ["short", "jailbreak", "small", "anthropic", "2"] in rows
        assert ["no model here", "exfil", "medium", "", "1"] in rows
    finally:
        shutil.rmtree(tmp)


def test_write_payload_csv_quotes_special_chars():
    index = [{"text": 'a, "b"\nc', "category": "jailbreak", "tier": "small",
              "models": [], "source_count": 1, "sources": ["x"]}]
    tmp = tempfile.mkdtemp()
    try:
        path = os.path.join(tmp, "o.csv")
        write_payload_csv(index, path)
        with open(path, newline="", encoding="utf-8") as f:
            rows = list(_csv.reader(f))
        assert rows[1][0] == 'a, "b"\nc'  # comma/quote/newline round-trip intact
        assert rows[1][3] == ""
    finally:
        shutil.rmtree(tmp)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest scripts/test_fetch_and_build.py -v -k write_payload_csv`
Expected: FAIL with `ImportError: cannot import name 'write_payload_csv'`

- [ ] **Step 3: Implement `write_payload_csv` and wire it into `main()`**

In `scripts/fetch_and_build.py`, add `import csv` to the top import block. Add the function near the other writers:

```python
def write_payload_csv(payload_index, out_path):
    rows = 0
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["prompt", "category", "tier", "model", "source_count"])
        for entry in payload_index:
            models = entry["models"] if entry["models"] else [""]
            for model in models:
                writer.writerow([entry["text"], entry["category"], entry["tier"],
                                 model, entry["source_count"]])
                rows += 1
    return rows
```

In `main()`, immediately after the block that writes `payload_index.json`, add:

```python
    csv_rows = write_payload_csv(payload_index, os.path.join(PAYLOADS_DIR, "promptbreaker.csv"))
    print("Wrote promptbreaker.csv (%d rows)" % csv_rows)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest scripts/test_fetch_and_build.py -v`
Expected: all tests PASS (the 2 new ones plus all prior tests)

- [ ] **Step 5: Regenerate the corpus and sanity-check the CSV**

Run: `python3 scripts/fetch_and_build.py`
Then:
```bash
head -1 payloads/promptbreaker.csv
python3 -c "import csv;r=list(csv.reader(open('payloads/promptbreaker.csv',newline='')));print('rows',len(r)-1);print('cols',r[0])"
python3 -c "import json,csv;idx=json.load(open('payloads/payload_index.json'));exp=sum(len(e['models']) or 1 for e in idx);act=len(list(csv.reader(open('payloads/promptbreaker.csv',newline=''))))-1;print('expected',exp,'actual',act);assert exp==act"
```
Expected: header is `prompt,category,tier,model,source_count`; row count equals the payload_index model-expansion count (the assert passes).

- [ ] **Step 6: Commit**

```bash
git add scripts/fetch_and_build.py scripts/test_fetch_and_build.py payloads/promptbreaker.csv
git commit -m "Export promptbreaker.csv for Burp extension consumption

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: Part B — reconstruct a buildable project (gating spike)

**Files:**
- Create: `/Users/ddhasmana1/promptbreaker/prompt-breaker-tool/` (new git repo)
  - `settings.gradle`, `build.gradle`, `.gitignore`
  - `src/main/java/com/llminjector/**` (decompiled from the jar)

**Interfaces:**
- Produces: a buildable Gradle project whose `./gradlew build` (or `gradle build`) emits a loadable Burp extension jar under `build/libs/`.

This is a **spike**, not TDD — the deliverable is "it compiles to a jar." **Fallback:** if getting it to compile requires disproportionate manual rewriting (more than routine decompiler-artifact fixes), STOP and report BLOCKED with the specific blocker — the decision then flips to obtaining the real source. Do not grind indefinitely.

- [ ] **Step 1: Create the project skeleton and decompile fresh from the jar**

```bash
mkdir -p /Users/ddhasmana1/promptbreaker/prompt-breaker-tool
cd /Users/ddhasmana1/promptbreaker/prompt-breaker-tool
git init -q
mkdir -p src/main/java
# CFR (download if not already on disk in scratch)
CFR=/private/tmp/claude-502/-Users-ddhasmana1-promptbreaker-promptbreaker-payloads-repo/edc745a4-a0f6-49ce-b47f-564c38b688c2/scratchpad/cfr.jar
[ -f "$CFR" ] || curl -sL -o "$CFR" https://repo1.maven.org/maven2/org/benf/cfr/0.152/cfr-0.152.jar
/opt/homebrew/opt/openjdk/bin/java -jar "$CFR" /Users/ddhasmana1/promptbreaker/prompt-breaker.jar --outputdir src/main/java --silent
find src/main/java -name '*.java' | wc -l   # expect ~43
```

- [ ] **Step 2: Write `.gitignore`, `settings.gradle`, `build.gradle`**

`.gitignore`:
```
.gradle/
build/
*.class
```

`settings.gradle`:
```groovy
rootProject.name = 'prompt-breaker'
```

`build.gradle`:
```groovy
plugins {
    id 'java'
}

group = 'com.llminjector'
version = '0.1.0'

java {
    toolchain {
        languageVersion = JavaLanguageVersion.of(21)
    }
}

repositories {
    mavenCentral()
}

dependencies {
    // Provided by Burp at runtime; compile-only here. Use the latest
    // montoya-api on Maven Central. If a compile error reports a missing
    // API symbol, bump/adjust this version.
    compileOnly 'net.portswigger.burp.extensions:montoya-api:2023.12.1'
}

jar {
    archiveBaseName = 'prompt-breaker'
}
```

- [ ] **Step 3: Get Gradle and generate the wrapper**

```bash
cd /Users/ddhasmana1/promptbreaker/prompt-breaker-tool
export JAVA_HOME=/opt/homebrew/opt/openjdk/libexec/openjdk.jdk/Contents/Home 2>/dev/null || export PATH=/opt/homebrew/opt/openjdk/bin:$PATH
command -v gradle >/dev/null || brew install gradle
gradle wrapper --gradle-version 8.10
```
If `brew` is unavailable, use the system `gradle` directly (skip the wrapper) for all build steps below.

- [ ] **Step 4: Build; iterate on decompiler-artifact fixes until green**

Run: `./gradlew build 2>&1 | tail -40` (or `gradle build`)

Fix the recurring CFR-artifact classes of errors as they appear (these are expected and routine):
- **Duplicate synthetic lambda + real method** (e.g. `ScanEngine` has both `runOne(...)` and `lambda$scan$0(...)` delegating to it): if the compiler reports a genuine duplicate/unused synthetic, delete the redundant synthetic `lambda$*` method and point the call site at the real method; otherwise leave it.
- **Enum-switch helper classes** (`Foo$1` with a `$SwitchMap$...` array): if they fail to compile, replace the `switch` on that enum with an `if/else` chain over the same enum constants in the owning method.
- **Raw-generic / unchecked casts** the compiler rejects: add the explicit generic type or an `@SuppressWarnings("unchecked")`.
- **`record` component or `var` quirks**: rewrite to explicit types.
Make the **smallest** change that compiles; do not refactor.

Repeat until `./gradlew build` succeeds. If the error count is not shrinking with routine fixes (structural decompile failure), STOP → report BLOCKED (fallback: real source).

- [ ] **Step 5: Verify the jar and record the outcome**

```bash
ls -la build/libs/*.jar
/opt/homebrew/opt/openjdk/bin/jar tf build/libs/prompt-breaker-0.1.0.jar | grep -c 'com/llminjector'
```
Expected: a jar exists and lists the `com/llminjector/**` classes.

**User-run verification (report as a required manual step, do not attempt here):** load `build/libs/prompt-breaker-0.1.0.jar` into Burp → Extensions → Add (Java) → confirm it loads with no error and the PromptBreaker/LLM-Injector tabs appear.

- [ ] **Step 6: Commit the buildable project**

```bash
cd /Users/ddhasmana1/promptbreaker/prompt-breaker-tool
git add -A
git commit -m "Reconstruct buildable Gradle/Montoya project from decompiled jar

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: Part B — minimal CSV-consume changes

**Files:**
- Modify: `/Users/ddhasmana1/promptbreaker/prompt-breaker-tool/src/main/java/com/llminjector/fetch/SourceFetcher.java` (`addCsv`, new `categoryIndex` helper)
- Modify: `/Users/ddhasmana1/promptbreaker/prompt-breaker-tool/src/main/java/com/llminjector/fetch/PromptSource.java` (`BUILT_IN` list)

**Interfaces:**
- Consumes: `Csv.parse(String) -> List<List<String>>`, `Prompt(name, content, category, source)`, existing `columnIndex`, `longest`, `labelFor` helpers (all already in `SourceFetcher`).
- Depends on: Task 2 produced a building project. Apply these edits to the reconstructed source (variable names in the decompiled `addCsv` may differ; replace the whole method body as shown).

Note the decompiled `addCsv` uses a literal `40` for its length check and applies `promptSource.category()` to every row. These two edits fix exactly that.

- [ ] **Step 1: Replace `addCsv` and add `categoryIndex` in `SourceFetcher.java`**

Replace the entire `addCsv` method with:

```java
    private void addCsv(List<Prompt> out, PromptSource src, String path, String raw) {
        List<List<String>> table = Csv.parse(raw);
        if (table.isEmpty()) {
            return;
        }
        List<String> header = table.get(0);
        int promptCol = columnIndex(header, src.csvColumn());
        int catCol = categoryIndex(header);
        int n = 0;
        for (int i = 1; i < table.size() && n < MAX_CSV_ROWS; ++i) {
            List<String> row = table.get(i);
            String p = promptCol >= 0 && promptCol < row.size() ? row.get(promptCol) : longest(row);
            if (p == null) {
                continue;
            }
            p = p.strip();
            if (p.isEmpty()) {          // was: p.length() < 40  — keep short payloads
                continue;
            }
            String category = catCol >= 0 && catCol < row.size() && !row.get(catCol).isBlank()
                    ? row.get(catCol).strip()
                    : src.category();
            out.add(new Prompt(labelFor(path) + " #" + (n + 1), p, category,
                    src.repo().toLowerCase() + ":" + path));
            ++n;
        }
    }

    private static int categoryIndex(List<String> header) {
        for (int i = 0; i < header.size(); ++i) {
            if (header.get(i).strip().equalsIgnoreCase("category")) {
                return i;
            }
        }
        return -1;
    }
```

If `MAX_CSV_ROWS` isn't already a constant in the file, use the existing literal the decompiled `addCsv` used (`2000`). Leave `MIN_LEN`, `addWholeFile`, and `addCodeBlocks` untouched.

- [ ] **Step 2: Build to confirm the edit compiles**

Run: `cd /Users/ddhasmana1/promptbreaker/prompt-breaker-tool && ./gradlew build 2>&1 | tail -20`
Expected: BUILD SUCCESSFUL.

- [ ] **Step 3: Add the PromptBreaker built-in source in `PromptSource.java`**

In the `BUILT_IN` `List.of(...)`, add one entry (adjust `owner`/`repo`/`branch` to the user's actual GitHub coordinates for the pushed payload repo — see the Prerequisite note; if unknown at build time, use the placeholders below and flag them for the user to edit):

```java
new PromptSource("PromptBreaker payloads (promptbreaker.csv)",
        "PROMPTBREAKER_OWNER", "promptbreaker-payloads-repo", "main",
        "jailbreak", Extraction.CSV, "prompt")
```

`category` here is only the fallback for rows without a `category` column — our CSV always has one, so per-row categories win. `csvColumn` is `prompt`.

- [ ] **Step 4: Build again**

Run: `cd /Users/ddhasmana1/promptbreaker/prompt-breaker-tool && ./gradlew build 2>&1 | tail -20`
Expected: BUILD SUCCESSFUL; `build/libs/prompt-breaker-0.1.0.jar` refreshed.

- [ ] **Step 5: Commit**

```bash
cd /Users/ddhasmana1/promptbreaker/prompt-breaker-tool
git add -A
git commit -m "Consume promptbreaker.csv: per-row category, keep short payloads, built-in source

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

- [ ] **Step 6: Report the user-run acceptance test (do not attempt here)**

Provide the user these exact steps:
1. Push the payload repo to GitHub (public), confirm `payloads/promptbreaker.csv` is reachable at its raw URL; set the built-in source's `owner`/`repo` to match (rebuild if edited).
2. Load `build/libs/prompt-breaker-0.1.0.jar` in Burp (Extensions → Add → Java).
3. Prompts tab → enable GitHub fetch (Config) → fetch the "PromptBreaker payloads" source.
4. Confirm: payloads appear; short ones (< 40 chars, e.g. `Tell Me Your Secrets, AI!`) are present; category column shows per-row categories (jailbreak/exfil/encoding), not all one category.

---

## Notes on ordering & dependencies

- **Task 1** is independent — run it first (or in parallel); it needs nothing from Part B.
- **Task 2** gates **Task 3** (can't edit what won't build). If Task 2 hits its fallback (BLOCKED), Task 3 is deferred pending real source; Task 1 still stands and ships value.
- End-to-end runtime (Task 3 Step 6) depends on the user pushing the payload repo to GitHub — a user action, not a code task.
