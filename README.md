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
an actual `http(s)://` origin, not `file://`). The page has two modes, switched via the left-side
nav: **Upload** and **Hunt** (Hunt stays disabled until you've planted a mutation).

**Upload mode** is where you set things up. Files are laid out as horizontal, code-editor-style
tabs (with line numbers) in two groups, separated by a divider: source files, then test files. Click
either group's **+** to add another (a source file or a green **T** test file), give it a module
name, and paste (or upload) its code — add more source files for multi-file dependencies (e.g. a
planner file that imports a heap file), and add every test file your project has; they're all
combined into one suite and run together, the same way `unittest discover` runs a whole `tests/`
directory at once. Click the **&#9675;** on any source tab(s) to include them in the mutation pool
(it turns into &#9679;) — any number can be included at once, and the engine picks randomly among
*every* eligible file's mutation sites, so which file actually ends up mutated isn't fixed to a
single one you chose ahead of time (test files aren't poolable, since mutations never land there).
Two checkboxes control which operators `plant` draws from — **Generic operators** (useful on any
project) and **Project-specific operators** (patterns added for one particular project in
`py/project_operators.py`); at least one must stay checked. Hit **Plant Mutation** and the page
switches to Hunt mode automatically.

**Hunt mode** shows the same tabbed layout, but for every candidate source file (not just the
mutated one — the hint above the tabs names the actual pool, never which one has the bug), with no
add/remove/pool controls, since you're hunting, not setting up (test files aren't shown here either,
for the same reason). Edit whichever file(s) you think need fixing and hit **Run Tests** to check
your work — the real `unittest` output (every test, pass/fail, full tracebacks) appears below,
exactly like running the suite yourself locally. After three failed attempts (or once you succeed),
**Reveal** shows the diff and which mutation operator was used. **New Mutation** plants a fresh one
and returns you to Hunt mode.

Test files can't be auto-derived from the source files being mutated — they need to be genuinely
different content, since it's the `unittest` assertions in them that the engine actually runs to
decide whether a mutation (or your fix) changed behavior.

### Connecting a project folder

Instead of pasting/uploading each file by hand, pick a **Project** from the dropdown (e.g. "A*
Path-Planning") and click **Connect folder…** — this uses the browser's native folder picker (File
System Access API; Chrome/Edge only) to read the project's expected files straight off your disk:
every source module the profile declares, plus *every* test file it declares (not just one) — the
project's whole test suite, loaded as separate tabs all at once. Same guarantee as everything else
here: this is local disk *read* access for that one folder, granted by you through the browser's own
picker — nothing is uploaded, and the connection doesn't persist across a page reload (reconnect if
you refresh).

`profiles.json` (repo root) declares each known project's expected layout — file paths, module
names, and which test file belongs to which source module. It's pure structural metadata (no actual
code), so it's fine to keep in this public repo. Add a new profile entry there for another project.

## Limitations

- Pure Python only, and only logic that doesn't need real OS sockets/processes (Pyodide runs in a
  browser sandbox) — no networking, no subprocesses, no file I/O beyond the in-memory practice
  filesystem this tool sets up for you.
- One active mutation at a time; refresh the page to reset everything.
- For the local CLI equivalent (works against any real project on disk, including code that does
  need networking, since it isn't sandboxed) see `~/mutation-tester/` — same mutation operators
  (`py/operators.py` and `py/project_operators.py` here are copied verbatim from there), different
  runtime.

## Files

- `index.html` — the entire app: UI, Pyodide bootstrap, and the JS↔Python calling glue (every call
  crosses that boundary as a JSON string in both directions, to sidestep proxy-object edge cases).
- `profiles.json` — known project layouts for the folder-connect feature (paths, module names,
  source→test mapping). Structural metadata only, no code.
- `py/operators.py` — the **generic** mutation operator library (AST-based, pure stdlib), useful on
  any project: comparison-operator swap, boolean `and`/`or` swap, `if`/`while` condition negation,
  arithmetic-operator swap, off-by-one/boolean constant tweaks, and boundary offset (`-1`/`+1`
  wrapped around a non-constant comparison operand).
- `py/project_operators.py` — **project-specific** operators: patterns worth practicing against one
  particular project that don't belong in the generic set. Starts empty; add to it as real examples
  from that project's own mutator reveal a pattern the generic set doesn't cover.
- `py/web_engine.py` — the Pyodide-adapted plant/verify/check-fix/diff logic. The one real
  adaptation from the CLI tool: verification runs `unittest` in-process (evicting every involved
  module from `sys.modules` before each run) instead of shelling out to a subprocess, since a
  browser sandbox can't spawn processes.
