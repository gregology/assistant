# Testing Philosophy

Tests in Assistant exist to enforce the Principle of Reversibility. The purpose of the test suite is not to reduce bugs in general. It is to guarantee that automated actions cannot cause irreversible harm.

## Test rigor follows irreversibility, not complexity

A one-line HTTP POST that unsubscribes from a mailing list deserves more test rigor than 100 lines of email parsing. The parsing can never leak data or trigger an irreversible action. The POST can.

Every action in the system is categorized by reversibility tier before any tests are written. The tier determines the testing strategy:

| Tier | Examples | Testing strategy |
|------|----------|-----------------|
| Read-only | Checking a mailbox, classifying content, reading files | Standard unit tests |
| Soft reversible | Archiving a message, creating a draft | Filesystem snapshot assertions |
| Hard reversible | Marking as spam (may train server filters) | Shadow and dry-run verification |
| Irreversible | Unsubscribing, sending data externally | Property-based safety invariants, mandatory dry run |

## Test the decision boundary, not the LLM

The LLM is non-deterministic. Asserting on its output is meaningless. You cannot write a meaningful test that says "given this email, the LLM should return confidence 0.85." It might return 0.82 next time. Or 0.91. That's fine. The LLM doing its job slightly differently each run is expected behavior, not a bug.

The automation dispatch logic that evaluates classification results and decides which actions to fire is a different story. That code is entirely deterministic. This is where a bug becomes an irreversible action. This is where tests focus.

The dispatch layer (`evaluate_automations`, `check_condition`, `conditions_match`) takes classification results as input and produces action lists as output. For any given input, it always produces the same output. That makes it testable in a meaningful way.

## Property-based testing over example-based testing

A property test that says "for all possible classifications, no unknown action is ever produced" is a fundamentally stronger guarantee than an example test that says "for this one test email, archive was produced."

Assistant uses Hypothesis to generate all possible classification outputs (500 examples per run) and asserts structural properties:

- Only known actions are ever produced
- Action count is bounded (no runaway automation chains)
- Missing classification keys never trigger automations

These properties hold for *all* inputs, not just the handful of examples a developer happened to think of.

## Chaos testing: the LLM being confidently wrong

The dangerous failure mode is not the LLM being unavailable. Retry logic handles that. The dangerous failure is the LLM being confidently wrong.

Picture a model that classifies a phishing email with maximum confidence across the board. Every boolean is true. Every confidence score is 1.0. The priority is "critical" and it "requires a response." What happens?

Chaos tests inject exactly these kinds of faults:

- All confidences maxed to 1.0
- All confidences zeroed to 0.0
- Booleans flipped to their opposite values
- Contradictory classifications (both "human" and "robot" at maximum confidence)
- Out-of-range values (5.0, -1.0)
- Wrong types entirely (string where a boolean should be, number where an enum should be)
- Missing keys, empty results, None values

For every one of these scenarios, the same assertion holds: only allowed actions are produced and the system does not crash. The safety boundaries hold under garbage input, not just well-formed input.

## Filesystem snapshot assertions

The filesystem is the database. Asserting on individual files misses emergent problems like orphaned tasks, files stuck in an intermediate state, or duplicate entries across directories.

Instead, the tests snapshot the entire directory tree and assert on it as a single comparable value. After a full task lifecycle (`enqueue -> dequeue -> complete`), the invariant is: pending is empty, active is empty, done has exactly one file, and the total task count is conserved. No task should exist in two directories at once. No task should disappear without being accounted for.

These structural assertions catch a class of bugs that per-file checks miss, particularly around crash recovery and concurrent access.

## Prompt injection testing

Untrusted content (email bodies, PR descriptions) is fed directly into LLM prompts. Rather than trying to assert that the LLM successfully ignored an injection attempt, the tests feed adversarial inputs through the full pipeline and assert on the *actions* produced.

The prompt-level defenses (random salt markers) are the first barrier. The deterministic dispatch rules are the second. Tests verify that even if the first barrier fails completely, the second barrier still prevents irreversible actions.
