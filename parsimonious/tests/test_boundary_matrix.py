# coding=utf-8
"""Boundary matrix for combined Parsimonious expressions.

Single expressions are easy to test; combinations are where parsers drift:
zero-width matches inside repetitions, lookaheads composed with empty
regexes, token streams at EOF, shared prefixes between ordered-choice
branches, Unicode code points vs. Python character indexes, and visitor
traversal order. Each test here pins one cell of that matrix.

Conventions:
* Failing scenarios assert the furthest error position, the rule name, and
  a stable context snippet.
* Successful scenarios assert the consumed length and the node span.
* Repetitions that could fail to advance run in a subprocess and are
  judged by the parent within one second (see ``subprocess_guard``).
"""

import pytest

from parsimonious.exceptions import (BadGrammar, IncompleteParseError,
                                     LeftRecursionError, ParseError,
                                     VisitationError)
from parsimonious.grammar import Grammar, TokenGrammar
from parsimonious.nodes import NodeVisitor
from parsimonious.tests.subprocess_guard import match_in_subprocess
from parsimonious.utils import Token


class TestNullableQuantifiers:
    """Nullable expressions fed into *, + and nested quantifiers.

    These are the inputs most likely to hang if the zero-width guard in
    ``Quantifier`` regresses, so each one runs in a subprocess with a
    parent-enforced one-second deadline.
    """

    def test_nullable_regex_inside_star_terminates(self):
        # ``b*`` matches the empty string at position 0 of 'aaab'; the star
        # must stop instead of looping on a zero-width child.
        start, end, child_count = match_in_subprocess('foo = (~"b*")*', 'aaab')
        assert (start, end) == (0, 0)
        assert child_count == 1  # one empty match, then the guard fired

    def test_nullable_regex_inside_plus_terminates(self):
        # Same, but the quantifier requires at least one iteration.
        start, end, child_count = match_in_subprocess('foo = (~"b*")+', 'aaa')
        assert (start, end) == (0, 0)
        assert child_count == 1

    def test_optional_inside_star_stops_at_first_nonmatch(self):
        # 'a'? consumes the two a's, then matches empty in front of 'b';
        # the zero-width child is appended before the guard stops the loop.
        start, end, child_count = match_in_subprocess('foo = ("a"?)*', 'aab')
        assert (start, end) == (0, 2)
        assert child_count == 3

    def test_nested_nullable_quantifiers_terminate(self):
        # A nullable plus nested inside a star: two guard layers deep.
        start, end, child_count = match_in_subprocess('foo = ((~"b*")+)*', 'aa')
        assert (start, end) == (0, 0)
        assert child_count == 1

    def test_empty_regex_inside_star_on_empty_input(self):
        # Zero-width match at EOF: the loop body never even runs.
        start, end, child_count = match_in_subprocess('foo = (~"")*', '')
        assert (start, end) == (0, 0)
        assert child_count == 0

    def test_bounded_quantifier_at_boundaries(self):
        grammar = Grammar('foo = "ab"{2,3}')
        node = grammar.parse('abab')
        assert (node.start, node.end) == (0, 4)
        assert len(node.children) == 2
        node = grammar.parse('ababab')
        assert (node.start, node.end) == (0, 6)
        assert len(node.children) == 3
        with pytest.raises(ParseError) as exc_info:
            grammar.parse('ab')
        assert exc_info.value.pos == 0
        assert exc_info.value.expr.name == 'foo'


