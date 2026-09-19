"""Helpers to run potentially non-terminating parses in a guarded subprocess.

Any repeat that might fail to advance (nullable expression inside ``*``,
``+`` or nested quantifiers) must be exercised in a separate process so the
parent can decide the outcome within one second instead of relying on a
global test-suite timeout to mask an infinite loop.
"""

import json
import os
import subprocess
import sys
import textwrap

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

SUBPROCESS_TIMEOUT_SECONDS = 1.0


def run_parse_snippet(snippet, timeout=SUBPROCESS_TIMEOUT_SECONDS):
    """Run ``snippet`` in a fresh Python subprocess and return its JSON result.

    The snippet must print exactly one line of JSON to stdout. Raises
    ``AssertionError`` if the subprocess does not finish within ``timeout``
    seconds (the parent, not a global timeout, decides the outcome) or if it
    exits abnormally.
    """
    code = textwrap.dedent(snippet)
    try:
        proc = subprocess.run(
            [sys.executable, '-c', code],
            capture_output=True, text=True,
            timeout=timeout, cwd=REPO_ROOT)
    except subprocess.TimeoutExpired as exc:
        raise AssertionError(
            'parse subprocess did not finish within %.1f s; probable '
            'non-advancing repeat (infinite loop).\nSnippet:\n%s'
            % (timeout, code)) from exc
    if proc.returncode != 0:
        raise AssertionError(
            'parse subprocess exited with code %s.\nstdout:\n%s\nstderr:\n%s'
            '\nSnippet:\n%s' % (proc.returncode, proc.stdout, proc.stderr, code))
    lines = [line for line in proc.stdout.splitlines() if line.strip()]
    assert len(lines) == 1, (
        'snippet must print exactly one JSON line, got: %r' % (proc.stdout,))
    return json.loads(lines[0])
