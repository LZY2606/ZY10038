"""A tiny deterministic grammar generator for property-style tests.

Generates only *semantically safe* expressions: quantifiers that can
repeat (``*`` and ``+``) never wrap a nullable subexpression, so no
generated grammar can fail to advance; references form a DAG (each rule
may only reference rules generated before it), so no left recursion is
possible. Everything is driven by a single fixed seed, generation depth
and node counts are capped, and two runs with the same seed produce the
same grammars, inputs, and results in the same order.

Expression trees are plain tuples:

* ``('lit', 'a')``            -- a one-character literal
* ``('rx', 'ab', 1, 2)``      -- a char-class regex ``[ab]{1,2}``
* ``('ref', 'h1')``           -- a reference to a helper rule
* ``('seq', (m1, m2))``       -- a sequence
* ``('choice', (b1, b2))``    -- an ordered choice
* ``('quant', member, '*')``  -- a quantifier (one of ? * +)
* ``('not', member)``         -- a negative lookahead (zero-width, safe)
* ``('grp', member)``         -- harmless grouping (used by variants)
"""

import os
import random

#: Fixed default seed. Set PARSIMONIOUS_FUZZ_SEED to replay another seed.
DEFAULT_SEED = 1042
SEED_ENV_VAR = 'PARSIMONIOUS_FUZZ_SEED'

MAX_DEPTH = 4
MAX_NODES = 12
SAMPLE_COUNT = 60
ALPHABET = 'abc'

ROOT_RULE = 's'
EOF_RULE = 's_eof'
EOF_SENTINEL = '!~"."'


def effective_seed():
    """Return the seed in effect: the env override or the fixed default."""
    return int(os.environ.get(SEED_ENV_VAR, DEFAULT_SEED))


def node_count(tree):
    """Number of nodes in an expression tree."""
    if tree[0] in ('lit', 'rx', 'ref'):
        return 1
    if tree[0] in ('seq', 'choice'):
        return 1 + sum(node_count(m) for m in tree[1])
    return 1 + node_count(tree[1])


def is_nullable(tree, rules):
    """Whether the expression can match the empty string.

    ``rules`` maps helper rule names to their trees; only earlier rules
    are referenced, so the recursion always terminates.
    """
    kind = tree[0]
    if kind == 'lit':
        return len(tree[1]) == 0
    if kind == 'rx':
        return tree[2] == 0
    if kind == 'ref':
        return is_nullable(rules[tree[1]], rules)
    if kind == 'seq':
        return all(is_nullable(m, rules) for m in tree[1])
    if kind == 'choice':
        return any(is_nullable(b, rules) for b in tree[1])
    if kind == 'quant':
        return tree[2] in '?*' or is_nullable(tree[1], rules)
    if kind in ('not', 'grp'):
        return kind == 'not' or is_nullable(tree[1], rules)
    raise ValueError('unknown node %r' % (tree,))


def _render(tree, parenthesize=False):
    """Render an expression tree as grammar right-hand-side text."""
    kind = tree[0]
    if kind == 'lit':
        return '"%s"' % tree[1]
    if kind == 'rx':
        chars, lo, hi = tree[1], tree[2], tree[3]
        if lo == 1 and hi == 1:
            return '~"[%s]"' % chars
        return '~"[%s]{%d,%d}"' % (chars, lo, hi)
    if kind == 'ref':
        return tree[1]
    if kind == 'seq':
        body = ' '.join(_render(m, parenthesize=True) for m in tree[1])
        return '(%s)' % body if parenthesize else body
    if kind == 'choice':
        body = ' / '.join(_render(b, parenthesize=True) for b in tree[1])
        return '(%s)' % body if parenthesize else body
    if kind == 'quant':
        member = _render(tree[1], parenthesize=True)
        if tree[1][0] in ('quant', 'not'):
            member = '(%s)' % member
        return '%s%s' % (member, tree[2])
    if kind == 'not':
        member = _render(tree[1], parenthesize=True)
        if tree[1][0] in ('quant', 'not'):
            member = '(%s)' % member
        return '!%s' % member
    if kind == 'grp':
        return '(%s)' % _render(tree[1])
    raise ValueError('unknown node %r' % (tree,))


def render_grammar(rules, root):
    """Render a full grammar: root rule first, then helpers in order."""
    lines = ['%s = %s' % (ROOT_RULE, _render(root))]
    for name, tree in rules.items():
        lines.append('%s = %s' % (name, _render(tree)))
    return '\n'.join(lines)


class _Generator:
    """Builds random but semantically safe expression trees."""

    def __init__(self, rng):
        self.rng = rng

    def atom(self, rules, visible):
        """Return a random non-nullable atom."""
        choice = self.rng.randrange(3)
        if choice == 0 or not visible:
            return ('lit', self.rng.choice(ALPHABET))
        if choice == 1:
            chars = ''.join(sorted(self.rng.sample(ALPHABET, 2)))
            return ('rx', chars, 1, self.rng.randint(1, 2))
        return ('ref', self.rng.choice(visible))

    def expr(self, rules, visible, depth, budget):
        """Return a random expression tree within depth/node budgets."""
        if depth >= MAX_DEPTH or budget[0] <= 1:
            budget[0] -= 1
            return self.atom(rules, visible)
        roll = self.rng.randrange(10)
        if roll < 3:
            budget[0] -= 1
            return self.atom(rules, visible)
        if roll < 5:  # sequence of 2-3 terms
            count = self.rng.randint(2, 3)
            members = tuple(self.expr(rules, visible, depth + 1, budget)
                            for _ in range(count))
            budget[0] -= 1
            return ('seq', members)
        if roll < 6:  # ordered choice of 2 branches
            branches = tuple(self.expr(rules, visible, depth + 1, budget)
                             for _ in range(2))
            budget[0] -= 1
            return ('choice', branches)
        if roll < 8:  # quantifier; * and + only over non-nullable members
            member = self.expr(rules, visible, depth + 1, budget)
            symbol = self.rng.choice('?*+')
            if symbol in '*+' and is_nullable(member, rules):
                symbol = '?'
            budget[0] -= 1
            return ('quant', member, symbol)
        if roll < 9:  # negative lookahead (zero-width, never repeated)
            member = self.atom(rules, visible)
            budget[0] -= 1
            return ('not', member)
        budget[0] -= 1
        return self.atom(rules, visible)


