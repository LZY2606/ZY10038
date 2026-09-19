"""Mutation stages as tests: each simulated defect must be caught by the
boundary matrix, and the worktree must be clean afterwards."""

import pytest

from parsimonious.tests.mutation_testing import (
    MUTATIONS, git_worktree_status, run_mutation)

MUTATION_BY_NAME = {m.name: m for m in MUTATIONS}


@pytest.mark.parametrize('name', [m.name for m in MUTATIONS])
def test_mutation_is_caught(name):
    result = run_mutation(MUTATION_BY_NAME[name])
    assert not result.abnormal, result.report
    assert result.caught, result.report


def test_worktree_clean_after_mutation_stages():
    assert git_worktree_status() == ''
