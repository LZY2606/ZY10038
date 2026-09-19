"""Automated mutation testing entry point.

Simulates three defect classes in the parser core, runs the boundary
tests that are supposed to catch each one in a subprocess, and verifies
they do. Every stage (apply, run, restore, workspace check) is reported
separately so an abnormal exit of the mutated test run still says *which*
stage blew up. All files are restored afterwards and the git worktree is
verified clean.

Run directly for a human-readable report::

    python3 -m parsimonious.tests.mutation_harness [mutation ...]
"""

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
BOUNDARY_TESTS = 'parsimonious/tests/test_boundary_matrix.py'

#: How long a mutated test run may take before we declare it abnormal.
MUTATED_RUN_TIMEOUT = 120


class Mutation:
    """A single-source-line defect plus the tests that must catch it."""

    def __init__(self, name, path, original, replacement,
                 expected_failures, description):
        self.name = name
        self.path = path
        self.original = original
        self.replacement = replacement
        self.expected_failures = expected_failures
        self.description = description

    @property
    def node_ids(self):
        return ['%s::%s' % (BOUNDARY_TESTS, test)
                for test in self.expected_failures]


MUTATIONS = {mutation.name: mutation for mutation in [
    Mutation(
        name='furthest_error_becomes_last',
        path='parsimonious/expressions.py',
        original='if node is None and pos >= error.pos and (',
        replacement='if node is None and True and (',
        expected_failures=[
            'TestSharedPrefixes::test_error_reports_furthest_position_not_last',
            'TestSharedPrefixes::test_error_names_furthest_named_rule',
        ],
        description='error reporting records the last failure instead of '
                    'the furthest one'),
    Mutation(
        name='zero_width_repetition_continues',
        path='parsimonious/expressions.py',
        original="if len(children) >= self.min and length == 0:  # Don't "
                 "loop infinitely",
        replacement='if False:  # mutated: zero-width repetitions continue',
        expected_failures=[
            'TestNullableQuantifiers::test_nullable_regex_inside_star_terminates',
            'TestNullableQuantifiers::test_nullable_regex_inside_plus_terminates',
            'TestNullableQuantifiers::test_optional_inside_star_stops_at_first_nonmatch',
            'TestNullableQuantifiers::test_nested_nullable_quantifiers_terminate',
        ],
        description='the zero-width guard in Quantifier is disabled, so '
                    'nullable repetitions spin forever (the subprocess '
                    'guard must time each one out within 1s)'),
    Mutation(
        name='token_grammar_eof_off_by_one',
        path='parsimonious/expressions.py',
        original='if token_list[pos].type == self.literal:',
        replacement='if token_list[pos + 1].type == self.literal:',
        expected_failures=[
            'TestTokenGrammarBoundary::test_last_token_matches_exactly_at_eof',
            'TestTokenGrammarBoundary::test_optional_token_at_eof_is_skipped',
            'TestTokenGrammarBoundary::test_match_vs_parse_with_trailing_tokens',
        ],
        description='TokenMatcher reads one token past the current '
                    'position, shifting EOF by one'),
]}


class MutationStageError(AssertionError):
    """A mutation stage failed or exited abnormally."""

    def __init__(self, mutation_name, stage, detail):
        self.mutation_name = mutation_name
        self.stage = stage
        super().__init__(
            'mutation {!r} failed at stage {!r}: {}'.format(
                mutation_name, stage, detail))


class MutationNotCaught(AssertionError):
    """The mutated code passed the tests that should have caught it."""

    def __init__(self, mutation_name, output):
        super().__init__(
            'mutation {!r} was NOT caught; expected tests passed.\n'
            'output:\n{}'.format(mutation_name, output))


def _read(path):
    return (REPO_ROOT / path).read_text()


def _write(path, content):
    (REPO_ROOT / path).write_text(content)


