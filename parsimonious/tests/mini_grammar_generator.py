"""A small deterministic grammar generator for boundary testing.

Only semantically safe expressions are produced:

* quantifiers (``*`` ``+`` ``?``) wrap non-nullable atoms only, so no
  repeat can fail to advance;
* lookaheads are never placed inside a quantifier;
* literals and regexes always consume at least one character.

Generation is driven by a fixed seed with depth and node-count limits, so
two consecutive runs produce identical corpora in identical order.

For every sample we check the parse/match consumption relation and three
metamorphic transforms: harmless grouping, an equivalent duplicated
single branch, and an explicit end-of-input marker.
"""

import os
import random
from dataclasses import dataclass

from parsimonious.grammar import Grammar
from parsimonious.exceptions import ParseError, IncompleteParseError

DEFAULT_SEED = 20260920
SAMPLE_COUNT = 32
MAX_DEPTH = 3
MAX_NODES = 14

# Atoms always consume exactly one character from {a, b, c}.
ATOMS = ('"a"', '"b"', '"c"', '~"[ab]"', '~"[bc]"')

def _alphabet_inputs():
    """All strings over {a, b} of length 0..3, in a fixed order."""
    inputs = ['']
    for length in range(1, 4):
        inputs.extend(
            format(i, '0%db' % length).replace('0', 'a').replace('1', 'b')
            for i in range(2 ** length))
    return tuple(inputs)


INPUTS = _alphabet_inputs()


def fuzz_seed():
    """The active seed; overridable for replaying a reported failure."""
    return int(os.environ.get('PARSIMONIOUS_FUZZ_SEED', DEFAULT_SEED))


@dataclass(frozen=True)
class Sample:
    seed: int
    index: int
    grammar: str


class _Generator:
    """Builds one grammar RHS with a node budget and depth limit."""

    def __init__(self, seed, max_depth=MAX_DEPTH, max_nodes=MAX_NODES):
        self.rng = random.Random(seed)
        self.max_depth = max_depth
        self.budget = max_nodes

    def atom(self):
        self.budget -= 1
        return self.rng.choice(ATOMS)

    def expr(self, depth):
        if depth >= self.max_depth or self.budget < 2:
            return self.atom()
        choice = self.rng.random()
        if choice < 0.30:
            return self.atom()
        if choice < 0.50:  # sequence
            parts = [self.expr(depth + 1)
                     for _ in range(self.rng.randint(2, 3))]
            return '(%s)' % ' '.join(parts)
        if choice < 0.65:  # ordered choice
            parts = [self.expr(depth + 1)
                     for _ in range(self.rng.randint(2, 3))]
            return '(%s)' % ' / '.join(parts)
        if choice < 0.80:  # quantifier around a non-nullable atom only
            return '(%s)%s' % (self.atom(), self.rng.choice('*+?'))
        # lookahead prefix (zero-width, never quantified) before a suffix
        return '(%s%s %s)' % (self.rng.choice('&!'), self.atom(),
                              self.expr(depth + 1))


def generate_sample(seed, index):
    """Deterministically build sample ``index`` for ``seed``."""
    gen = _Generator(seed * 100003 + index)
    rhs = gen.expr(0)
    rules = ['main = %s' % rhs]
    if gen.rng.random() < 0.5:
        # A helper rule referenced before its definition (forward ref).
        rules[0] = 'main = (%s) tail' % rhs
        rules.append('tail = %s' % gen.atom())
    return Sample(seed=seed, index=index, grammar='\n'.join(rules))


def corpus(seed, count=SAMPLE_COUNT):
    return [generate_sample(seed, i) for i in range(count)]


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------

def match_outcome(grammar, text):
    """(matched, end_or_error_pos) for ``grammar.match(text)``."""
    try:
        node = grammar.match(text)
    except ParseError as exc:
        return (False, exc.pos)
    return (True, node.end)


