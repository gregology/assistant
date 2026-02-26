# Web UI Architecture

GaaS currently requires users to hand-edit `config.yaml`. That works for developers but creates a real barrier for anyone else. Home Assistant solved this well by offering YAML editing, UI forms, and in-UI YAML editors. Users pick their comfort level.

The goal is to add a web UI that lives alongside the YAML config, not above it. The YAML file stays as the source of truth. The UI reads it, displays it, and can write back to it while preserving comments and formatting.

## Research

We looked at how five projects handle the "config file vs UI" problem, evaluated three frontend architecture approaches, and tested two YAML round-tripping libraries.

### How others do it

**Home Assistant** went through the most visible version of this struggle. Their Config Flow system generates forms from schemas (voluptuous + selectors). Developers declare a schema, the frontend auto-renders the form. Smart separation between "data" (setup-time credentials) and "options" (runtime tunables).

The controversial part: HA decided that Config Flow and YAML cannot coexist for the same integration. Power users lost version control, bulk editing, diffing. Architecture issue #399 captured the backlash. The team held firm (ADR-0010). Years later the community is still split on it.

HA does not preserve YAML comments on round-trip. They chose not to solve it because their strategic direction was UI-first.

Lesson: don't force users to pick one. YAML and UI should be peers.

**Grafana** has the cleanest model. Dashboards can be provisioned from files (shown read-only in the UI) or created in the UI (stored in a database). File-originated content is displayed but never mutated by the UI. This completely sidesteps the round-trip fidelity problem.

**Node-RED** went the other direction. The UI is the primary interface, the file (`flows.json`) is just persistence. The file format is optimized for machine consumption, not hand-editing.

**n8n** is instructive mostly as a cautionary tale. The Vue.js frontend is over 100K lines of TypeScript. They need a full-time frontend team. That is not us.

**Portainer** uses an import/export model. Upload a compose file, Portainer ingests it, you edit via UI, you can re-export. The export may not match the original. This is fine for their use case but violates GaaS's "filesystem is the database" principle.

### JSON Schema form generation

The JS ecosystem has two mature options. **react-jsonschema-form** (RJSF, ~15.6k GitHub stars) takes a JSON Schema + uiSchema and renders a full form with validation. Handles nested objects, arrays, oneOf/anyOf. Requires React. **JSON Forms** (EclipseSource) takes a separate data schema and UI schema, with an elegant renderer/tester priority system for extensibility. Also requires a JS framework.

On the Python/server-side, there's nothing mature. **FastUI** (by the Pydantic team) is closest. You define components as Pydantic models, a pre-built React app renders them. Young, limited component set, custom widgets require React knowledge. **fh-pydantic-form** generates HTML from Pydantic models for FastHTML. Useful reference but not production-grade.

No existing library takes a JSON Schema (or Pydantic model) and emits server-rendered HTML forms suitable for HTMX. That piece would be custom code.

### YAML round-tripping

**ruamel.yaml** is the only real option. It preserves comments, key ordering, block style, quoting. Must use `typ='rt'` mode. The C extension silently drops comments, so you need the pure-Python path. Deleting list elements can orphan adjacent comments. No stable public API for comment manipulation. But it works.

**StrictYAML** would be ideal except it rejects custom tags. `!secret` is a dealbreaker.

**PyYAML** (current dependency) strips all comments and formatting. Not viable for round-trip editing.

### Frontend approaches

We evaluated three stacks against GaaS's constraints: Python-developer team, FastAPI + Jinja2 already in the project, maintainability over features, no desire for a JavaScript build toolchain.

| | HTMX + Jinja2 | Alpine.js + HTMX | Full SPA |
|---|---|---|---|
| Build toolchain | None | None (CDN) | Node.js + npm + bundler |
| Python dev accessibility | Excellent | Good | Poor |
| Nested form capability | Moderate | Good | Excellent |
| New dependencies | 0 | 1 (~14KB) | Dozens |
| Maintenance burden | Low | Low-Medium | High |
| Testing | Server tests only | Needs E2E for Alpine bits | Two full test suites |

Pure HTMX means every form interaction is a server round-trip. Adding an automation rule = HTTP request + partial render. That is fine for flat config but gets chatty with deeply nested structures.

Alpine.js + HTMX is the sweet spot. HTMX for page structure and data loading, Alpine for client-side form state in complex sections (automations, classifications). Adding/removing rules is instant on the client. Saving is an explicit action that round-trips to the server. Alpine loads from a CDN, 14KB, no build step.

A full SPA produces the best UX for nested forms but the worst maintenance profile. Two languages, two runtimes, two test suites, JS ecosystem churn. Every config schema change requires updating both the API and the frontend. Not worth it for a config editor.

## Chosen approach: phased, Grafana-style

Start read-only. Add editing incrementally. Each phase is independently useful.

### Phase 1: Config viewer (HTMX + Jinja2 + DaisyUI)

Render the full config as collapsible HTML. No YAML writing. No `ruamel.yaml` dependency yet.

