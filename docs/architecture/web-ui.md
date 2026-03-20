# Web UI Architecture

Assistant's web UI lives alongside the YAML config, not above it. The YAML file stays as the source of truth. The UI reads it, displays it, and writes back to it while preserving comments and formatting.

## What's built

The UI is a server-rendered application using **HTMX + Jinja2 + DaisyUI + Alpine.js**. No JavaScript build toolchain — DaisyUI, HTMX, and Alpine.js all load from CDN.

### Config viewing

The full config is rendered as collapsible HTML with:

- Collapsible sections per integration and platform
- Provenance badges on automation rules (`rule`, `llm`, `hybrid`)
- Expanded classification shorthands (what `human: "is this urgent?"` actually becomes)
- Inline validation warnings and errors
- Integration manifests (available platforms, schema docs)

### Config editing

Round-trip YAML editing via `ruamel.yaml` (typ='rt') in `app/ui/yaml_rw.py`. All writes preserve comments, key ordering, block style, and quoting.

Editable config sections:

- **LLM profiles** — create, update, delete (key-value forms)
- **Directory paths** — notes, task queue, logs, custom integrations
- **Integration settings** — schedule and LLM profile selection per integration
- **Scripts** — create, update, delete (name, description, inputs, shell code, timeout, output config)

`!secret` values are displayed as masked placeholders and never resolved in the UI.

A raw YAML escape hatch (`save_raw_yaml` / `read_raw_yaml`) lets power users edit the full config as text.

### Task queue viewer

Available at `/ui/queue`. Shows pending, active, done, and failed task counts with payload inspection.

### Chat interface

Available at `/ui/chat`. Uses Alpine.js for client-side state, polling the task API for LLM responses. Messages are routed through the task queue (`chat.message` at priority 1) to serialize LLM access. Commands (e.g., `/clear`) are handled immediately without the LLM.

### Dashboard features

- **"Run Now" button** for each configured integration — triggers a POST to `/ui/integrations/{integration_id}/run`, wraps the existing `_run_integration` logic, and returns an HTML partial with enqueued task IDs
- **Alpha status ribbon** — permanent corner ribbon in `base.html` signaling experimental status
- **"Restart Required" banner** — appears via HTMX Out-of-Band (OOB) swap whenever a config change is saved

### Audit log browser

Available at `/ui/logs`. Browse daily markdown log files with per-day detail view.

## Endpoints

All mutating endpoints validate via Pydantic, write via `ruamel.yaml`, and return the updated HTML partial (for HTMX swap) rather than JSON.

```
GET  /ui/                                          # Dashboard
GET  /ui/chat                                      # Chat interface
GET  /ui/config                                    # Config viewer
GET  /ui/queue                                     # Task queue viewer
GET  /ui/logs                                      # Audit log browser
GET  /ui/logs/{date}                               # Single day's log

POST   /ui/config/llms/{name}                      # Create/update LLM profile
DELETE /ui/config/llms/{name}                       # Delete LLM profile
POST   /ui/config/directories                      # Update directory paths
POST   /ui/config/integrations/{index}/settings     # Update integration settings
POST   /ui/config/scripts/{name}                    # Create/update script
DELETE /ui/config/scripts/{name}                    # Delete script
POST   /ui/config/yaml                              # Raw YAML save
POST   /ui/integrations/{integration_id}/run        # Trigger integration run
POST   /ui/system/restart                           # Restart server
```

## Dependencies

- **Jinja2** — already a project dependency
- **DaisyUI + Tailwind** — loaded from CDN, no build step
- **HTMX** — loaded from CDN
- **Alpine.js** — loaded from CDN (~14KB), used for client-side form state in complex sections and the chat interface
- **ruamel.yaml** — core dependency (`pyproject.toml`), used for round-trip YAML editing that preserves comments and formatting

No phase requires Node.js, npm, or a JavaScript build step.

## Background

### Research

We looked at how five projects handle the "config file vs UI" problem, evaluated three frontend architecture approaches, and tested two YAML round-tripping libraries.

#### How others do it