def check_parse_match_relation(sample, inputs=INPUTS):
    """For every input: parse consumes everything iff match reached the end.

    Returns a list of failure dicts (empty on success).
    """
    grammar = Grammar(sample.grammar)
    failures = []
    for text in inputs:
        matched, value = match_outcome(grammar, text)
        if matched:
            if value == len(text):
                node = grammar.parse(text)
                if node.end != value:
                    failures.append(_failure(sample, text, 'parse end %r != '
                                             'match end %r' % (node.end, value)))
            else:
                try:
                    grammar.parse(text)
                except IncompleteParseError as exc:
                    if exc.pos != value:
                        failures.append(_failure(
                            sample, text, 'IncompleteParseError pos %r != '
                            'match end %r' % (exc.pos, value)))
                except ParseError as exc:
                    failures.append(_failure(
                        sample, text, 'parse raised %r although match '
                        'consumed %r of %r' % (exc, value, len(text))))
                else:
                    failures.append(_failure(
                        sample, text, 'parse succeeded although match '
                        'consumed only %r of %r' % (value, len(text))))
        else:
            try:
                grammar.parse(text)
            except IncompleteParseError as exc:
                failures.append(_failure(
                    sample, text, 'match failed at %r but parse was '
                    'incomplete at %r' % (value, exc.pos)))
            except ParseError as exc:
                if exc.pos != value:
                    failures.append(_failure(
                        sample, text, 'parse error pos %r != match error '
                        'pos %r' % (exc.pos, value)))
            else:
                failures.append(_failure(
                    sample, text, 'parse succeeded although match failed '
                    'at %r' % (value,)))
        if failures:
            break
    return failures


def _variants(grammar_text):
    """The three metamorphic transforms of a generated grammar."""
    first_line, _, rest = grammar_text.partition('\n')
    _, _, rhs = first_line.partition(' = ')
    grouped = 'main = (%s)%s' % (rhs, '\n' + rest if rest else '')
    duplicated = 'main = (%s) / (%s)%s' % (rhs, rhs,
                                           '\n' + rest if rest else '')
    eof = 'main = (%s) end_of_input%s\nend_of_input = !~"."' % (
        rhs, '\n' + rest if rest else '')
    return {'grouped': grouped, 'duplicated': duplicated, 'eof': eof}


def check_metamorphic_variants(sample, inputs=INPUTS):
    """Grouping and duplicated branches preserve match outcomes; an explicit
    EOF marker turns ``match`` into ``parse``."""
    base = Grammar(sample.grammar)
    variants = {name: Grammar(text)
                for name, text in _variants(sample.grammar).items()}
    failures = []
    for text in inputs:
        expected = match_outcome(base, text)
        for name in ('grouped', 'duplicated'):
            got = match_outcome(variants[name], text)
            if got != expected:
                failures.append(_failure(
                    sample, text, 'variant %r changed match outcome: '
                    '%r != %r' % (name, got, expected), variant=name))
        # Explicit EOF: match succeeds iff the base grammar parses fully.
        eof_matched, eof_value = match_outcome(variants['eof'], text)
        base_parses_fully = expected[0] and expected[1] == len(text)
        if eof_matched != base_parses_fully:
            failures.append(_failure(
                sample, text, 'eof variant matched=%r but full parse=%r'
                % (eof_matched, base_parses_fully), variant='eof'))
        elif eof_matched and eof_value != len(text):
            failures.append(_failure(
                sample, text, 'eof variant consumed %r of %r'
                % (eof_value, len(text)), variant='eof'))
        if failures:
            break
    return failures


# ---------------------------------------------------------------------------
# Failure reporting with a minimal reproducible sample
# ---------------------------------------------------------------------------

def _failure(sample, text, message, variant=None):
    return {'sample': sample, 'input': text, 'message': message,
            'variant': variant}


def shrink_input(still_fails, text):
    """Deterministically shrink ``text`` while ``still_fails`` keeps holding.

    Tries prefixes first, then single-character deletions, one pass each.
    """
    for end in range(len(text)):
        if still_fails(text[:end]):
            text = text[:end]
            break
    index = 0
    while index < len(text):
        candidate = text[:index] + text[index + 1:]
        if still_fails(candidate):
            text = candidate
        else:
            index += 1
    return text


def format_failure(failure, still_fails=None):
    """A stable, replayable failure report.

    Includes the seed, the generated grammar, the input as code points and
    a minimal reproducible input (shrunk when a recheck callback is given).
    """
    sample = failure['sample']
    text = failure['input']
    minimal = shrink_input(still_fails, text) if still_fails else text
    lines = [
        'seed: %d (replay with PARSIMONIOUS_FUZZ_SEED=%d)' % (
            sample.seed, sample.seed),
        'sample index: %d' % sample.index,
        'variant: %s' % (failure['variant'] or 'base'),
        'generated grammar:',
        sample.grammar,
        'input code points: %s' % ([ord(c) for c in text],),
        'minimal reproducible input code points: %s' % (
            [ord(c) for c in minimal],),
        'problem: %s' % failure['message'],
    ]
    return '\n'.join(lines)
