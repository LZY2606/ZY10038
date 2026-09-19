"""Repository-root pytest configuration.

Prints the names of the newly added boundary-matrix, generator and
mutation-stage tests in the terminal summary so that a plain
``python3 -m pytest -q`` acceptance run shows the new cases and
verification stages that ran.
"""

NEW_TEST_MODULES = (
    'test_boundary_matrix',
    'test_mini_grammar_generator',
    'test_mutation_testing',
)


def pytest_terminal_summary(terminalreporter):
    reporter = terminalreporter
    seen = {module: [] for module in NEW_TEST_MODULES}
    for outcome in ('passed', 'failed', 'skipped', 'error'):
        for report in reporter.stats.get(outcome, []):
            module = report.nodeid.split('::')[0].rsplit('/', 1)[-1]
            module = module[:-3] if module.endswith('.py') else module
            if module in seen:
                seen[module].append('%s [%s]' % (report.nodeid, outcome))
    if not any(seen.values()):
        return
    reporter.write_sep('=', 'boundary matrix / generator / mutation stages')
    for module in NEW_TEST_MODULES:
        if seen[module]:
            reporter.write_line('%s (%d):' % (module, len(seen[module])))
            for nodeid in seen[module]:
                reporter.write_line('  %s' % nodeid)
