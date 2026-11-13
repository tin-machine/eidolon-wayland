# D3 — deterministic documentation resolver

Implementation: [scripts/docs.py](../../scripts/docs.py). D3 is a **read-only, offline** helper. D1's 167-row inventory is not a managed-document registry; legacy files are not automatically elevated to approved contracts. D4 adds an unconditional `Documentation integrity` CI job; marking it as a required Ruleset check is an administrator operation. D5 Linux Desktop v3 migration is **not yet implemented**.

## Usage

~~~sh
python3 scripts/docs.py validate
python3 scripts/docs.py context --feature documentation-system --task D3
python3 scripts/docs.py context --feature documentation-system --task D3 --format json
python3 scripts/docs.py affected --path scripts/docs.py
python3 scripts/docs.py drift --feature documentation-system --task D3
python3 -m unittest discover -s scripts/tests -p test_docs_resolver.py -v
~~~

When Linux Desktop v3 is registered in D5, the following interface becomes available:

~~~sh
python3 scripts/docs.py context --feature linux-desktop-v3 --task I4
~~~

**Before D5, that command deliberately fails** with an unregistered-feature error. Do not infer contract validity by scanning filenames.

## Security and supported YAML subset

No PyYAML, JSON Schema runtime, network requests, or external services are required. The CLI evaluates the keywords used by the pinned v1 JSON Schema and accepts an intentionally limited, safe YAML subset: 2-space indented mappings, scalar lists, simple inline scalar arrays, quoted or bare scalar strings, integer/boolean/null values. Unsupported constructs (tags, anchors, custom constructors, block strings, duplicate keys, multiline mappings) fail closed. YAML timestamps are strings; dates are validated against canonical ISO 8601. This is **not a generic YAML 1.2 parser**. JSON Schema changes that introduce unsupported keywords must update the D3 validator and its regression tests in the same PR.

Paths are repository-relative; parent traversal and symlink escape are rejected. Metadata Front Matter is discovered only in Markdown files that start with a managed \`---\` block; legacy documents without it remain unregistered. Managed dependencies, supersedes, and active manifest references must resolve to stable IDs or validation fails. A proposed design is returned with a warning; it is not treated as accepted. Where multiple accepted documents claim the same explicit \`authority_keys\` scope, validation fails instead of choosing the latest filename.

\`context\` returns deterministic dependency-first ordering (sorted IDs as tie-break). It emits metadata and file paths, **not pasted body text**. The defaults are at most 32 documents and 250,000 total bytes; larger contexts fail instead of silently truncating. \`affected\` is conservative, and a shared source may match multiple features.

## Drift semantics

A document's baseline SHA and related source patterns are not a proof of current correctness. Drift is \`clean\` only when the pinned baseline is present, is an ancestor of the comparison HEAD, and no registered related source has changed. Tracked changes (including rename sources and destinations) yield \`check_required\`. Missing baseline, missing source mapping, shallow checkout, and Git errors yield \`unknown\`; never interpret it as \`clean\`. A result of \`clean\` is **not evidence of Quest acceptance**.

## D3 boundaries

The only live feature manifest in this PR is \`documentation-system\`, a low-risk self-test of the resolver. It references the architecture proposal, whose \`design_status\` remains \`proposed\`. It deliberately does not register older Phase 0–6 files or Linux Desktop v3 contracts: those require D5 review of authoritative status, baseline, dependency IDs and ongoing I2/I3/I4 implementation work.

Existing \`scripts/check-design\`, CI scope classification, workflows and Phase task specs are preserved. The Python unit suite is discovered by existing \`scripts/check-host --suite all\`; D4 also runs these focused tests as a fast independent required-check candidate. 
