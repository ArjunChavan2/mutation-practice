# Mutation Practice

A browser-based practice tool for code-repair/mutation-testing exercises (built for EECS 367's
"Mutate" assessment: *"Students will be expected to be able to repair their mutated versions of
their submitted code without the use of AI or any other significant tools and materials."*).

Paste your own already-correct source file(s) and test file in. It plants one subtle, verified
mutation (guaranteed to actually break your tests, never an inert no-op change) and you have to find
and fix it yourself. Nothing is revealed until you've either fixed it or tried three times.

**Nothing you paste is ever sent anywhere or committed to this repo.** Everything runs client-side
in your browser via [Pyodide](https://pyodide.org/) (CPython compiled to WebAssembly); your code
lives only in that tab's in-memory filesystem and optionally in your own browser's `localStorage`
(so you don't have to re-paste every visit) — never on a server, never in git history.

## Using it

Open the deployed GitHub Pages URL (or `index.html` locally via a static file server — Pyodide needs
an actual `http(s)://` origin, not `file://`). Paste each source file's code with its module name
(add more for multi-file dependencies, e.g. a planner file that imports a heap file), mark which one
to mutate, paste your test file, and hit **Plant Mutation**. Edit the mutated code in place and hit
**Check My Fix**; after three failed attempts (or once you succeed), **Reveal** shows the diff and
which mutation operator was used.

## Limitations

- Pure Python only, and only logic that doesn't need real OS sockets/processes (Pyodide runs in a
  browser sandbox) — no networking, no subprocesses, no file I/O beyond the in-memory practice
  filesystem this tool sets up for you.
- One active mutation at a time; refresh the page to reset everything.
- For the local CLI equivalent (works against any real project on disk, including code that does
  need networking, since it isn't sandboxed) see `~/mutation-tester/` — same mutation operators
  (`py/operators.py` here is copied verbatim from there), different runtime.

## Files

- `index.html` — the entire app: UI, Pyodide bootstrap, and the JS↔Python calling glue (every call
  crosses that boundary as a JSON string in both directions, to sidestep proxy-object edge cases).
- `py/operators.py` — the mutation operator library (AST-based, pure stdlib): comparison-operator
  swap, boolean `and`/`or` swap, `if`/`while` condition negation, arithmetic-operator swap, and
  off-by-one/boolean constant tweaks.
- `py/web_engine.py` — the Pyodide-adapted plant/verify/check-fix/diff logic. The one real
  adaptation from the CLI tool: verification runs `unittest` in-process (evicting every involved
  module from `sys.modules` before each run) instead of shelling out to a subprocess, since a
  browser sandbox can't spawn processes.
