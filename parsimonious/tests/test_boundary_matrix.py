"""Reusable boundary matrix for composed Parsimonious expressions.

Covers nullable expressions inside star/plus/nested quantifiers, lookahead
combined with zero-width regexes, TokenGrammar behavior on empty streams /
last token / EOF, forward references, direct and indirect left recursion,
shared prefixes between ordered alternatives, Unicode code point indexing,
and NodeVisitor ordering on empty / nested / failing nodes.

Failure scenarios assert the farthest error position, the rule name and a
stable context fragment. Success scenarios assert consumed length and node
spans. Repeats that might not advance run in a guarded subprocess which the
parent judges within one second (see ``subprocess_guard``).
"""

import pytest

from parsimonious.grammar import Grammar, TokenGrammar
from parsimonious.exceptions import (
    ParseError, IncompleteParseError, LeftRecursionError, VisitationError)
from parsimonious.nodes import NodeVisitor
from parsimonious.utils import Token

from parsimonious.tests.subprocess_guard import run_parse_snippet


PROBE_PRELUDE = '''
import json
from parsimonious.grammar import Grammar
from parsimonious.exceptions import ParseError

def probe(grammar_text, text):
    g = Grammar(grammar_text)
    try:
        node = g.match(text)
    except ParseError as e:
        return {'matched': False, 'error': type(e).__name__, 'pos': e.pos,
                'rule': e.expr.name if e.expr is not None else None,
                'context': e.text[e.pos:e.pos + 20]}
    return {'matched': True, 'end': node.end, 'start': node.start,
            'children': len(node.children), 'text': node.text}
'''


def run_probes(cases):
    """Run ``probe`` for (grammar, text) pairs in a guarded subprocess."""
    lines = [PROBE_PRELUDE, 'results = []']
    for grammar_text, text in cases:
        lines.append('results.append(probe(%r, %r))' % (grammar_text, text))
    lines.append('print(json.dumps(results))')
    return run_parse_snippet('\n'.join(lines))


# ---------------------------------------------------------------------------
# Nullable expressions inside star / plus / nested quantifiers (subprocess)
# ---------------------------------------------------------------------------

def test_nullable_literal_in_star_terminates():
    """``""*`` must not loop; it matches one empty child and stops."""
    (result,) = run_probes([('foo = ""*', 'ab')])
    assert result == {'matched': True, 'end': 0, 'start': 0,
                      'children': 1, 'text': ''}


def test_nullable_literal_in_plus_terminates():
    """``""+`` satisfies its minimum with one empty child and stops."""
    (result,) = run_probes([('foo = ""+', 'ab')])
    assert result == {'matched': True, 'end': 0, 'start': 0,
                      'children': 1, 'text': ''}


def test_zero_width_regex_in_star_terminates():
    """A regex that can match empty (``[a-z]*``) inside ``*`` must stop."""
    empty_first, consuming = run_probes([
        ('foo = ~"[a-z]*"*', '12'),
        ('foo = ~"[a-z]*"*', 'ab12'),
    ])
    assert empty_first == {'matched': True, 'end': 0, 'start': 0,
                           'children': 1, 'text': ''}
    # The regex consumes 'ab', then matches empty once more and stops.
    assert consuming == {'matched': True, 'end': 2, 'start': 0,
                         'children': 2, 'text': 'ab'}


def test_nested_nullable_quantifiers_terminate():
    """``(""?)*`` nests a nullable quantifier inside another quantifier."""
    (result,) = run_probes([('foo = (""?)*', 'ab')])
    assert result == {'matched': True, 'end': 0, 'start': 0,
                      'children': 1, 'text': ''}


def test_optional_inside_plus_at_input_end():
    """``("a"?)+`` consumes greedily and stops exactly at end of input."""
    at_end, empty_match = run_probes([
        ('foo = ("a"?)+', 'aaa'),
        ('foo = ("a"?)+', 'b'),
    ])
    assert at_end == {'matched': True, 'end': 3, 'start': 0,
                      'children': 3, 'text': 'aaa'}
    assert empty_match == {'matched': True, 'end': 0, 'start': 0,
                           'children': 1, 'text': ''}


def test_zero_width_lookahead_inside_star():
    """A lookahead (zero-width by definition) inside ``*`` must not loop."""
    (result,) = run_probes([('foo = (&"a")* "a"', 'a')])
    assert result == {'matched': True, 'end': 1, 'start': 0,
                      'children': 2, 'text': 'a'}


# ---------------------------------------------------------------------------
# Lookahead combined with zero-width regexes
# ---------------------------------------------------------------------------

def test_positive_lookahead_of_zero_width_regex_consumes_nothing():
    g = Grammar('foo = &~"[a-z]*" "ab"')
    node = g.parse('ab')
    assert node.end - node.start == 2
    lookahead = node.children[0]
    assert (lookahead.start, lookahead.end) == (0, 0)


