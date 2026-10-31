"""LC-41 LC-D13: the frontier, MC and margin-loop panels show the edited-since-study banner.

The behaviour is a component test per panel (vitest, run by the GUI frontend CI job). This
guard keeps the manifest row honest from the backend side: each panel reads the route's
`edited_since_study` through the shared banner, and its vitest file asserts the banner.
"""
from pathlib import Path

import pytest

RESULTS = Path(__file__).resolve().parents[2] / 'frontend' / 'src' / 'pages' / 'results'
PANELS = {
    'FrontierPanel': 'frontier-edited',
    'McPanel': 'mc-edited',
    'MarginLoopPanel': 'margin-loop-edited',
}


@pytest.mark.parametrize('panel', PANELS)
def test_the_panel_renders_the_shared_banner_from_its_route_flag(panel):
    source = (RESULTS / f'{panel}.tsx').read_text(encoding='utf-8')
    assert 'EditedSinceStudyBanner' in source
    assert f'edited={{payload?.edited_since_study}} testId="{PANELS[panel]}"' in source


@pytest.mark.parametrize('panel', PANELS)
def test_the_panel_has_a_component_test_for_each_flag_value(panel):
    test = (RESULTS / f'{panel}.test.tsx').read_text(encoding='utf-8')
    assert f"'{PANELS[panel]}'" in test
    for case in ('[true, true]', '[false, false]', '[null, false]'):
        assert case in test
