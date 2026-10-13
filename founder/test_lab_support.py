"""Offline feedback double for tests of unrelated lab boundaries."""
from unittest.mock import patch
from founder.models import LabSiteVersion
from founder.services.lab_reply import version_reply


def install_lab_mocks(case):
    def feedback(prompt, source, result, chosen):
        version = LabSiteVersion(source=source, html=result.text, kind=source.kind if source else 'static',
                                 backend_modules=chosen, input_tokens=result.input_tokens,
                                 output_tokens=result.output_tokens, model=result.model, edit_method=result.edit_method)
        if source: version.startup_id = source.startup_id
        facts = version_reply(version)
        return {'summary': 'Результат запроса сохранён.',
                'completed': [*facts['items'], *facts.get('more', [])][:5], 'not_done': [],
                'checks': {'review_mode': 'local'}}
    mock = patch('founder.lab_views.validate_result', side_effect=feedback)
    mock.start()
    case.addCleanup(mock.stop)