def test_negative_lookahead_of_nullable_regex_always_fails():
    """``![a-z]*`` can never succeed because the regex matches empty."""
    g = Grammar('foo = !~"[a-z]*" "ab"')
    with pytest.raises(ParseError) as exc_info:
        g.match('ab')
    error = exc_info.value
    assert error.pos == 0
    assert error.expr.name == 'foo'
    assert error.text[error.pos:error.pos + 20] == 'ab'


def test_positive_lookahead_at_end_of_input():
    g = Grammar('foo = "a" &""')
    node = g.parse('a')
    assert node.end == 1
    lookahead = node.children[1]
    assert (lookahead.start, lookahead.end) == (1, 1)


def test_failed_lookahead_reports_position_at_eof():
    g = Grammar('foo = "a" &"b"')
    with pytest.raises(ParseError) as exc_info:
        g.match('a')
    error = exc_info.value
    assert error.pos == 1
    # The innermost failing expression is the unnamed literal inside the
    # lookahead; the named rule at position 0 does not overwrite it.
    assert error.expr.name == ''
    # Stable context fragment: the empty tail at end of input.
    assert error.text[error.pos:error.pos + 20] == ''


def test_negative_lookahead_succeeds_at_eof():
    g = Grammar('foo = "a" !"b"')
    node = g.parse('a')
    assert node.end == 1
    lookahead = node.children[1]
    assert (lookahead.start, lookahead.end) == (1, 1)


# ---------------------------------------------------------------------------
# TokenGrammar: empty token stream, last token, EOF; match vs parse
# ---------------------------------------------------------------------------

def test_token_grammar_empty_stream_star_and_plus():
    g = TokenGrammar('foo = "a"*\nbar = "a"+')
    node = g['foo'].parse([])
    assert (node.start, node.end) == (0, 0)
    assert node.children == []
    with pytest.raises(ParseError) as exc_info:
        g['bar'].parse([])
    assert exc_info.value.pos == 0


def test_token_grammar_required_token_on_empty_stream():
    """Pin current behavior: a required token on an empty stream raises
    IndexError (TokenMatcher indexes past the end of the token list)."""
    g = TokenGrammar('foo = "a"')
    with pytest.raises(IndexError):
        g.parse([])


def test_token_grammar_match_vs_parse_on_last_token():
    g = TokenGrammar('foo = "a" "b"')
    tokens = [Token('a'), Token('b'), Token('c')]
    node = g.match(tokens)
    assert (node.start, node.end) == (0, 2)
    with pytest.raises(IncompleteParseError) as exc_info:
        g.parse(tokens)
    error = exc_info.value
    assert error.pos == 2
    assert error.expr.name == 'foo'
    exact = g.parse([Token('a'), Token('b')])
    assert (exact.start, exact.end) == (0, 2)


def test_token_grammar_negative_lookahead_at_eof():
    """Pin current behavior: ``!"b"`` at token EOF raises IndexError because
    TokenMatcher reads past the last token."""
    g = TokenGrammar('foo = "a" !"b"')
    with pytest.raises(IndexError):
        g.parse([Token('a')])


def test_token_grammar_error_position_and_context():
    g = TokenGrammar('foo = "a" "b"')
    tokens = [Token('a'), Token('x')]
    with pytest.raises(ParseError) as exc_info:
        g.parse(tokens)
    error = exc_info.value
    assert error.pos == 1
    # Token lists have no lines; column remains 1-based position fallback.
    assert error.line() is None
    assert error.column() == 2
    assert error.text[error.pos:error.pos + 20] == [Token('x')]


# ---------------------------------------------------------------------------
# Forward references and left recursion
# ---------------------------------------------------------------------------

def test_forward_reference_resolves_to_later_rule():
    g = Grammar('foo = bar "!"\nbar = "hi"')
    node = g.parse('hi!')
    assert node.end == 3
    assert node.children[0].expr.name == 'bar'
    assert (node.children[0].start, node.children[0].end) == (0, 2)


def test_direct_left_recursion_is_detected():
    g = Grammar('foo = foo "a" / "a"')
    with pytest.raises(LeftRecursionError) as exc_info:
        g.parse('aa')
    assert "'foo'" in str(exc_info.value)


def test_indirect_left_recursion_is_detected():
    g = Grammar('foo = bar "a" / "a"\nbar = foo "b" / "b"')
    with pytest.raises(LeftRecursionError) as exc_info:
        g.parse('ab')
    message = str(exc_info.value)
    assert 'Left recursion' in message
    assert "'foo'" in message or "'bar'" in message


# ---------------------------------------------------------------------------
# Shared prefixes between ordered alternatives
# ---------------------------------------------------------------------------

def test_shared_prefix_backtracks_to_second_alternative():
    g = Grammar('foo = "ab" "x" / "ab" "y"')
    node = g.parse('aby')
    assert (node.start, node.end) == (0, 3)
    assert node.text == 'aby'