**Home Assistant** went through the most visible version of this struggle. Their Config Flow system generates forms from schemas (voluptuous + selectors). Developers declare a schema, the frontend auto-renders the form. Smart separation between "data" (setup-time credentials) and "options" (runtime tunables).

The controversial part: HA decided that Config Flow and YAML cannot coexist for the same integration. Power users lost version control, bulk editing, diffing. Architecture issue #399 captured the backlash. The team held firm (ADR-0010). Years later the community is still split on it.

HA does not preserve YAML comments on round-trip. They chose not to solve it because their strategic direction was UI-first.

Lesson: don't force users to pick one. YAML and UI should be peers.

**Grafana** has the cleanest model. Dashboards can be provisioned from files (shown read-only in the UI) or created in the UI (stored in a database). File-originated content is displayed but never mutated by the UI. This completely sidesteps the round-trip fidelity problem.

**Node-RED** went the other direction. The UI is the primary interface, the file (`flows.json`) is just persistence. The file format is optimized for machine consumption, not hand-editing.

**n8n** is instructive mostly as a cautionary tale. The Vue.js frontend is over 100K lines of TypeScript. They need a full-time frontend team. That is not us.

**Portainer** uses an import/export model. Upload a compose file, Portainer ingests it, you edit via UI, you can re-export. The export may not match the original. This is fine for their use case but violates Assistant's "filesystem is the database" principle.

#### JSON Schema form generation

The JS ecosystem has two mature options. **react-jsonschema-form** (RJSF, ~15.6k GitHub stars) takes a JSON Schema + uiSchema and renders a full form with validation. Handles nested objects, arrays, oneOf/anyOf. Requires React. **JSON Forms** (EclipseSource) takes a separate data schema and UI schema, with an elegant renderer/tester priority system for extensibility. Also requires a JS framework.

On the Python/server-side, there's nothing mature. **FastUI** (by the Pydantic team) is closest. You define components as Pydantic models, a pre-built React app renders them. Young, limited component set, custom widgets require React knowledge. **fh-pydantic-form** generates HTML from Pydantic models for FastHTML. Useful reference but not production-grade.

No existing library takes a JSON Schema (or Pydantic model) and emits server-rendered HTML forms suitable for HTMX. That piece would be custom code.

#### YAML round-tripping

**ruamel.yaml** is the only real option. It preserves comments, key ordering, block style, quoting. Must use `typ='rt'` mode. The C extension silently drops comments, so you need the pure-Python path. Deleting list elements can orphan adjacent comments. No stable public API for comment manipulation. But it works.

**StrictYAML** would be ideal except it rejects custom tags. `!secret` is a dealbreaker.

**PyYAML** (current dependency) strips all comments and formatting. Not viable for round-trip editing.

#### Frontend approaches

We evaluated three stacks against Assistant's constraints: Python-developer team, FastAPI + Jinja2 already in the project, maintainability over features, no desire for a JavaScript build toolchain.

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

## Alternatives considered

### FastUI (Pydantic-native)

Everything defined in Python, pre-built React app renders it. Appealing because Assistant already has Pydantic models for everything. But FastUI is young, the component set is limited, and custom widgets require React knowledge, which defeats the "all Python" premise. Non-standard UI patterns like `!yolo` badges or provenance display would require forking or upstream contributions.

### Full SPA with RJSF

The strongest form-editing experience. RJSF handles nested JSON Schema forms with validation, conditional fields, custom widgets. But it requires React, a build toolchain, and ongoing JS maintenance. The cost is disproportionate to the value for a config editor.

### Pure HTMX (no Alpine.js)

Works for flat config. Gets painful for deeply nested forms because every add/remove/conditional-show requires a server round-trip and a dedicated partial template. Estimate: 15-20 Jinja2 partials and corresponding endpoints just for the automation rule editor. Adding Alpine for client-side form state in complex sections is a better tradeoff.

## Future work

- Classification editor: add/edit/remove with type selector (confidence/boolean/enum)
- Automation rule builder: when/then pairs with operator selection, provenance preview
- Integration setup wizard: step-by-step config creation, like HA's Config Flow
- Dry-run mode: preview what an automation rule would match against recent items
- Onboarding wizard: reuse editing components for initial `config.yaml` generation
