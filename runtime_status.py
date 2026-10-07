"""Bounded, read-only probes and honest status labels for the workbench."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
import shutil
import subprocess
import time
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, build_opener

from account_personas import read_json
from runtime_tuning import project_runtime, project_runtime_environment

POLL_SECONDS = 10
STALE_SECONDS = 35
CHINA_TIME = timezone(timedelta(hours=8))


@dataclass(frozen=True)
class RuntimeSnapshot:
    gateway: str
    accounts: dict = field(default_factory=dict)
    rpc_error: str | None = None
    checked_at: float = field(default_factory=time.time)
    observed_at: float = field(default_factory=time.monotonic)


@dataclass(frozen=True)
class StatusView:
    title: str
    detail: str = ''
    tone: str = 'neutral'


def _http_json(url: str) -> dict:
    # These are loopback-only probes; do not route through configured web proxies.
    opener = build_opener(ProxyHandler({}))
    try:
        response = opener.open(url, timeout=1.5)
    except HTTPError as error:
        response = error
    except (OSError, URLError):
        return {}
    try:
        with response:
            data = response.read(131073)
        if len(data) > 131072:
            return {}
        value = json.loads(data)
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _channel_snapshot(root: Path, config_path: Path) -> dict:
    try:
        runtime = project_runtime(root)
        node = str(runtime[0] / 'node.exe') if runtime else shutil.which('node')
        command = str(runtime[1] / 'openclaw.cmd') if runtime else shutil.which('openclaw.cmd')
        if not node or not command:
            return {'ok': False, 'error': 'runtime_unavailable'}
        environment = project_runtime_environment(root)
        environment['WECHAT_AI_OPENCLAW_COMMAND'] = command
        result = subprocess.run(
            [node, str(root / 'scripts/gateway_status.mjs'), str(root), str(config_path)],
            cwd=root, env=environment, capture_output=True, text=True,
            encoding='utf-8', errors='replace', timeout=8,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if result.returncode or len(result.stdout) > 131072:
            return {'ok': False, 'error': 'status_unavailable'}
        value = json.loads(result.stdout)
        return value if isinstance(value, dict) else {'ok': False, 'error': 'status_unavailable'}
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired):
        return {'ok': False, 'error': 'status_unavailable'}


def probe_runtime(root: Path, config_path: Path) -> RuntimeSnapshot:
    try:
        if not config_path.is_file():
            return RuntimeSnapshot('unconfigured')
        config = read_json(config_path, {})
        gateway = config.get('gateway', {})
        port = gateway.get('port', 18789)
        if type(port) is not int or not 1 <= port <= 65535:
            return RuntimeSnapshot('unknown', rpc_error='config_unavailable')
        address = f"{'https' if gateway.get('tls', {}).get('enabled') else 'http'}://127.0.0.1:{port}"
    except (OSError, ValueError, TypeError, AttributeError):
        return RuntimeSnapshot('unknown', rpc_error='config_unavailable')
    with ThreadPoolExecutor(max_workers=3) as pool:
        health, startup, ready = list(pool.map(_http_json, [address + suffix for suffix in ('/healthz', '/startupz', '/readyz')]))
    if ready.get('ready') is True:
        state = 'ready'
    elif startup.get('status') == 'starting':
        state = 'starting'
    elif startup.get('status') == 'draining':
        state = 'draining'
    elif ready.get('ready') is False:
        state = 'blocked'
    elif health.get('ok') is True or startup.get('ok') is True:
        state = 'unconfirmed'
    else:
        return RuntimeSnapshot('unavailable')
    if state in ('starting', 'draining'):
        return RuntimeSnapshot(state)
    channel = _channel_snapshot(root, config_path)
    accounts = {}
    rows = channel.get('accounts', [])
    if channel.get('ok') is True and isinstance(rows, list):
        for row in rows:
            if isinstance(row, dict) and isinstance(row.get('accountId'), str) and row['accountId']:
                key = re.sub(r'[^a-z0-9_-]+', '-', row['accountId'].lower()).strip('-')
                accounts[key] = row
        return RuntimeSnapshot(state, accounts)
    return RuntimeSnapshot(state, rpc_error=channel.get('error', 'status_unavailable'))


def fresh(snapshot: RuntimeSnapshot | None, now: float | None = None) -> bool:
    return isinstance(snapshot, RuntimeSnapshot) and (time.monotonic() if now is None else now) - snapshot.observed_at <= STALE_SECONDS


def account_status(account: dict, snapshot: RuntimeSnapshot | None, *, now: float | None = None) -> StatusView:
    setup = account.get('status', '')
    provider = account.get('provider_id', '')
    if not provider:
        if setup == '等待扫码':
            return StatusView('等待扫码', '完成扫码后可运行', 'waiting')
        return StatusView('绑定失败' if setup == '绑定失败' else '未绑定',
                          '请重新扫码' if setup == '绑定失败' else '完成扫码后可运行',
                          'failed' if setup == '绑定失败' else 'neutral')
    if not fresh(snapshot, now):
        return StatusView('状态待确认', '尚未检测或检测结果已过期', 'waiting')
    if snapshot.gateway in ('starting', 'draining'):
        return StatusView('网关启动中' if snapshot.gateway == 'starting' else '网关重载中', '', 'waiting')
    if snapshot.gateway in ('unavailable', 'unconfigured', 'unknown'):
        return StatusView('网关未连接', '无法确认网关运行，请检查 OpenClaw', 'waiting')
    if snapshot.rpc_error:
        return StatusView('状态待确认', '网关已响应，但未取得微信接收状态', 'waiting')
    key = re.sub(r'[^a-z0-9_-]+', '-', str(provider).lower()).strip('-')
    row = snapshot.accounts.get(key)
    if row is None:
        return StatusView('账号未就绪', '当前网关未报告这个微信账号', 'waiting')
    if row.get('enabled') is False:
        return StatusView('停用中' if row.get('running') is True else '已停用', '', 'neutral')
    if row.get('configured') is False:
        return StatusView('需重新扫码', '微信凭证未就绪', 'failed')
    error = row.get('errorKind')
    if error:
        titles = {'login': ('需重新扫码', '微信登录已失效'), 'network': ('连接异常', '微信接收服务报告连接问题'),
                  'model': ('回复服务异常', '模型服务报告异常'), 'runtime': ('接收异常', '微信接收服务报告异常')}
        title, detail = titles.get(error, titles['runtime'])
        return StatusView(title, detail, 'failed')
    if row.get('restartPending') is True:
        return StatusView('重连中', '', 'waiting')
    if row.get('connected') is False:
        return StatusView('连接未就绪', '接收服务尚未确认连接', 'waiting')
    if row.get('running') is True and row.get('configured') is True and row.get('enabled') is True:
        if snapshot.gateway != 'ready':
            return StatusView('接收中·待就绪', '网关尚未完全就绪', 'waiting')
        return StatusView('接收服务运行', '微信接收服务正在运行', 'active')
    if row.get('lifecycle') in ('starting', 'restarting', 'reconnecting'):
        return StatusView('启动中' if row.get('lifecycle') == 'starting' else '重连中', '', 'waiting')
    if row.get('running') is False:
        return StatusView('接收未启动', '配置已存在，但接收服务未运行', 'waiting')
    return StatusView('状态待确认', '未取得完整接收状态', 'waiting')


def overview(snapshot: RuntimeSnapshot | None, accounts: list[dict], *, now: float | None = None) -> StatusView:
    if not fresh(snapshot, now):
        return StatusView('正在检查运行状态' if snapshot is None else '运行状态待确认', '', 'waiting')
    names = {'unconfigured': '网关未配置', 'unavailable': '网关未连接', 'starting': '网关启动中',
             'draining': '网关重载中', 'blocked': '网关尚未就绪', 'unknown': '网关状态待确认',
             'unconfirmed': '网关已响应·就绪待确认', 'ready': '网关运行中'}
    title = names.get(snapshot.gateway, '网关状态待确认')
    tone = 'active' if snapshot.gateway == 'ready' else 'waiting'
    if snapshot.gateway == 'ready':
        if snapshot.rpc_error:
            title += ' · 微信状态待确认'
            tone = 'waiting'
        else:
            bound = [account for account in accounts if account.get('provider_id')]
            views = [account_status(account, snapshot, now=now) for account in bound]
            active = sum(view.tone == 'active' for view in views)
            title += f' · 微信接收 {active}/{len(bound)}' if bound else ' · 暂无已绑定微信'
            if not views or active != len(views):
                tone = 'failed' if any(view.tone == 'failed' for view in views) else 'waiting'
    clock = datetime.fromtimestamp(snapshot.checked_at, CHINA_TIME).strftime('%H:%M:%S')
    return StatusView(title, f'最近检测 {clock}', tone)