def test_shared_prefix_error_reports_farthest_position():
    g = Grammar(
        'foo = branch_x / branch_y\n'
        'branch_x = "ab" tail_x\n'
        'branch_y = "ab" tail_y\n'
        'tail_x = "x"\n'
        'tail_y = "y"\n')
    with pytest.raises(ParseError) as exc_info:
        g.parse('abz')
    error = exc_info.value
    assert error.pos == 2
    assert error.expr.name == 'tail_y'
    assert error.text[error.pos:error.pos + 20] == 'z'


def test_farthest_error_wins_over_last_error():
    """The reported error is the *farthest* failure, not the most recent:
    ``first`` fails at position 3 after ``second`` later fails at 1."""
    g = Grammar(
        'main = first / second\n'
        'first = "a" "b" "c" last1\n'
        'last1 = "z"\n'
        'second = "a" last2\n'
        'last2 = "x"\n')
    with pytest.raises(ParseError) as exc_info:
        g.parse('abcy')
    error = exc_info.value
    assert error.pos == 3
    assert error.expr.name == 'last1'
    assert error.text[error.pos:error.pos + 20] == 'y'


# ---------------------------------------------------------------------------
# Unicode code points vs Python character indices
# ---------------------------------------------------------------------------

def test_astral_code_point_counts_as_one_position():
    g = Grammar('foo = "💣" "b"')
    node = g.parse('💣b')
    assert node.end == 2
    bomb, bee = node.children
    assert (bomb.start, bomb.end) == (0, 1)
    assert (bee.start, bee.end) == (1, 2)


def test_astral_repeat_consumption_and_incomplete_parse():
    g = Grammar('foo = "💣"+')
    node = g.parse('💣💣💣')
    assert node.end == 3
    assert len(node.children) == 3
    with pytest.raises(IncompleteParseError) as exc_info:
        g.parse('💣💣x')
    error = exc_info.value
    assert error.pos == 2
    assert error.expr.name == 'foo'
    assert error.text[error.pos:error.pos + 20] == 'x'


def test_combining_character_positions_are_code_point_indices():
    # 'e' + U+0301 (combining acute) is two code points, hence two positions.
    text = 'e\u0301x'
    assert len(text) == 3
    g = Grammar('foo = ~"." ~"."')
    node = g.match(text)
    first, second = node.children
    assert (first.start, first.end) == (0, 1)
    assert (second.start, second.end) == (1, 2)
    g2 = Grammar('foo = "e" "\\u0301" "z"')
    with pytest.raises(ParseError) as exc_info:
        g2.parse('e\u0301y')
    error = exc_info.value
    assert error.pos == 2
    assert error.column() == 3
    assert error.text[error.pos:error.pos + 20] == 'y'


# ---------------------------------------------------------------------------
# NodeVisitor ordering: empty nodes, nested nodes, raising visit methods
# ---------------------------------------------------------------------------

class RecordingVisitor(NodeVisitor):
    """Records (method, text) for every visit, in call order."""

    def __init__(self):
        self.calls = []

    def visit_foo(self, node, visited_children):
        self.calls.append(('foo', node.text))
        return visited_children

    def visit_bar(self, node, visited_children):
        self.calls.append(('bar', node.text))
        return node.text

    def generic_visit(self, node, visited_children):
        self.calls.append(('generic', node.text))
        return node.text


def test_visitor_on_empty_node_receives_no_children():
    g = Grammar('foo = "a"*')
    visitor = RecordingVisitor()
    result = visitor.visit(g.parse(''))
    assert result == []
    assert visitor.calls == [('foo', '')]


def test_visitor_visits_children_before_parents_left_to_right():
    g = Grammar('foo = bar bar\nbar = "a" / "b"')
    visitor = RecordingVisitor()
    result = visitor.visit(g.parse('ab'))
    assert result == ['a', 'b']
    assert visitor.calls == [
        ('generic', 'a'), ('bar', 'a'),
        ('generic', 'b'), ('bar', 'b'),
        ('foo', 'ab'),
    ]


def test_visitor_error_wraps_node_and_preserves_prior_order():
    class BoomVisitor(RecordingVisitor):
        def visit_bar(self, node, visited_children):
            self.calls.append(('bar', node.text))
            if node.text == 'b':
                raise ValueError('boom on b')
            return node.text

    g = Grammar('foo = bar bar\nbar = "a" / "b"')
    visitor = BoomVisitor()
    with pytest.raises(VisitationError) as exc_info:
        visitor.visit(g.parse('ab'))
    error = exc_info.value
    assert error.original_class is ValueError
    # Children before the failure were visited in order; the failing node is
    # the second ``bar`` and the pretty tree marks it.
    assert visitor.calls == [
        ('generic', 'a'), ('bar', 'a'),
        ('generic', 'b'), ('bar', 'b'),
    ]
    assert '<-- *** We were here. ***' in str(error)


def test_visitor_generic_fallback_for_unnamed_expressions():
    g = Grammar('foo = "a" "b"')
    visitor = RecordingVisitor()
    result = visitor.visit(g.parse('ab'))
    assert result == ['a', 'b']
    assert visitor.calls == [
        ('generic', 'a'), ('generic', 'b'), ('foo', 'ab'),
    ]
