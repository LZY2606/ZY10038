"""Surface the boundary/fuzz/mutation validation stages in the summary.

``python3 -m pytest -q`` otherwise prints only dots; this hook appends a
short section naming the new boundary-matrix, fuzz, and mutation tests
and their outcomes so an acceptance run shows what was verified.
"""

VALIDATION_MODULES = (
    'test_boundary_matrix',
    'test_grammar_fuzz',
    'test_mutation',
)


def pytest_terminal_summary(terminalreporter):
    stats = terminalreporter.stats
    lines = []
    for outcome in ('passed', 'failed', 'error', 'skipped'):
        for report in stats.get(outcome, []):
            if any(module in report.nodeid for module in VALIDATION_MODULES):
                lines.append((report.nodeid, outcome))
    if not lines:
        return
    terminalreporter.write_sep('=', 'boundary matrix / fuzz / mutation stages')
    for nodeid, outcome in sorted(lines):
        terminalreporter.write_line('%-7s %s' % (outcome.upper(), nodeid))
