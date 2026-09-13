# Repository change extraction

RI-001 identifies Python file changes. RI-002 enriches that result with statically
extracted symbols. Both stages read immutable Git objects; neither checks out a
revision nor imports repository code.

```python
from lou.repository import extract_changed_symbols, parse_repository_changes

files = parse_repository_changes(
    repository_id="broken-store",
    repository_path="/path/to/broken-store",
    base_revision="good",
    candidate_revision="n-plus-one",
)
symbols = extract_changed_symbols(
    repository_path="/path/to/broken-store",
    change=files,
)
assert symbols.changed_symbols == ["store.app.Store.checkout"]
assert files.changed_symbols == []
```

Pass the original RI-001 result and the same repository into RI-002. Its commit
fields must contain full immutable SHAs. The returned `RepositoryChange` is a deep
copy, preserving file classifications, identities, and caller metadata. RI-001
continues to report file-extraction completeness without attempting symbol parsing.
No graph, workload selection, execution, or platform wiring is performed here.

## Symbols and ranges

`changed_symbols` is a sorted, unique list of keys. Keys combine the repository-relative
Python path (with the final `.py` removed) with lexical class/function names, such as
`store.app.Store.checkout` and `store.app.outer.inner`. Path components are percent
encoded, including literal dots, before joining with dots; `a.b.py` and `a/b.py`
therefore remain distinct. `__init__` remains an explicit component. Repeated
declarations in one scope receive source-order suffixes `#2`, `#3`, and so on.
Keys are deterministic for the same commits; renames change path-based identities.

Details live under `metadata["symbol_extraction"]` with `schema_version: "1"`:

- `symbols`: records with `key`, `kind` (`function`, `method`, `class`, or `module`),
  `phase` (`baseline` or `candidate`), repository-relative `path`, and inclusive,
  one-based `start_line`/`end_line`. Ranges include decorators and exclude trailing
  blank lines except those before a trailing indented comment. Trailing comments at
  the definition suite's indentation depth are included. Async definitions use the
  same function/method kinds.
- `diagnostics`: source-free records normally containing `path`, `phase`, and a stable
  error `code`. Snapshots beyond the processing limit use one aggregate record.
- `expected_snapshots`, `completed_snapshots`, `max_blob_bytes`, and
  `max_file_snapshots`: extraction accounting and configured module limits.

Changed lines select their innermost enclosing definition. Class headers and
class-level statements select the class. Lines outside definitions, including
module comments or whitespace, select `<module>`, whose range covers the file.
Deleting a definition retains its baseline identity; introducing one retains its
candidate identity. A deletion-only hunk need not produce a candidate range.
Additions, deletions, and renames include every definition in the applicable
snapshot. Renames retain both old and new identities. Mode-only changes produce
no symbols when the source bytes are identical.

## Limits and incomplete results

Only regular Git blobs are parsed. Symlinks and other object modes are skipped,
without following their targets. Each source blob is limited to 1 MiB, checked
before reading its contents. A call processes at most 200 file snapshots in sorted
file-pair order, baseline before candidate. Each Git command has the existing
30-second timeout. These bounds are local analysis safeguards, not an execution
sandbox or an aggregate job deadline.

Syntax errors (including syntax unsupported by the running Python interpreter),
encoding errors, bare-CR line endings, missing paths, unsupported objects, and
exceeded limits lower completeness. LF and CRLF files and Python encoding cookies
are supported when decoding preserves Git's LF line count. When one side cannot
be extracted, all scopes from its available counterpart are conservatively retained.
Empty source files contain no symbols.

RI-002 completeness is the input completeness multiplied by completed/expected
snapshots; an empty comparison preserves input completeness. Thus an empty symbol
list with completeness below one is not evidence that nothing changed. Repository,
commit, and Git execution failures use existing typed errors and abort the call.
Diagnostics contain neither source snippets nor raw command output.

RI-001 fixes the rename threshold and limit, diff algorithm, indentation heuristic,
and text treatment. It disables external diff and text-conversion drivers, and
sanitizes inherited repository-selection, pathspec, and diff environment options.
This keeps file classification reproducible and prevents repository attributes from
executing a configured diff helper. Repository discovery must also resolve to the
same directory as the nearest `.git` marker, preventing nested repositories from
redirecting analysis to an ancestor worktree.

The checkout example and exact phase ranges are tested in
`backend/tests/unit/repository/fixtures/checkout_symbols.json`. Shared version-one
contracts remain unchanged.
