# gaas-bot Decisions Log

Architectural decisions for gaas-bot, recorded when they're made. Each entry captures the context and constraints at the time. If constraints change, update or retire the decision.

---

## 001: Single structured output for audit findings (2026-03-05)

**Context**: Audit commands (docs, refactor, tests) need to produce GitHub issues from their findings. Three approaches were considered:

- **Option A (chosen)**: Single Claude agent call with `output_model=AuditReport` returning a list of findings. Python iterates findings and creates issues programmatically.
- **Option B**: Two-phase agent — Phase 1 explores freely with tools, Phase 2 receives Phase 1's text and returns structured output. Decouples exploration from formatting.
- **Option C**: Claude writes `to_review/*.md` files with YAML frontmatter, Python parses them post-hoc.

**Decision**: Option A — single structured output.

**Rationale**: Audit commands are read-only exploration. Claude doesn't need Write tool access if it returns structured output, which is a trust improvement (the agent literally can't modify the worktree). One agent call, one structured output, one deterministic creation loop — fewest moving parts, easiest to test, most auditable. This aligns with the project principle "log what the agent does, don't ask it what it did" — structured output is a schema-enforced contract, not a hope that the LLM wrote valid files.

**Assumption**: This decision assumes Claude can produce quality structured output for audit-sized reports (up to 10 findings with detailed markdown bodies). If structured output quality degrades — truncated findings, malformed JSON, or the model struggling with the schema complexity — Option B (two-phase: explore then structure) is the fallback. The two-phase approach lets Claude explore freely in Phase 1 and then format in Phase 2 as a pure structuring task, which is more resilient to output quality issues.

**Revisit when**: Structured output from the agent SDK proves unreliable for reports of this size, or audit prompts need to use tools like Write during exploration that conflict with structured output mode.
