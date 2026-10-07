"""Prepare an OpenClaw 2026.9 config COPY; never overwrite the source."""
from copy import deepcopy
import json
from pathlib import Path
import sys


def migrate(config):
    result = deepcopy(config)
    result.get('meta', {}).pop('lastTouchedAt', None)
    tailscale = result.get('gateway', {}).get('tailscale', {})
    if tailscale.get('resetOnExit') and tailscale.get('mode', 'off') != 'off':
        raise ValueError('Active Tailscale reset policy needs manual migration')
    tailscale.pop('resetOnExit', None)
    agents = result.setdefault('agents', {})
    if 'list' in agents:
        if 'entries' in agents:
            raise ValueError('Ambiguous agent configuration: both list and entries exist')
        entries = {}
        for item in agents.pop('list'):
            agent_id = item.pop('id')
            if agent_id in entries:
                raise ValueError('Duplicate agent id')
            item.pop('default', None)
            entries[agent_id] = item
        agents['entries'] = entries
    if len(agents.get('entries', {})) > 1:
        agents['ownership'] = 'explicit'
    defaults = agents.get('defaults', {})
    if 'memorySearch' in defaults:
        memory = result.setdefault('memory', {})
        if 'search' in memory:
            raise ValueError('Conflicting memory defaults')
        memory['search'] = defaults.pop('memorySearch')
    result.get('memory', {}).get('search', {}).get('store', {}).pop('path', None)
    for agent_id, item in agents.get('entries', {}).items():
        if 'memorySearch' in item:
            memory = item.setdefault('memory', {})
            if 'search' in memory:
                raise ValueError('Conflicting agent memory configuration')
            memory['search'] = item.pop('memorySearch')
        item.get('memory', {}).get('search', {}).get('store', {}).pop('path', None)
        if agent_id.startswith('wechat-ai-'):
            item.setdefault('memory', {}).setdefault('search', {})['rememberAcrossConversations'] = False
    return result


if __name__ == '__main__':
    source, destination = map(Path, sys.argv[1:3])
    if source.resolve() == destination.resolve():
        raise ValueError('Choose a separate candidate file')
    config = json.loads(source.read_text(encoding='utf-8-sig'))
    with destination.open('x', encoding='utf-8') as output:
        json.dump(migrate(config), output, ensure_ascii=False, indent=2)
    print('Candidate config created; original is unchanged.')
