"""Run potentially non-advancing parses in a child process with a hard deadline.

Repetitions over nullable expressions (``(~\"b*\")*`` and friends) are the
classic way to accidentally write a parser that never advances and never
returns. These tests must not rely on a global test-suite timeout to notice
that: each case runs in its own subprocess and the *parent* decides the
outcome within ``DEFAULT_TIMEOUT`` seconds.
"""

import subprocess
import sys

#: Maximum wall-clock seconds the parent waits before declaring a hang.
DEFAULT_TIMEOUT = 1.0


class SubprocessHang(AssertionError):
    """The child process did not finish within the allotted time."""


class SubprocessCrash(AssertionError):
    """The child process exited abnormally (non-zero exit, bad output)."""


def _child_program(grammar_source, text):
    """Return the Python program run inside the child process.

    It prints ``start end child_count`` of the top-level match node, or
    ``PARSE_ERROR <pos>`` when the grammar does not match.
    """
    return (
        "from parsimonious.grammar import Grammar\n"
        "from parsimonious.exceptions import ParseError\n"
        "grammar = Grammar({grammar!r})\n"
        "try:\n"
        "    node = grammar.match({text!r})\n"
        "except ParseError as exc:\n"
        "    print('PARSE_ERROR', exc.pos)\n"
        "else:\n"
        "    print(node.start, node.end, len(node.children))\n"
    ).format(grammar=grammar_source, text=text)


def match_in_subprocess(grammar_source, text, timeout=DEFAULT_TIMEOUT):
    """Match ``text`` against ``grammar_source`` in a fresh interpreter.

    Return ``(start, end, child_count)`` of the top-level match node.

    The parent enforces ``timeout`` itself via ``subprocess.run``; a child
    that is still running after ``timeout`` seconds is killed and reported
    as :class:`SubprocessHang`. Any other abnormal exit (exception in the
    child, unparseable output) raises :class:`SubprocessCrash`. Both carry
    the grammar and the input code points for reproduction.
    """
    diag = "grammar={!r} input code points={!r} timeout={}s".format(
        grammar_source, [ord(c) for c in text], timeout)
    try:
        proc = subprocess.run(
            [sys.executable, '-c', _child_program(grammar_source, text)],
            capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise SubprocessHang(
            'parse did not finish within {}s (possible non-advancing '
            'repetition): {}'.format(timeout, diag))
    if proc.returncode != 0:
        raise SubprocessCrash(
            'parse subprocess exited with code {}: {}\nstderr:\n{}'.format(
                proc.returncode, diag, proc.stderr))
    out = proc.stdout.strip().split()
    if len(out) != 3:
        raise SubprocessCrash(
            'parse subprocess printed unexpected output: {}\nstdout: '
            '{!r}\nstderr: {!r}'.format(diag, proc.stdout, proc.stderr))
    start, end, child_count = (int(piece) for piece in out)
    return start, end, child_count
