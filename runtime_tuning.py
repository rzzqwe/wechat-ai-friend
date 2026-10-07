"""Small, schema-supported runtime adjustments for companion chat."""

import json
import os
from pathlib import Path
import shutil
import uuid


def tune_companion_plugins(config: dict, plugin_path: Path) -> bool:
    """Limit implicit discovery for a companion-only host; preserve explicit plugins."""
    from copy import deepcopy
    original = deepcopy(config)
    plugins = config.setdefault('plugins', {})
    paths = plugins.setdefault('load', {}).setdefault('paths', [])
    selected = str(plugin_path.resolve())
    retained = []
    for candidate in paths:
        if not isinstance(candidate, str):
            retained.append(candidate)
            continue
        try:
            manifest = json.loads((Path(candidate) / 'openclaw.plugin.json').read_text(encoding='utf-8-sig'))
        except (OSError, ValueError):
            retained.append(candidate)
            continue
        if manifest.get('id') != 'wechat-companion-voice' or str(Path(candidate).resolve()) == selected:
            if candidate not in retained:
                retained.append(candidate)
    if selected not in retained:
        retained.append(selected)
    plugins['load']['paths'] = retained
    agents = config.get('agents', {}).get('entries', {})
    # Another type of agent may depend on implicitly enabled general-purpose plugins.
    only_companions = isinstance(agents, dict) and all(name == 'main' or name.startswith('wechat-ai-') for name in agents)
    owners = [config.get('agents', {}).get('defaults', {}), *agents.values()] if isinstance(agents, dict) else []
    model_refs = []
    for owner in owners:
        selection = owner.get('model', {})
        model_refs.extend([selection] if isinstance(selection, str) else
                          [selection.get('primary', ''), *selection.get('fallbacks', [])] if isinstance(selection, dict) else [])
        model_refs.extend(owner.get(field, '') for field in ('utilityModel',))
        model_refs.append(owner.get('heartbeat', {}).get('model', ''))
    # Native provider aliases and embedding providers can require auto plugins.
    only_custom_models = all(not ref or isinstance(ref, str) and ref.startswith('custom-') for ref in model_refs)
    only_custom_models = only_custom_models and all(name.startswith('custom-') for name in config.get('models', {}).get('providers', {}))
    if only_companions and only_custom_models and not isinstance(plugins.get('allow'), list):
        required = {'openclaw-weixin', 'wechat-companion-voice'}
        memory = plugins.get('slots', {}).get('memory', 'memory-core')
        if isinstance(memory, str) and memory != 'none':
            required.add(memory)
        required.update(name for name, value in plugins.get('entries', {}).items()
                        if isinstance(value, dict) and value.get('enabled') is True)
        required.update(name for name, value in config.get('channels', {}).items()
                        if isinstance(value, dict) and value.get('enabled') is not False)
        # Keep operator-supplied local plugins even if their entry omitted enabled.
        for candidate in retained:
            try:
                manifest = json.loads((Path(candidate) / 'openclaw.plugin.json').read_text(encoding='utf-8-sig'))
                if isinstance(manifest.get('id'), str):
                    required.add(manifest['id'])
            except (OSError, ValueError, TypeError):
                pass
        plugins['allow'] = sorted(required)
    return config != original


def install_windows_gateway_acceleration(state: Path, source: Path) -> bool:
    launcher = state / 'gateway.cmd'
    if not launcher.is_file() or not source.is_file():
        return False
    original = launcher.read_bytes()
    text = original.decode('utf-8-sig')
    # Newer managed services pin their runtime and supervise worker processes.
    # Keep their installer-owned definition intact; this shim is legacy-only.
    if '--task-supervisor' in text:
        return False
    target = state / 'runtime' / 'windows-native-paths.cjs'
    if 'windows-native-paths.cjs' in text:
        return False
    lines = text.splitlines(keepends=True)
    matches = []
    for index, line in enumerate(lines):
        stripped = line.strip()
        if 'gateway' not in stripped.split():
            continue
        if stripped.startswith(chr(34)):
            end = stripped.find(chr(34), 1) + 1
            executable = stripped[1:end - 1]
        else:
            executable = stripped.split(' ', 1)[0]
            end = len(executable)
        if Path(executable.replace(chr(92), '/')).name.lower() == 'node.exe':
            matches.append((index, stripped, end))
    if len(matches) != 1:
        raise ValueError('Cannot uniquely identify the gateway Node command')
    index, line, end = matches[0]
    ending = chr(13) + chr(10) if lines[index].endswith(chr(13) + chr(10)) else chr(10)
    lines[index] = line[:end] + ' --require ' + chr(34) + str(target) + chr(34) + line[end:] + ending
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
    backup = launcher.with_suffix('.cmd.before-native-paths')
    if not backup.exists():
        backup.write_bytes(original)
    temporary = launcher.with_name(launcher.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        temporary.write_bytes(''.join(lines).encode('utf-8'))
        os.replace(temporary, launcher)
    finally:
        temporary.unlink(missing_ok=True)
    return True


def project_runtime(root: Path) -> tuple[Path, Path] | None:
    manifest = root / 'data' / 'runtime.json'
    if not manifest.exists():
        return None
    try:
        saved = json.loads(manifest.read_text(encoding='utf-8-sig'))
        if saved.get('enabled') is not True:
            return None
        node_dir = Path(saved['node_dir'])
        bin_dir = Path(saved['bin_dir'])
        if not node_dir.is_absolute() or not bin_dir.is_absolute():
            raise ValueError('Runtime paths must be absolute')
        if not (node_dir / 'node.exe').is_file() or not (bin_dir / 'openclaw.cmd').is_file():
            raise ValueError('Runtime executable is missing')
        return node_dir, bin_dir
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        raise RuntimeError('Project runtime configuration is invalid: data/runtime.json') from exc


def project_runtime_environment(root: Path) -> dict[str, str]:
    env = os.environ.copy()
    runtime = project_runtime(root)
    if runtime:
        env['PATH'] = os.pathsep.join((str(runtime[0]), str(runtime[1]), env.get('PATH', '')))
    return env


def prefer_configured_api_key(config: dict) -> bool:
    """Avoid external credential/plugin discovery for our plaintext custom key."""
    selected = config.get('agents', {}).get('defaults', {}).get('model', '')
    primary = selected.get('primary', '') if isinstance(selected, dict) else selected
    if not isinstance(primary, str) or '/' not in primary:
        return False
    provider_id = primary.split('/', 1)[0]
    if not provider_id.startswith('custom-'):
        return False
    provider = config.get('models', {}).get('providers', {}).get(provider_id)
    if not isinstance(provider, dict) or provider.get('api') != 'openai-completions':
        return False
    key = provider.get('apiKey')
    if provider.get('auth') is not None or not isinstance(key, str) or not key.strip():
        return False
    # Preserve environment references and externally managed secret mechanisms.
    if key.startswith('$') or key.lower() in ('none', 'dummy', 'local', 'secretref-managed'):
        return False
    provider['auth'] = 'api-key'
    return True
