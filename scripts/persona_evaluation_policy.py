'''Local execution limits for persona experiments; never alters production settings.'''
import json
from pathlib import Path
from urllib.error import HTTPError

POLICY_ROOT = Path(__file__).resolve().parents[1] / 'data' / 'evaluations'


def enforce_models(directory, models, policy_root=None):
    root = Path(policy_root or POLICY_ROOT).resolve()
    directory = Path(directory).resolve()
    if not directory.is_relative_to(root):
        return
    policies = []
    current = directory
    while True:
        path = current / 'execution-policy.json'
        if path.exists():
            policy = json.loads(path.read_text(encoding='utf-8'))
            allowed = policy.get('allowed_models')
            if not isinstance(allowed, list) or not allowed or not all(isinstance(x,str) and x.strip() for x in allowed):
                raise ValueError('Invalid experiment model policy: '+str(path))
            policies.append((path,{x.strip().lower() for x in allowed}))
        if current == root:
            break
        current = current.parent
    for phase, model in models.items():
        if not isinstance(model,str) or not model.strip():
            raise ValueError('Missing model for '+phase)
        for path, allowed in policies:
            if model.strip().lower() not in allowed:
                raise ValueError('Model disallowed by user experiment policy: '+phase+'='+model+'; '+str(path))


def fatal_provider_status(error):
    visited = set()
    while error is not None and id(error) not in visited:
        visited.add(id(error))
        if isinstance(error,HTTPError) and error.code in {401,402,403}:
            return error.code
        error = error.__cause__ or error.__context__
    return None
