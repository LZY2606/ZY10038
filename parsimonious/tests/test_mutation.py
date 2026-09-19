"""Mutation tests: each simulated defect must be caught by the matrix.

These wrap ``mutation_harness`` so the whole verification is part of the
normal pytest run. Files are always restored and the git worktree is
checked for leftovers, even when a mutation stage fails.
"""

import pytest

from parsimonious.tests.mutation_harness import MUTATIONS, run_mutation


@pytest.mark.parametrize('name', ['furthest_error_becomes_last'])
def test_mutation_furthest_error_becomes_last_is_caught(name):
    assert run_mutation(name).startswith('caught:')


@pytest.mark.parametrize('name', ['zero_width_repetition_continues'])
def test_mutation_zero_width_repetition_is_caught(name):
    assert run_mutation(name).startswith('caught:')


@pytest.mark.parametrize('name', ['token_grammar_eof_off_by_one'])
def test_mutation_token_grammar_eof_off_by_one_is_caught(name):
    assert run_mutation(name).startswith('caught:')


def test_all_declared_mutations_have_tests():
    assert set(MUTATIONS) == {
        'furthest_error_becomes_last',
        'zero_width_repetition_continues',
        'token_grammar_eof_off_by_one',
    }