def generate_input(tree, rules, rng, depth=0):
    """Generate an input string that the tree is likely to match."""
    kind = tree[0]
    if kind == 'lit':
        return tree[1]
    if kind == 'rx':
        chars, lo, hi = tree[1], tree[2], tree[3]
        count = rng.randint(lo, hi)
        return ''.join(rng.choice(chars) for _ in range(count))
    if kind == 'ref':
        if depth > MAX_DEPTH + 2:  # paranoia; the DAG makes this impossible
            return ''
        return generate_input(rules[tree[1]], rules, rng, depth + 1)
    if kind == 'seq':
        return ''.join(generate_input(m, rules, rng, depth) for m in tree[1])
    if kind == 'choice':
        return generate_input(rng.choice(list(tree[1])), rules, rng, depth)
    if kind == 'quant':
        member, symbol = tree[1], tree[2]
        count = {'?': rng.randint(0, 1),
                 '*': rng.randint(0, 2),
                 '+': rng.randint(1, 2)}[symbol]
        return ''.join(generate_input(member, rules, rng, depth)
                       for _ in range(count))
    if kind in ('not', 'grp'):
        return ''
    raise ValueError('unknown node %r' % (tree,))


def _disturb(text, rng):
    """Perturb an input so failure paths get exercised too."""
    if text and rng.randrange(2):
        return text[:-1]  # truncate: likely a ParseError
    return text + rng.choice(ALPHABET)  # extra: likely incomplete parse


class Sample:
    """One generated grammar plus one input, with rendering variants."""

    def __init__(self, index, rules, root, text):
        self.index = index
        self.rules = rules
        self.root = root
        self.text = text
        self.grammar_text = render_grammar(rules, root)
        # Filled in by generate_samples (deterministically):
        self.grouping_grammar = None
        self.duplicate_grammar = None
        self.eof_grammar = None

    def describe(self):
        return ('seed={} sample={}\ngrammar:\n{}\ninput code points: {}'.format(
            effective_seed(), self.index, self.grammar_text,
            [ord(c) for c in self.text]))


def generate_samples(seed):
    """Return the deterministic list of samples for ``seed``."""
    rng = random.Random(seed)
    samples = []
    for index in range(SAMPLE_COUNT):
        gen = _Generator(rng)
        rules = {}
        helper_count = rng.randint(0, 2)
        for helper_index in range(helper_count):
            name = 'h%d' % (helper_index + 1)
            budget = [MAX_NODES // 2]
            rules[name] = gen.expr(rules, sorted(rules), 0, budget)
        budget = [MAX_NODES]
        root = gen.expr(rules, sorted(rules), 0, budget)
        text = generate_input(root, rules, rng)
        if rng.randrange(3) == 0:
            text = _disturb(text, rng)
        sample = Sample(index, rules, root, text)
        sample.grouping_grammar = grouping_variant(sample, rng)
        sample.duplicate_grammar = duplicate_branch_variant(sample, rng)
        sample.eof_grammar = eof_variant(sample)
        samples.append(sample)
    return samples


# --- Semantics-preserving variants -------------------------------------

def _map_nodes(tree, fn):
    """Apply ``fn`` to every node, bottom-up; fn returns a replacement."""
    kind = tree[0]
    if kind in ('seq', 'choice'):
        tree = (kind, tuple(_map_nodes(m, fn) for m in tree[1]))
    elif kind in ('quant', 'not', 'grp'):
        tree = (kind, _map_nodes(tree[1], fn)) + tree[2:]
    return fn(tree)


def _nth_node_transform(tree, target_index, make_variant):
    """Replace the node at pre-order position ``target_index``."""
    counter = [0]

    def transform(node):
        index = counter[0]
        counter[0] += 1
        return make_variant(node) if index == target_index else node

    return _map_nodes(tree, transform), counter[0]


def grouping_variant(sample, rng):
    """Wrap one random node of the root rule in harmless parentheses."""
    index = rng.randrange(node_count(sample.root))
    new_root, _ = _nth_node_transform(
        sample.root, index, lambda node: ('grp', node))
    return render_grammar(sample.rules, new_root)


def duplicate_branch_variant(sample, rng):
    """Turn one random node into an equivalent ``(x / x)`` choice."""
    index = rng.randrange(node_count(sample.root))
    new_root, _ = _nth_node_transform(
        sample.root, index, lambda node: ('choice', (node, node)))
    return render_grammar(sample.rules, new_root)


def eof_variant(sample):
    """Prepend a rule requiring explicit EOF after the original root."""
    return '%s = %s %s\n%s' % (
        EOF_RULE, ROOT_RULE, EOF_SENTINEL, sample.grammar_text)