class TestLookaheadZeroWidth:
    """Positive/negative lookahead combined with zero-width regexes."""

    def test_positive_lookahead_consumes_nothing(self):
        grammar = Grammar('foo = &"ab" "a" "b"')
        node = grammar.parse('ab')
        assert (node.start, node.end) == (0, 2)
        lookahead, a, b = node.children
        assert (lookahead.start, lookahead.end) == (0, 0)
        assert (a.start, a.end) == (0, 1)
        assert (b.start, b.end) == (1, 2)

    def test_positive_lookahead_of_empty_regex_always_succeeds(self):
        grammar = Grammar('foo = &~"" "a"')
        node = grammar.parse('a')
        assert (node.start, node.end) == (0, 1)
        lookahead, _ = node.children
        assert (lookahead.start, lookahead.end) == (0, 0)

    def test_negative_lookahead_of_empty_regex_always_fails(self):
        # Nothing can fail to match the empty regex, so !~"" never matches.
        grammar = Grammar('foo = !~"" "a"')
        with pytest.raises(ParseError) as exc_info:
            grammar.match('')
        error = exc_info.value
        assert error.pos == 0
        assert error.expr.name == 'foo'
        assert error.text[error.pos:error.pos + 5] == ''

    def test_negative_lookahead_dot_is_eof_sentinel(self):
        grammar = Grammar('foo = "a" !~"."')
        node = grammar.parse('a')
        assert (node.start, node.end) == (0, 1)
        with pytest.raises(ParseError) as exc_info:
            grammar.parse('ab')
        error = exc_info.value
        assert error.pos == 1
        assert error.text[error.pos:error.pos + 5] == 'b'

    def test_lookahead_pair_at_end_of_input(self):
        # & and ! composed at the very end of the input.
        grammar = Grammar('foo = "a"* &~""')
        node = grammar.parse('aa')
        assert (node.start, node.end) == (0, 2)
        star, lookahead = node.children
        assert len(star.children) == 2
        assert (lookahead.start, lookahead.end) == (2, 2)

    def test_lookahead_does_not_consume_shared_prefix(self):
        grammar = Grammar('foo = &"a" "a" "b"')
        node = grammar.parse('ab')
        assert (node.start, node.end) == (0, 2)
        with pytest.raises(ParseError) as exc_info:
            grammar.match('ac')
        error = exc_info.value
        assert error.pos == 1
        assert error.text[error.pos:error.pos + 5] == 'c'


class TestTokenGrammarBoundary:
    """TokenGrammar on an empty token stream, the last token, and EOF."""

    def test_empty_token_stream_star_matches_empty(self):
        grammar = TokenGrammar('foo = "a"*')
        node = grammar.parse([])
        assert (node.start, node.end) == (0, 0)
        assert node.children == []

    def test_empty_token_stream_required_token_raises_index_error(self):
        # Current behavior: a required token at EOF of an empty stream
        # escapes as IndexError rather than ParseError. Pinned so any
        # change is a conscious decision.
        grammar = TokenGrammar('foo = "a"')
        with pytest.raises(IndexError):
            grammar.match([])
        with pytest.raises(IndexError):
            grammar.parse([])

    def test_last_token_matches_exactly_at_eof(self):
        tokens = [Token('a'), Token('b')]
        grammar = TokenGrammar('foo = "a" "b"')
        node = grammar.parse(tokens)
        assert (node.start, node.end) == (0, 2)
        assert node.end == len(tokens)
        first, second = node.children
        assert (first.start, first.end) == (0, 1)
        assert (second.start, second.end) == (1, 2)

    def test_required_token_past_last_token_raises_index_error(self):
        grammar = TokenGrammar('foo = "a" "b"')
        with pytest.raises(IndexError):
            grammar.match([Token('a')])

    def test_optional_token_at_eof_is_skipped(self):
        grammar = TokenGrammar('foo = "a" "b"?')
        node = grammar.parse([Token('a')])
        assert (node.start, node.end) == (0, 1)

    def test_match_vs_parse_with_trailing_tokens(self):
        tokens = [Token('a'), Token('b')]
        grammar = TokenGrammar('foo = "a"')
        node = grammar.match(tokens)
        assert (node.start, node.end) == (0, 1)  # match() tolerates a suffix
        with pytest.raises(IncompleteParseError) as exc_info:
            grammar.parse(tokens)
        error = exc_info.value
        assert error.pos == 1
        assert error.expr.name == 'foo'
        assert error.line() is None  # token streams have no line numbers

    def test_wrong_token_type_reports_furthest_position(self):
        tokens = [Token('a'), Token('c')]
        grammar = TokenGrammar('foo = "a" "b"')
        with pytest.raises(ParseError) as exc_info:
            grammar.parse(tokens)
        error = exc_info.value
        assert error.pos == 1
        assert str(error.text[error.pos]) == '<Token "c">'


