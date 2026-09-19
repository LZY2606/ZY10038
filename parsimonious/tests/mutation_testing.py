"""Automated mutation harness for the boundary matrix.

Simulates three defect classes in ``parsimonious/expressions.py``:

1. ``farthest_error_becomes_last`` -- the farthest-failure rule in
   ``Expression.match_core`` is replaced by "last failure wins".
2. ``zero_width_repeat_may_continue`` -- the guard that stops a quantifier
   whose member matched zero-width is disabled, so nullable repeats loop.
3. ``token_eof_off_by_one`` -- ``TokenMatcher`` reads the token one slot
   past the current position, shifting EOF behavior by one.

Each stage patches the source, runs the boundary-matrix tests in a
subprocess, and requires the mutated suite to fail in the expected tests.
The mutated source is always restored afterwards, and the git worktree is
verified clean at the end. If a mutated test process exits abnormally
(timeout or signal), the stage name is reported.

Run directly with::

    python3 -m parsimonious.tests.mutation_testing
"""

import os
import subprocess
import sys
from dataclasses import dataclass, field

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
EXPRESSIONS = os.path.join(REPO_ROOT, 'parsimonious', 'expressions.py')
BOUNDARY_TESTS = os.path.join('parsimonious', 'tests',
                              'test_boundary_matrix.py')
MUTATED_SUITE_TIMEOUT = 120  # seconds; the suite itself guards loops at 1 s


@dataclass(frozen=True)
class Mutation:
    name: str
    anchor: str           # exact source text to replace (must occur once)
    replacement: str
    expected_failures: tuple  # test names that must fail under the mutation


MUTATIONS = (
    Mutation(
        name='farthest_error_becomes_last',
        anchor='if node is None and pos >= error.pos and (',
        replacement='if node is None and True and (  # mutated: last error '
                    'wins instead of farthest',
        expected_failures=('test_farthest_error_wins_over_last_error',)),
    Mutation(
        name='zero_width_repeat_may_continue',
        anchor='if len(children) >= self.min and length == 0:  # Don\'t '
               'loop infinitely',
        replacement='if False:  # mutated: zero-width repeats keep looping',
        expected_failures=('test_nullable_literal_in_star_terminates',)),
    Mutation(
        name='token_eof_off_by_one',
        anchor='if token_list[pos].type == self.literal:',
        replacement='if token_list[pos + 1].type == self.literal:  # '
                    'mutated: EOF shifted by one',
        expected_failures=('test_token_grammar_match_vs_parse_on_last_token',)),
)


@dataclass
class MutationResult:
    name: str
    caught: bool
    abnormal: bool = False
    report: str = ''
    failed_tests: list = field(default_factory=list)


def _apply_mutation(mutation):
    with open(EXPRESSIONS) as f:
        source = f.read()
    count = source.count(mutation.anchor)
    if count != 1:
        raise RuntimeError(
            'mutation stage %s: anchor occurs %d times (expected 1); '
            'refusing to mutate' % (mutation.name, count))
    with open(EXPRESSIONS, 'w') as f:
        f.write(source.replace(mutation.anchor, mutation.replacement))
    return source


def _restore(original_source):
    with open(EXPRESSIONS, 'w') as f:
        f.write(original_source)


def _failed_test_names(output):
    return [line.split('::')[-1].split(' ')[0].strip()
            for line in output.splitlines()
            if line.startswith('FAILED ')]


def run_mutation(mutation):
    """Run one mutation stage; always restores the source file."""
    original = _apply_mutation(mutation)
    try:
        try:
            proc = subprocess.run(
                [sys.executable, '-m', 'pytest', BOUNDARY_TESTS,
                 '-q', '--tb=no', '-rf', '-p', 'no:cacheprovider'],
                capture_output=True, text=True,
                timeout=MUTATED_SUITE_TIMEOUT, cwd=REPO_ROOT)
        except subprocess.TimeoutExpired:
            return MutationResult(
                name=mutation.name, caught=False, abnormal=True,
                report='mutation stage %s: mutated test process exceeded '
                       '%ds and was killed' % (mutation.name,
                                               MUTATED_SUITE_TIMEOUT))
        if proc.returncode < 0:
            return MutationResult(
                name=mutation.name, caught=False, abnormal=True,
                report='mutation stage %s: mutated test process died from '
                       'signal %d' % (mutation.name, -proc.returncode))
        output = proc.stdout + proc.stderr
        failed = _failed_test_names(output)
        if proc.returncode == 0:
            return MutationResult(
                name=mutation.name, caught=False,
                report='mutation stage %s: NOT caught; boundary suite '
                       'passed against mutated source' % mutation.name)
        missing = [t for t in mutation.expected_failures
                   if t not in failed]
        if missing:
            return MutationResult(
                name=mutation.name, caught=False,
                failed_tests=failed,
                report='mutation stage %s: suite failed but expected tests '
                       '%r did not; failed: %r' % (mutation.name, missing,
                                                   failed))
        return MutationResult(
            name=mutation.name, caught=True, failed_tests=failed,
            report='mutation stage %s: caught by %r' % (
                mutation.name, mutation.expected_failures))
    finally:
        _restore(original)


def git_worktree_status():
    """Modifications to tracked files only.

    Untracked files (the new test modules themselves) are intentional
    additions, not mutation residue; residue means a tracked source file
    left modified after the stages ran.
    """
    proc = subprocess.run(['git', 'status', '--porcelain',
                           '--untracked-files=no'],
                          capture_output=True, text=True, cwd=REPO_ROOT)
    return proc.stdout.strip()


def run_all_mutations():
    results = [run_mutation(m) for m in MUTATIONS]
    leftover = git_worktree_status()
    return results, leftover


def main():
    failures = 0
    for result in run_all_mutations()[0]:
        print(result.report)
        if not result.caught:
            failures += 1
    leftover = git_worktree_status()
    if leftover:
        failures += 1
        print('git worktree not clean after mutation stages:\n%s' % leftover)
    else:
        print('git worktree clean after mutation stages')
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
