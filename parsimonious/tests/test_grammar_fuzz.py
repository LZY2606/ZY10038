"""Property-style tests over grammars from ``grammar_fuzz``.

For every generated sample we check:

* the consumption relation between ``parse`` and ``match``,
* that adding harmless grouping preserves consumption,
* that duplicating a single branch (``x`` -> ``(x / x)``) preserves it,
* that appending an explicit EOF sentinel behaves like ``parse``.

Every failure message carries the seed, the generated grammar, the input
code points, and a minimized reproducing input so the failure can be
replayed without re-running the whole matrix.
"""

from parsimonious.exceptions import IncompleteParseError, ParseError
from parsimonious.grammar import Grammar
from parsimonious.tests.grammar_fuzz import effective_seed, generate_samples


def _match_outcome(grammar_text, text):
    """Return ('match', start, end) or ('error', pos) for a match call."""
    grammar = Grammar(grammar_text)
    try:
        node = grammar.match(text)
    except ParseError as error:
        return ('error', error.pos)
    return ('match', node.start, node.end)


def _parse_outcome(grammar_text, text):
    """Return ('parse', start, end) or ('error', kind, pos) for parse."""
    grammar = Grammar(grammar_text)
    try:
        node = grammar.parse(text)
    except IncompleteParseError as error:
        return ('error', 'incomplete', error.pos)
    except ParseError as error:
        return ('error', 'parse', error.pos)
    return ('parse', node.start, node.end)


def check_consumption_relation(sample, text):
    """parse() must succeed exactly when match() consumes everything."""
    match = _match_outcome(sample.grammar_text, text)
    parse = _parse_outcome(sample.grammar_text, text)
    if match[0] == 'error':
        if parse != ('error', 'parse', match[1]):
            return ('match failed at pos %s but parse gave %r'
                    % (match[1], parse))
    elif match[2] == len(text):
        if parse != ('parse', match[1], match[2]):
            return ('match consumed all %d chars but parse gave %r'
                    % (len(text), parse))
    else:
        if parse != ('error', 'incomplete', match[2]):
            return ('match stopped at pos %s but parse gave %r'
                    % (match[2], parse))
    return None


def check_grouping_variant(sample, text):
    """Harmless parentheses must not change consumption."""
    original = _match_outcome(sample.grammar_text, text)
    grouped = _match_outcome(sample.grouping_grammar, text)
    if grouped != original:
        return ('grouping changed outcome: %r -> %r\nvariant grammar:\n%s'
                % (original, grouped, sample.grouping_grammar))
    return None


def check_duplicate_branch_variant(sample, text):
    """``x`` and ``(x / x)`` must consume identically."""
    original = _match_outcome(sample.grammar_text, text)
    duplicated = _match_outcome(sample.duplicate_grammar, text)
    if duplicated != original:
        return ('duplicate branch changed outcome: %r -> %r\nvariant '
                'grammar:\n%s'
                % (original, duplicated, sample.duplicate_grammar))
    return None


def check_eof_variant(sample, text):
    """An explicit EOF sentinel must behave like parse()'s EOF check."""
    original = _match_outcome(sample.grammar_text, text)
    eof = _match_outcome(sample.eof_grammar, text)
    fully_consumed = original[0] == 'match' and original[2] == len(text)
    if fully_consumed:
        if eof != original:
            return ('full consumption %r but EOF variant gave %r\nvariant '
                    'grammar:\n%s' % (original, eof, sample.eof_grammar))
    elif eof[0] != 'error':
        return ('partial consumption %r but EOF variant matched %r\nvariant '
                'grammar:\n%s' % (original, eof, sample.eof_grammar))
    return None


def _minimize_input(check, sample, text):
    """Greedily delete characters while ``check`` still fails."""
    current = text
    changed = True
    while changed:
        changed = False
        for index in range(len(current)):
            candidate = current[:index] + current[index + 1:]
            if check(sample, candidate) is not None:
                current = candidate
                changed = True
                break
    return current


def _run_check(check, samples):
    """Run ``check`` over all samples; fail with full diagnostics."""
    for sample in samples:
        problem = check(sample, sample.text)
        if problem is None:
            continue
        minimal = _minimize_input(check, sample, sample.text)
        raise AssertionError(
            'seed={} sample={}\n'
            'grammar:\n{}\n'
            'input code points: {}\n'
            'problem: {}\n'
            'minimal reproducer input code points: {} '
            '(re-run with the same seed to replay)'.format(
                effective_seed(), sample.index, sample.grammar_text,
                [ord(c) for c in sample.text], problem,
                [ord(c) for c in minimal]))


SAMPLES = generate_samples(effective_seed())


def test_parse_match_consumption_relation():
    _run_check(check_consumption_relation, SAMPLES)


def test_harmless_grouping_preserves_consumption():
    _run_check(check_grouping_variant, SAMPLES)


def test_duplicate_single_branch_preserves_consumption():
    _run_check(check_duplicate_branch_variant, SAMPLES)


def test_explicit_eof_variant_matches_full_consumption():
    _run_check(check_eof_variant, SAMPLES)


def test_generation_is_deterministic_across_runs():
    """Two consecutive runs produce identical order and identical results."""
    first = generate_samples(effective_seed())
    second = generate_samples(effective_seed())
    assert len(first) == len(second)
    for one, two in zip(first, second):
        assert one.grammar_text == two.grammar_text
        assert one.text == two.text
        assert one.grouping_grammar == two.grouping_grammar
        assert one.duplicate_grammar == two.duplicate_grammar
        assert one.eof_grammar == two.eof_grammar
    checks = [check_consumption_relation, check_grouping_variant,
              check_duplicate_branch_variant, check_eof_variant]
    results_first = [[check(sample, sample.text) for sample in first]
                     for check in checks]
    results_second = [[check(sample, sample.text) for sample in second]
                      for check in checks]
    assert results_first == results_second
