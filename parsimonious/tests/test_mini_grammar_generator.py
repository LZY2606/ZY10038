"""Property-style tests driven by the deterministic mini grammar generator.

Every sample compares the parse/match consumption relation and applies
three metamorphic transforms (harmless grouping, an equivalent duplicated
single branch, an explicit end-of-input marker). Failures report the seed,
the generated grammar, the input code points and a minimal reproducible
input. Generation and evaluation are deterministic: two consecutive runs
yield identical order and results.
"""

import pytest

from parsimonious.tests.mini_grammar_generator import (
    INPUTS, SAMPLE_COUNT, check_metamorphic_variants,
    check_parse_match_relation, corpus, format_failure, fuzz_seed)


def _recheck(sample, variant):
    """Return a predicate telling whether ``text`` still triggers the
    failure, used to shrink to a minimal reproducible input."""
    check = (check_metamorphic_variants if variant
             else check_parse_match_relation)

    def still_fails(text):
        failures = check(sample, inputs=(text,))
        return any(f['variant'] == variant for f in failures)

    return still_fails


def _assert_no_failures(samples, check):
    for sample in samples:
        failures = check(sample)
        if failures:
            first = failures[0]
            pytest.fail(format_failure(
                first, still_fails=_recheck(sample, first['variant'])),
                pytrace=False)


def test_generated_corpus_parse_match_relation():
    """match consumes a prefix; parse succeeds iff the prefix is everything,
    and IncompleteParseError reports exactly the match end position."""
    _assert_no_failures(corpus(fuzz_seed()), check_parse_match_relation)


def test_generated_corpus_metamorphic_variants():
    """Grouping and duplicated branches preserve outcomes; an explicit EOF
    marker makes match behave like parse."""
    _assert_no_failures(corpus(fuzz_seed()), check_metamorphic_variants)


def test_generator_output_is_deterministic():
    """Two consecutive corpora from one seed are identical, in order."""
    seed = fuzz_seed()
    first = corpus(seed)
    second = corpus(seed)
    assert [s.grammar for s in first] == [s.grammar for s in second]
    assert len(first) == SAMPLE_COUNT


def test_evaluation_is_deterministic_across_two_runs():
    """Two consecutive evaluations produce identical ordered outcomes."""
    from parsimonious.grammar import Grammar
    from parsimonious.tests.mini_grammar_generator import match_outcome

    def run():
        outcomes = []
        for sample in corpus(fuzz_seed()):
            grammar = Grammar(sample.grammar)
            outcomes.append(tuple(match_outcome(grammar, text)
                                  for text in INPUTS))
        return outcomes

    assert run() == run()