class TestForwardRefsAndLeftRecursion:
    """Forward rule references and direct/indirect left-recursion detection."""

    def test_forward_reference_resolves_to_named_rule(self):
        grammar = Grammar('foo = bar "x"\nbar = "a"')
        node = grammar.parse('ax')
        assert (node.start, node.end) == (0, 2)
        ref, x = node.children
        assert ref.expr.name == 'bar'
        assert (ref.start, ref.end) == (0, 1)

    def test_forward_reference_chain_resolves(self):
        # A rule that is just a reference to another rule collapses onto
        # the concrete expression when references are resolved.
        grammar = Grammar('foo = bar\nbar = baz\nbaz = "a"')
        assert grammar['foo'] is grammar['baz']
        node = grammar.parse('a')
        assert (node.start, node.end) == (0, 1)
        assert node.expr.name == 'baz'

    def test_direct_left_recursion_detected(self):
        grammar = Grammar('foo = foo "a" / "a"')
        with pytest.raises(LeftRecursionError) as exc_info:
            grammar.parse('a')
        assert exc_info.value.expr.name == 'foo'
        assert 'Left recursion' in str(exc_info.value)

    def test_indirect_left_recursion_detected(self):
        grammar = Grammar('foo = bar / "a"\nbar = foo "b"')
        with pytest.raises(LeftRecursionError) as exc_info:
            grammar.parse('ab')
        assert exc_info.value.expr.name == 'foo'

    def test_circular_rule_definition_rejected(self):
        # The BadGrammar raised during reference resolution is wrapped in
        # a VisitationError by the grammar-building visitor.
        with pytest.raises(VisitationError) as exc_info:
            Grammar('foo = bar\nbar = foo')
        assert exc_info.value.original_class is BadGrammar
        assert 'Circular' in str(exc_info.value)


class TestSharedPrefixes:
    """Ordered-choice branches that share prefixes."""

    def test_first_branch_wins_and_parse_notices_leftover(self):
        grammar = Grammar('foo = "a" / "ab"')
        node = grammar.match('ab')
        assert (node.start, node.end) == (0, 1)  # PEG: first match wins
        with pytest.raises(IncompleteParseError) as exc_info:
            grammar.parse('ab')
        error = exc_info.value
        assert error.pos == 1
        assert error.expr.name == 'foo'
        assert error.text[error.pos:error.pos + 5] == 'b'

    def test_backtracking_to_second_branch_with_shared_prefix(self):
        grammar = Grammar('foo = "ab" / "ac"')
        node = grammar.parse('ac')
        assert (node.start, node.end) == (0, 2)
        assert node.children[0].text == 'ac'

    def test_error_reports_furthest_position_not_last(self):
        # 'deep' fails at position 1, then 'shallow' also fails at position
        # 1; the reported error must stay at the furthest position (1),
        # not drift back to the start rule at position 0.
        grammar = Grammar(
            'start = deep / shallow\n'
            'deep = "a" "b" "c"\n'
            'shallow = "a" "q"')
        with pytest.raises(ParseError) as exc_info:
            grammar.match('ax')
        error = exc_info.value
        assert error.pos == 1
        assert error.text[error.pos:error.pos + 5] == 'x'

    def test_error_names_furthest_named_rule(self):
        grammar = Grammar(
            'start = "a" middle\n'
            'middle = "b" "c"')
        with pytest.raises(ParseError) as exc_info:
            grammar.match('ax')
        error = exc_info.value
        assert error.pos == 1
        assert error.expr.name == 'middle'
        assert error.text[error.pos:error.pos + 5] == 'x'


