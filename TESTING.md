# Testing

## Local run

From the repository root:

```
python3 -m pip install -e '.[testing]'   # one-time setup
python3 -m pytest -q                      # full acceptance run
```

No external services, environment variables, or network access are
required. The acceptance run includes the boundary matrix, the seeded
grammar-generator checks, and the mutation stages.

## Boundary matrix

`parsimonious/tests/test_boundary_matrix.py` pins composed-expression
behavior: nullable expressions inside `*`/`+`/nested quantifiers,
lookahead combined with zero-width regexes, TokenGrammar on empty token
streams / the last token / EOF, forward references, direct and indirect
left recursion, shared prefixes between alternatives, Unicode code point
indexing, and NodeVisitor ordering. Failure cases assert the farthest
error position, the rule name, and a stable context fragment; success
cases assert consumed length and node spans.

## Subprocess protection

Repeats that might not advance (nullable member inside a quantifier) run
in a separate Python subprocess via
`parsimonious/tests/subprocess_guard.py`. The parent decides the outcome
within 1 second (`SUBPROCESS_TIMEOUT_SECONDS`); a timeout fails the test
with "probable non-advancing repeat" instead of relying on any global
suite timeout. To run only these:

```
python3 -m pytest parsimonious/tests/test_boundary_matrix.py -q
```

## Seeded grammar generator

`parsimonious/tests/mini_grammar_generator.py` generates small grammars
from a fixed seed with depth and node-count limits. Only semantically
safe expressions are produced (quantifiers wrap non-nullable atoms only;
lookaheads are never quantified). Each sample checks the parse/match
consumption relation and three metamorphic transforms: harmless grouping,
an equivalent duplicated single branch, and an explicit end-of-input
marker. Failures report the seed, the generated grammar, the input code
points, and a shrunk minimal reproducible input.

Seed replay: set `PARSIMONIOUS_FUZZ_SEED` to the seed from a failure
report (optional; the default seed needs no setup):

```
PARSIMONIOUS_FUZZ_SEED=<seed> python3 -m pytest parsimonious/tests/test_mini_grammar_generator.py -q
```

Two consecutive runs with the same seed produce identical corpora and
identical ordered results (pinned by
`test_generator_output_is_deterministic` and
`test_evaluation_is_deterministic_across_two_runs`).

## Mutation stages

`parsimonious/tests/mutation_testing.py` simulates three defect classes
in `parsimonious/expressions.py`:

1. `farthest_error_becomes_last` — farthest-failure error selection
   becomes last-failure-wins.
2. `zero_width_repeat_may_continue` — the zero-width repeat guard is
   disabled, so nullable quantifiers loop.
3. `token_eof_off_by_one` — `TokenMatcher` reads one token past the
   current position, shifting EOF by one.

Each stage patches the source, runs the boundary matrix in a subprocess,
and requires the expected tests to fail; the source is always restored
and the git worktree is verified free of tracked modifications. An
abnormal exit of the mutated process (timeout or signal) is reported
with the stage name. Run all stages directly:

```
python3 -m parsimonious.tests.mutation_testing
```

The same stages also run as tests
(`parsimonious/tests/test_mutation_testing.py`) inside the standard
`python3 -m pytest -q` acceptance run.
