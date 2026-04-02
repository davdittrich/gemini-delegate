# Gemini Prompt Template (Token-Efficient)

## Analysis / Plan (no code changes)

```
Task:
- <what to analyze>

Repo pointers:
- <file paths + approximate line numbers>

Constraints:
- Keep it concise and actionable.
- Do not paste large snippets; reference files/lines instead.

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
- Return a checklist of issues + suggested fixes (no code unless requested).

Input diff:
<paste unified diff here>
```

## Tool-assisted review (ACP — Gemini reads files directly)

```
Task:
- Review the implementation of <feature/function> for correctness and edge cases.

Repo pointers:
- Start at <file path>:<line range>
- Also check <related file>:<line range> for integration points

Constraints:
- Read the files yourself using the workspace. Do not ask for code to be pasted.
- Check: off-by-one errors, null/empty handling, error paths, thread safety.
- Cross-reference any claims in comments/docs against the actual code.

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
- <research question requiring up-to-date information>

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
  Find ways it could fail, miss edge cases, or produce incorrect results.

Plan:
<paste plan text here>

Constraints:
- Check all technical claims against the actual source files.
- Find at least 3 potential failure modes.
- For each issue: state the mechanism, the evidence, and severity (blocking/non-blocking).

Output:
- JSON: {"verdict": "PASS"|"FAIL", "blocking_issues": [{"issue": "...", "evidence": "...", "severity": "blocking"}], "non_blocking": [...]}
```
