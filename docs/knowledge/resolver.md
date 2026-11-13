# D3 — deterministic documentation resolver

Implementation: [scripts/docs.py](../../scripts/docs.py). D3 is a **read-only, offline** helper. D1's 167-row inventory is not a managed-document registry; legacy files are not automatically elevated to approved contracts. D4 adds an unconditional `Documentation integrity` CI job; marking it as a required Ruleset check is an administrator operation. D5 registers 10 selected Linux Desktop v3 documents with **proposed/historical** metadata; it does not approve designs or verify unmerged I2/I4 code. See [v3 feature index](../features/linux-desktop-v3/README.md). D6 additionally registers curated [Curved Workspace](../features/curved-workspace/README.md), [SA0](../features/spatial-anchor/README.md), [IME](../features/android-ime/README.md), and [initial Phase 0–6](../features/initial-phases/README.md) reading sets. The original Phase task JSON and Curved Workspace fixture JSON remain unmodified authorities; unregistered historic files are not automatically elevated.

## Usage

~~~sh
python3 scripts/docs.py validate
python3 scripts/docs.py context --feature documentation-system --task D3
python3 scripts/docs.py context --feature documentation-system --task D3 --format json
python3 scripts/docs.py affected --path scripts/docs.py
python3 scripts/docs.py drift --feature documentation-system --task D3
python3 -m unittest discover -s scripts/tests -p test_docs_resolver.py -v
~~~

D5 now registers a bounded Linux Desktop v3 task graph. For I4, resolve I2 and I4 **separately** to respect the 250,000-byte default context limit; I4's implementation prerequisites still include I2/I3 and cannot be inferred from the narrower I4 listing alone:

~~~sh
python3 scripts/docs.py context --feature linux-desktop-v3 --task I2
python3 scripts/docs.py context --feature linux-desktop-v3 --task I4
~~~

Unregistered tasks (including I3/I5) deliberately return a nonzero error. A registered document still requires a source/PR status check; do not infer design acceptance from IDs or dates.

## Security and supported YAML subset

No PyYAML, JSON Schema runtime, network requests, or external services are required. The CLI evaluates the keywords used by the pinned v1 JSON Schema and accepts an intentionally limited, safe YAML subset: 2-space indented mappings, scalar lists, simple inline scalar arrays, quoted or bare scalar strings, integer/boolean/null values. Unsupported constructs (tags, anchors, custom constructors, block strings, duplicate keys, multiline mappings) fail closed. YAML timestamps are strings; dates are validated against canonical ISO 8601. This is **not a generic YAML 1.2 parser**. JSON Schema changes that introduce unsupported keywords must update the D3 validator and its regression tests in the same PR.

Paths are repository-relative; parent traversal and symlink escape are rejected. Metadata Front Matter is discovered only in Markdown files that start with a managed \`---\` block; legacy documents without it remain unregistered. Managed dependencies, supersedes, and active manifest references must resolve to stable IDs or validation fails. A proposed design is returned with a warning; it is not treated as accepted. Where multiple accepted documents claim the same explicit \`authority_keys\` scope, validation fails instead of choosing the latest filename.

\`context\` returns deterministic dependency-first ordering (sorted IDs as tie-break). It emits metadata and file paths, **not pasted body text**. The defaults are at most 32 documents and 250,000 total bytes; larger contexts fail instead of silently truncating. \`affected\` is conservative, and a shared source may match multiple features.

## Drift semantics

A document's baseline SHA and related source patterns are not a proof of current correctness. Drift is \`clean\` only when the pinned baseline is present, is an ancestor of the comparison HEAD, and no registered related source has changed. Tracked changes (including rename sources and destinations) yield \`check_required\`. Missing baseline, missing source mapping, shallow checkout, and Git errors yield \`unknown\`; never interpret it as \`clean\`. A result of \`clean\` is **not evidence of Quest acceptance**.

## D3 boundaries

D6 adds four curated feature indices while keeping historical evidence separate from implementation status. The initial `documentation-system` smoke-test remains proposed. D5 adds `linux-desktop-v3` with selected B1/B2/C1/C2/E1/I2/I4 records and source associations, preserving original files and historical observations. Neither feature index registers Phase 0–6 or grants accepted design authority. D5's source baseline and PR #13/#16 boundary are documented in the [v3 index](../features/linux-desktop-v3/README.md).

Existing \`scripts/check-design\`, CI scope classification, workflows and Phase task specs are preserved. The Python unit suite is discovered by existing \`scripts/check-host --suite all\`; D4 also runs these focused tests as a fast independent required-check candidate. 