class TestUnicodeIndexes:
    """Unicode code points vs. Python character indexes (they coincide)."""

    def test_astral_literal_spans_one_index(self):
        grammar = Grammar('foo = "\U0001F4A3" "x"')
        text = '\U0001F4A3x'
        node = grammar.parse(text)
        assert len(text) == 2  # one astral code point == one index
        assert (node.start, node.end) == (0, 2)
        bomb, x = node.children
        assert (bomb.start, bomb.end) == (0, 1)
        assert (x.start, x.end) == (1, 2)

    def test_error_position_counts_code_points_not_bytes(self):
        grammar = Grammar('foo = "\U0001F4A3" "\U0001F4A5"')
        with pytest.raises(ParseError) as exc_info:
            grammar.match('\U0001F4A3x')
        error = exc_info.value
        assert error.pos == 1  # not 4 (UTF-8 bytes) and not 2 (UTF-16 units)
        assert error.column() == 2
        assert error.text[error.pos:error.pos + 5] == 'x'

    def test_regex_dot_matches_one_code_point(self):
        grammar = Grammar('foo = ~"." ~"."')
        node = grammar.parse('\U0001F4A3x')
        assert (node.start, node.end) == (0, 2)
        first, second = node.children
        assert (first.start, first.end) == (0, 1)
        assert (second.start, second.end) == (1, 2)


class RecordingVisitor(NodeVisitor):
    """A visitor that records the order and spans of visited nodes."""

    def __init__(self):
        self.order = []

    def visit_foo(self, node, visited_children):
        self.order.append(('foo', node.start, node.end, visited_children))
        return visited_children

    def visit_bar(self, node, visited_children):
        self.order.append(('bar', node.start, node.end, visited_children))
        return visited_children

    def visit_item(self, node, visited_children):
        self.order.append(('item', node.start, node.end, node.text))
        return node.text

    def generic_visit(self, node, visited_children):
        return visited_children


class TestNodeVisitorOrder:
    """NodeVisitor on empty nodes, nested nodes, and raising methods."""

    def test_visits_children_before_parents_left_to_right(self):
        grammar = Grammar('foo = bar bar\nbar = "a"')
        visitor = RecordingVisitor()
        visitor.visit(grammar.parse('aa'))
        kinds = [entry[0] for entry in visitor.order]
        assert kinds == ['bar', 'bar', 'foo']
        assert visitor.order[0][1:3] == (0, 1)
        assert visitor.order[1][1:3] == (1, 2)
        assert visitor.order[2][1:3] == (0, 2)

    def test_visits_empty_node_with_no_children(self):
        grammar = Grammar('foo = "a"*')
        visitor = RecordingVisitor()
        visitor.visit(grammar.parse(''))
        assert visitor.order == [('foo', 0, 0, [])]

    def test_visits_nested_nodes_inside_out(self):
        grammar = Grammar(
            'foo = item item\n'
            'item = ~"[a-z]"')
        visitor = RecordingVisitor()
        visitor.visit(grammar.parse('ab'))
        kinds = [entry[0] for entry in visitor.order]
        assert kinds == ['item', 'item', 'foo']
        assert visitor.order[0][3] == 'a'
        assert visitor.order[1][3] == 'b'

    def test_raising_visit_method_wraps_after_children_visited(self):
        class BoomVisitor(RecordingVisitor):
            def visit_foo(self, node, visited_children):
                raise ValueError('boom')

        grammar = Grammar('foo = bar bar\nbar = "a"')
        visitor = BoomVisitor()
        with pytest.raises(VisitationError) as exc_info:
            visitor.visit(grammar.parse('aa'))
        error = exc_info.value
        assert error.original_class is ValueError
        assert isinstance(error.__cause__, ValueError)
        assert 'boom' in str(error)
        # Both children were fully visited before the parent blew up:
        assert [entry[0] for entry in visitor.order] == ['bar', 'bar']

    def test_unwrapped_exceptions_propagate_unchanged(self):
        class KeyErrorVisitor(NodeVisitor):
            unwrapped_exceptions = (KeyError,)

            def visit_foo(self, node, visited_children):
                raise KeyError('nope')

            def generic_visit(self, node, visited_children):
                return visited_children

        grammar = Grammar('foo = "a"')
        with pytest.raises(KeyError):
            KeyErrorVisitor().visit(grammar.parse('a'))