What it shows:
- Full config with collapsible sections per integration and platform
- Provenance badges on automation rules (`rule`, `llm`, `hybrid`)
- Expanded classification shorthands (what `human: "is this urgent?"` actually becomes)
- Inline validation warnings and errors
- Integration manifests (available platforms, schema docs)
- Task queue status (pending/active/done/failed counts, inspect payloads)
- Audit log browser (daily markdown logs)

Why start here: a viewer carries zero risk of mangling user files. It validates the template structure and component choices. It is immediately useful for anyone debugging their config. And it forces us to build the schema-to-HTML rendering that editing will need.

DaisyUI provides collapse/accordion for nested sections, tabs for switching between integrations, badges for provenance. It loads via CDN on top of Tailwind. No build step.

### Phase 2: Simple editing (add Alpine.js)

Add editing for flat config sections where the form complexity is low:
- LLM profiles (key-value forms)
- Directory paths (text inputs)
- Integration-level config (schedule, LLM profile selection)
- Script definitions (name, description, inputs, shell code via textarea)

Add `ruamel.yaml` dependency here. Implement round-trip save that preserves comments and formatting. Add an inline YAML editor (raw textarea showing the relevant YAML section) as an escape hatch for power users who'd rather type YAML.

`!secret` values are displayed as masked placeholders. Never resolved in the UI.

### Phase 3: Complex editing + onboarding (Alpine.js components)

- Classification editor: add/edit/remove with type selector (confidence/boolean/enum)
- Automation rule builder: when/then pairs with operator selection, provenance preview
- Integration setup wizard: step-by-step config creation, like HA's Config Flow
- Dry-run mode: preview what an automation rule would match against recent items

The onboarding wizard naturally emerges from the editing components. A "new integration" flow reuses the same forms. This is also where enabling the UI during install makes sense. The install procedure could optionally run the Phase 3 wizard to generate the initial `config.yaml`.

### UI Features

### Triggering integrations from the Dashboard

The Dashboard provides a "Run Now" button for each configured integration. This uses HTMX to trigger a POST request to `/ui/integrations/{integration_id}/run`.

Why: manual triggering is the most common user request for debugging and immediate feedback. While the scheduler handles periodic runs, the UI must provide a "pull" mechanism. The UI route wraps the existing `_run_integration` logic and returns an HTML partial with the enqueued task IDs for immediate user feedback.

### Alpha status visibility

The UI includes a high-visibility "ALPHA" corner ribbon and fixed "Restart Required" banner.

Why: clear visual signaling prevents users from mistaking an experimental tool for a production-ready one. The ribbon is a permanent fixture in `base.html`, while the banner uses HTMX Out-of-Band (OOB) swaps to appear or update whenever a configuration change is saved.

## API layer

The UI talks to FastAPI endpoints that read/write config. The endpoints are the validation boundary. Direct file editing by the UI is not allowed. This aligns with HA's position that the API boundary is the right abstraction layer.

Proposed endpoints (will evolve):

```
GET  /ui/                           # Main UI page
GET  /ui/config                     # Full config viewer
GET  /ui/config/integrations/{id}   # Single integration detail
GET  /ui/queue                      # Task queue viewer
GET  /ui/logs                       # Audit log browser
GET  /ui/logs/{date}                # Single day's log

# Phase 2+
POST /ui/config/llms/{name}         # Create/update LLM profile
DELETE /ui/config/llms/{name}       # Delete LLM profile
POST /ui/config/integrations/{id}   # Update integration config
POST /ui/config/scripts/{name}      # Create/update script
```

All mutating endpoints validate via Pydantic, write via `ruamel.yaml`, and return the updated HTML partial (for HTMX swap) rather than JSON.

### What this means for new dependencies

Phase 1 adds nothing. Jinja2 is already a dependency. DaisyUI and HTMX are loaded from CDN (or vendored as static files).

Phase 2 adds `ruamel.yaml` and Alpine.js (CDN/vendored).

No phase requires Node.js, npm, or a JavaScript build step.

## Alternatives we considered but rejected

### FastUI (Pydantic-native)

Everything defined in Python, pre-built React app renders it. Appealing because GaaS already has Pydantic models for everything. But FastUI is young, the component set is limited, and custom widgets require React knowledge, which defeats the "all Python" premise. Non-standard UI patterns like `!yolo` badges or provenance display would require forking or upstream contributions.

### Full SPA with RJSF

The strongest form-editing experience. RJSF handles nested JSON Schema forms with validation, conditional fields, custom widgets. But it requires React, a build toolchain, and ongoing JS maintenance. The cost is disproportionate to the value for a config editor.

### Pure HTMX (no Alpine.js)

Works for flat config. Gets painful for deeply nested forms because every add/remove/conditional-show requires a server round-trip and a dedicated partial template. Estimate: 15-20 Jinja2 partials and corresponding endpoints just for the automation rule editor. Adding Alpine for client-side form state in complex sections is a better tradeoff.

## Open questions

- Should the UI be an optional dependency group (`uv sync --group ui`) or always available?
- Should Phase 1 ship with the task queue viewer, or is that a separate effort?
- How much of the onboarding wizard (Phase 3) should be CLI-based vs UI-based?
- Should the inline YAML editor use a syntax highlighting library (CodeMirror, Monaco) or a plain textarea?
