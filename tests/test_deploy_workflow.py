from pathlib import Path
import re

import yaml


def test_deployment_is_main_only_and_environment_gated():
    workflow = yaml.safe_load(Path('.github/workflows/deploy.yml').read_text())
    triggers = workflow.get('on', workflow.get(True))
    assert set(triggers) == {'push', 'workflow_dispatch'}
    assert triggers['push']['branches'] == ['main']
    assert triggers['workflow_dispatch']['inputs']['environment']['options'] == ['dev', 'prod']
    job = workflow['jobs']['deploy']
    assert "github.ref == 'refs/heads/main'" in job['if']
    assert job['environment'] == "${{ inputs.environment || 'dev' }}"
    assert workflow['concurrency']['cancel-in-progress'] is False
    assert job['permissions'] == {'contents': 'read', 'id-token': 'write'}
    for step in job['steps']:
        if 'uses' in step:
            assert re.fullmatch(r'[\w./-]+@[0-9a-f]{40}', step['uses'])
