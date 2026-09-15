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
    assert not any(name.startswith('TF_VAR_') for name in job['env'])
    steps = {step.get('name'): step for step in job['steps']}
    aws_step = next(step for step in job['steps'] if step.get('uses', '').startswith('aws-actions/'))
    assert aws_step['with']['role-to-assume'] == '${{ secrets.AWS_DEPLOY_ROLE_ARN }}'
    assert steps['Configure backend']['env'] == {
        'TF_VAR_state_bucket': '${{ secrets.TF_STATE_BUCKET }}',
    }
    assert steps['Deploy infrastructure']['env'] == {
        'TF_VAR_state_bucket': '${{ secrets.TF_STATE_BUCKET }}',
        'TF_VAR_bedrock_resource_arns': "${{ secrets.BEDROCK_RESOURCE_ARNS || '[]' }}",
        'TF_VAR_slack_team_id': '${{ secrets.SLACK_TEAM_ID }}',
        'TF_VAR_slack_api_app_id': '${{ secrets.SLACK_API_APP_ID }}',
    }
    for step in job['steps']:
        if 'uses' in step:
            assert re.fullmatch(r'[\w./-]+@[0-9a-f]{40}', step['uses'])


def test_rollback_recording_uses_terraform_function_names(tmp_path):
    import json
    import os
    import subprocess

    workflow = yaml.safe_load(Path('.github/workflows/deploy.yml').read_text())
    script = next(step['run'] for step in workflow['jobs']['deploy']['steps']
                  if step.get('name') == 'Record rollback versions')
    config = {'extra': {'FunctionName': 'custom-dev-extra'}}
    for name, body in {
        'terraform': f"printf '%s\\n' '{json.dumps(config)}'",
        'aws': 'printf "%s\\n" "$*" >> "$CALLS"; echo 7',
    }.items():
        tool = tmp_path / name
        tool.write_text('#!/bin/sh\n' + body + '\n')
        tool.chmod(0o755)
    result = subprocess.run(
        ['bash', '-e', '-o', 'pipefail', '-c', script], text=True, capture_output=True,
        env={**os.environ, 'PATH': f'{tmp_path}:{os.environ["PATH"]}',
             'ENV': 'dev', 'CALLS': str(tmp_path / 'calls'),
             'GITHUB_STEP_SUMMARY': str(tmp_path / 'summary')},
    )
    assert result.returncode == 0, result.stderr
    assert (tmp_path / 'calls').read_text().splitlines() == [
        'lambda get-alias --function-name custom-dev-extra --name current --query FunctionVersion --output text'
    ]
    assert (tmp_path / 'summary').read_text() == 'custom-dev-extra: 7\n'