def apply_mutation(mutation):
    """Swap the healthy line for the mutated one. Stage: ``apply``."""
    content = _read(mutation.path)
    if content.count(mutation.original) != 1:
        raise MutationStageError(
            mutation.name, 'apply',
            'expected exactly one occurrence of {!r} in {}'.format(
                mutation.original, mutation.path))
    _write(mutation.path, content.replace(
        mutation.original, mutation.replacement))


def restore_mutation(mutation, original_content):
    """Write the original file content back. Stage: ``restore``."""
    _write(mutation.path, original_content)
    if _read(mutation.path) != original_content:
        raise MutationStageError(
            mutation.name, 'restore',
            'could not restore {}'.format(mutation.path))


def run_expected_failures(mutation):
    """Run the catching tests against the mutated tree. Stage: ``run``.

    Return pytest's stdout. The mutated run exiting abnormally (crash or
    timeout) is reported with the stage name attached.
    """
    command = [sys.executable, '-m', 'pytest', '-q', '-rf', '--no-header',
               '-p', 'no:cacheprovider'] + mutation.node_ids
    try:
        proc = subprocess.run(command, cwd=str(REPO_ROOT),
                              capture_output=True, text=True,
                              timeout=MUTATED_RUN_TIMEOUT)
    except subprocess.TimeoutExpired:
        raise MutationStageError(
            mutation.name, 'run',
            'mutated test run did not finish within {}s'.format(
                MUTATED_RUN_TIMEOUT))
    output = proc.stdout + proc.stderr
    if proc.returncode == 0:
        raise MutationNotCaught(mutation.name, output)
    if proc.returncode != 1:
        raise MutationStageError(
            mutation.name, 'run',
            'mutated pytest exited abnormally with code {}.\n'
            'output:\n{}'.format(proc.returncode, output))
    failed_lines = [line for line in output.splitlines()
                    if line.startswith('FAILED')]
    missing = [test for test in mutation.expected_failures
               if not any(test in line for line in failed_lines)]
    if missing:
        raise MutationStageError(
            mutation.name, 'run',
            'these tests were expected to fail but did not appear as '
            'failures: {}\noutput:\n{}'.format(missing, output))
    return output


def _workspace_status(mutation):
    proc = subprocess.run(['git', 'status', '--porcelain'],
                          cwd=str(REPO_ROOT), capture_output=True, text=True)
    if proc.returncode != 0:
        raise MutationStageError(
            mutation.name, 'workspace',
            'git status failed: {}'.format(proc.stderr))
    return proc.stdout


def check_workspace_clean(mutation, baseline_status):
    """Verify the mutation left no residue in the git worktree.

    Compares against the status captured before the mutation ran, so
    pre-existing untracked files (like these very tests) don't count as
    leftovers. Stage: ``workspace``.
    """
    after = _workspace_status(mutation)
    if after != baseline_status:
        raise MutationStageError(
            mutation.name, 'workspace',
            'git worktree changed during mutation run.\n'
            'before:\n{}\nafter:\n{}'.format(baseline_status, after))


def run_mutation(name):
    """Apply, verify, and revert one mutation. Return a report line."""
    mutation = MUTATIONS[name]
    baseline_status = _workspace_status(mutation)
    original_content = _read(mutation.path)
    apply_mutation(mutation)
    try:
        run_expected_failures(mutation)
    finally:
        restore_mutation(mutation, original_content)
    check_workspace_clean(mutation, baseline_status)
    return 'caught: {} ({})'.format(name, mutation.description)


def main(argv=None):
    names = argv if argv else sorted(MUTATIONS)
    unknown = [name for name in names if name not in MUTATIONS]
    if unknown:
        print('unknown mutations: {}'.format(unknown), file=sys.stderr)
        return 2
    failures = 0
    for name in names:
        try:
            print(run_mutation(name))
        except (MutationStageError, MutationNotCaught) as error:
            failures += 1
            print('FAILED: {}'.format(error))
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
