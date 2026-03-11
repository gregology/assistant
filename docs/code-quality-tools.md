# Code Quality Tools

Dev dependencies for spotting tech debt, dead code, security issues, and complexity creep. All installed via `uv sync`.

These aren't meant to run on every commit. Run them periodically (monthly, or before a big refactor) and treat the output as input to a refactoring decision, not a punch list.

## Quick reference

```bash
# Type checking
uv run mypy app/ packages/ --ignore-missing-imports

# Complexity (cognitive)
uv run complexipy app/ packages/ --max-complexity-allowed 15

# Complexity (cyclomatic + maintainability index)
uv run radon cc app/ -a -nc        # cyclomatic, only show C+ (complex)
uv run radon mi app/ -nb           # maintainability index, only show B+ (declining)

# Dead code
uv run vulture app/ packages/ --min-confidence 80

# Unused/missing dependencies
uv run deptry .

# Security
uv run bandit -r app/ -q

# Linting
uv run ruff check app/ packages/ tests/

# Test coverage
uv run pytest --cov=app --cov-report=term-missing -v
```

## When to use what

### mypy

**What it catches:** Type mismatches, unchecked dict access, missing return types, wrong function signatures, `Any` leaking through your code.

**Config:** `pyproject.toml` under `[tool.mypy]`. Strict mode is on globally. The SDK (`gaas_sdk.*`) enforces full strict. `app.*` has `disallow_untyped_defs = false` for now (tighten later). Tests skip `no-untyped-def`. The Pydantic mypy plugin is enabled.

Package test directories (`packages/*/tests/`) are excluded from mypy. Multiple packages have identically named test files (e.g. `test_classify.py` in both `gaas-email` and `gaas-sdk`), and mypy treats those as duplicate modules. The top-level `tests/` directory is still checked. The package test directories don't have `__init__.py` files -- pytest discovers them fine without those since the project uses `--import-mode=importlib`.

**When to run it:** Before any refactor that touches function signatures or data flow. The SDK should stay at zero mypy errors. The `--ignore-missing-imports` flag is needed because some third-party packages don't ship type stubs.

**What to ignore:** Third-party library complaints. Focus on `app/` and `packages/`.

### complexipy

**What it catches:** Cognitive complexity per function. This measures how hard a function is to _understand_, not just how many branches it has. Nested conditionals, early returns that aren't really early, long chains of `elif`.

**When to run it:** When a module feels hard to work in but you can't articulate why. `config.py` and `loader.py` are the usual suspects. A function above 15 is worth looking at. Above 25, it probably needs to be split.

### radon

**What it catches:** Cyclomatic complexity (how many independent paths through the code) and maintainability index (a composite score combining complexity, LOC, and Halstead volume).

`radon cc` with `-nc` shows only functions rated C or worse. `radon mi` with `-nb` shows only modules rated B or worse.

**When to run it:** Alongside complexipy, for a second opinion. Cyclomatic complexity and cognitive complexity often disagree on what's "complex" and comparing the two gives a better picture. A function with high cyclomatic but low cognitive complexity is branchy but readable. The reverse means it's structurally simple but mentally taxing.

### vulture

**What it catches:** Unreachable code, unused imports, unused variables, unused function arguments. Good at finding re-export shims that have outlived their purpose, handler functions that nothing registers, config fields nobody reads.

**When to run it:** After extracting code into new modules or packages. After removing a feature. The `--min-confidence 80` flag cuts down on false positives but you'll still get some. Vulture can't see dynamic registration (like the handler dicts) so anything populated via `importlib` or dict assignment will show up as "unused."

**Known false positives in this codebase:**
- Handler functions registered dynamically via `HANDLERS` dicts
- `conftest.py` fixtures (used by pytest, not by imports)
- `__init__.py` re-exports consumed by entry points

### deptry

**What it catches:** Dependencies declared in `pyproject.toml` but not imported anywhere (dead deps). Dependencies imported in code but not declared (missing deps, relying on transitive installs). Dev dependencies imported in production code.

**When to run it:** After adding or removing packages. After moving code between `app/` and `packages/`. Catches the case where you remove the last import of a library but forget to remove it from `pyproject.toml`.

### import-linter

**What it catches:** Architectural boundary violations. You define contracts ("gaas-sdk must never import from app") and it flags violations.

**When to run it:** This one needs a config file. Add to `pyproject.toml`:

```toml
[tool.importlinter]
root_packages = ["app", "gaas_sdk", "gaas_email", "gaas_github", "gaas_gemini"]

[[tool.importlinter.contracts]]
name = "SDK does not import app"
type = "forbidden"
source_modules = ["gaas_sdk"]
forbidden_modules = ["app"]

[[tool.importlinter.contracts]]
name = "Integrations do not import app"
type = "forbidden"
source_modules = ["gaas_email", "gaas_github", "gaas_gemini"]
forbidden_modules = ["app"]
```

Then: `uv run lint-imports`

The two contracts above enforce the architectural boundary that was established when the SDK was extracted. Integrations talk to the app through `gaas_sdk.runtime`, never by importing `app.*` directly. If someone accidentally adds `from app.config import config` inside an integration package, this catches it.

Add more contracts as the architecture evolves.

### bandit

**What it catches:** Common security issues. Subprocess calls with `shell=True`, hardcoded passwords, insecure temp file creation, use of `eval`/`exec`, weak crypto.

**When to run it:** Before releases. After adding any code that touches subprocess execution, file I/O, or network calls. The script executor (`app/actions/script.py`) will always flag because it runs shell commands by design. Suppress those specific findings with `# nosec` comments if they've been reviewed.

The `-q` flag suppresses the per-file noise and shows only findings.

### ruff

**What it catches:** Broad linting. Covers most of what flake8 and pylint catch but runs in under a second. Import ordering, unused imports, f-string issues, exception handling anti-patterns, type annotation issues.

**Config:** `pyproject.toml` under `[tool.ruff]`. Line length is 100. Rule sets: `E, F, W, ANN, UP, RUF, B, SIM`. `ANN401` (disallowing `Any`) and `ANN204` (missing `__init__` return type) are globally ignored because they flag too many legitimate uses in Pydantic validators and protocol definitions. Tests are exempt from all `ANN` rules. `app/` and integration packages are exempt from function-level annotation rules (`ANN001-003`, `ANN201-202`) to match the graduated mypy strictness.

**When to run it:** On every PR if you want, it's fast enough. Or periodically as a sweep.

### pytest-cov

**What it catches:** Lines and branches your tests don't execute. The `--cov-report=term-missing` flag shows exact line numbers that aren't covered.

**When to run it:** Before writing new tests, to see where coverage is thin. After a refactor, to make sure you didn't orphan test coverage. Don't chase 100%. This project's testing philosophy is rigor proportional to irreversibility, so 80% coverage on a read-only parser matters less than 100% coverage on the dispatch layer.

To generate an HTML report for browsing:

```bash
uv run pytest --cov=app --cov-report=html
open htmlcov/index.html
```

