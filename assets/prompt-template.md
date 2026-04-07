# Gemini Prompt Template (Token-Efficient)

## Analysis / Plan (no code changes)

```
Task:
- <what to analyze>

Files to read:
- <file path>:<approximate line range>
- <file path>:<approximate line range>

Constraints:
- Read the files yourself. I am not pasting content.
- Keep it concise and actionable. Under 400 words.
- Reference files/lines, do not paste large snippets.
- DO NOT propose code changes unless explicitly asked.

Output:
- Bullet list of findings and a proposed plan.
```

## Patch (Unified Diff only)

```
Task:
- <what to change>

Repo pointers:
- <file paths + approximate line numbers>

Constraints:
- OUTPUT: Unified Diff Patch ONLY.
- Strictly prohibit any actual modifications.
- Minimal, focused changes. No unrelated refactors.

Output:
- A single unified diff patch.
```

## Review (audit an existing diff)

```
Task:
- Review the following unified diff for correctness, edge cases, and missing tests.

Constraints:
- Analyze ONLY the changed lines and their immediate context.
- DO NOT suggest style, naming, or formatting improvements.
- DO NOT restate the code. Reference by file:line.
- Keep response under 300 words.

Input diff:
<paste `git diff` output -- hunks only, not full files>

Output:
- JSON: {"verdict": "ok"|"issues", "blocking": [{"file": "...", "line": N, "issue": "..."}], "minor": [...]}
```

## Focused diff review (cheapest review option)

Use this when reviewing a specific git diff. Sends only the changed hunks, not full files.
Claude should generate the diff via `git diff` and paste only the relevant hunks.

```
Task:
- Review ONLY the diff below for bugs, edge cases, and correctness issues.

Diff:
<paste `git diff -- <file>` output>

Constraints:
- Analyze ONLY what changed. Do not review surrounding unchanged code.
- DO NOT suggest style, naming, or refactoring improvements.
- DO NOT restate the diff. Reference changes by +/- line markers.
- If the diff is correct, respond with: {"verdict": "ok", "blocking": [], "minor": []}

Output:
- JSON only. No prose. No explanation unless a blocking issue exists.
- Schema: {"verdict": "ok"|"issues", "blocking": [{"hunk": "+/- line ref", "issue": "..."}], "minor": [...]}
```

## Tool-assisted review (ACP — Gemini reads files directly)

```
Task:
- Review the implementation of <feature/function> for correctness and edge cases.

Files to read (use your file reading capability):
- <file path>:<start_line>-<end_line>  (primary focus)
- <related file>:<start_line>-<end_line>  (integration point, if any)

Context:
- <1-2 sentence description of what this code does and what changed>

Constraints:
- Read the files yourself using the workspace. Do not ask for code to be pasted.
- Check: off-by-one errors, null/empty handling, error paths, thread safety.
- Cross-reference any claims in comments/docs against the actual code.
- DO NOT suggest unrelated refactors or style improvements.
- DO NOT restate code back. Reference by file:line.

Output:
- JSON: {"verdict": "ok"|"issues", "blocking": [...], "minor": [...]}
```

## Pre-action audit (benchmark completeness)

```
Task:
- Audit benchmark script for completeness against the canonical pipeline.

Repo pointers:
- Canonical pipeline: benchmarks/run_benchmarks.R
- Script to audit: <path to benchmark script>

Constraints:
- List every estimator group in the canonical pipeline.
- Check which groups are missing from the audit target.
- Flag structural issues: early quit()/stop(), locally-scoped helpers, variable naming.
- Keep output concise.

Output:
- Bullet list: missing groups, structural risks, verdict (ready/not ready to run).
```

## Fix regression-safety check

```
Task:
- Verify that a proposed code change does NOT re-introduce a previously fixed bug.

Repo pointers:
- <file path and line range of proposed change>

Context:
- Previous bug: <describe what broke and why>
- Proposed fix: <describe what changes>

Constraints:
- Trace the specific code path that prevents the old failure mode.
- Answer: does this change re-introduce the bug? Yes/No with evidence.

Output:
- Verdict (safe/unsafe) with the defensive code path identified.
```

## Web search (current information)

```
Task:
- Use *google_web_search*: <research question requiring up-to-date information>

Constraints:
- Search the web for current information.
- Cite sources with URLs.
- Keep concise — bullet list, not essay.

Output:
- Bullet list of findings with source URLs.
```

## Plan review (adversarial, structured JSON)

```
Task:
- Review the following implementation plan as an adversarial reviewer.
  Find all ways it could fail, miss edge cases, or produce incorrect results.

Plan:
<paste plan text -- this is the ONE case where pasting is acceptable, since plans are not files>

Constraints:
- Check all technical claims against actual source files (read them yourself).
- Find at least 3 potential failure modes.
- For each issue: state mechanism, evidence, and severity.
- DO NOT suggest improvements beyond the scope of the plan.
- Keep response brief.

Output:
- JSON: {"verdict": "PASS"|"FAIL", "blocking_issues": [{"issue": "...", "evidence": "...", "severity": "blocking"}], "non_blocking": [...]}
```
