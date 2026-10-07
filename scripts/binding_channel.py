"""Pause only Weixin; recover interrupted attempts without enabling unrouted credentials."""
import os
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from account_personas import locked,read_json,write_json


def marker_path(store):
    return store.state/'wechat-ai-binding-pause.json'


def account_ids(store):
    value=read_json(store.state/'openclaw-weixin/accounts.json',[])
    if not isinstance(value,list) or any(not isinstance(item,str) for item in value):
        raise ValueError('微信账号索引无效。')
    return value


def owner_alive(pid):
    if not isinstance(pid,int) or pid<=0:
        return False
    if os.name=='nt':
        import ctypes
        from ctypes import wintypes
        kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        kernel.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD]
        kernel.OpenProcess.restype=wintypes.HANDLE
        kernel.GetExitCodeProcess.argtypes=[wintypes.HANDLE,ctypes.POINTER(wintypes.DWORD)]
        kernel.CloseHandle.argtypes=[wintypes.HANDLE]
        handle=kernel.OpenProcess(0x1000,False,pid)
        if not handle:
            return ctypes.get_last_error()==5
        try:
            code=wintypes.DWORD()
            return not kernel.GetExitCodeProcess(handle,ctypes.byref(code)) or code.value==259
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid,0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def _resume(store, marker, config):
    entries=config.get('agents',{}).get('entries',{})
    routed={row.get('match',{}).get('accountId') for row in config.get('bindings',[])
            if row.get('agentId') in entries and row.get('match',{}).get('channel')=='openclaw-weixin'}
    channel=config.setdefault('channels',{}).setdefault('openclaw-weixin',{})
    for account,previous in marker.get('account_flags',{}).items():
        entry=channel.setdefault('accounts',{}).setdefault(account,{})
        if previous['present']:
            entry['enabled']=previous['value']
        else:
            entry.pop('enabled',None)
        if not previous['entry_present'] and not entry:
            channel['accounts'].pop(account,None)
    for account in set(account_ids(store))-set(marker['accounts_before']):
        if account not in routed:
            channel.setdefault('accounts',{}).setdefault(account,{})['enabled']=False
    if marker['enabled_present']:
        channel['enabled']=marker['enabled_before']
    else:
        channel.pop('enabled',None)
    write_json(store.config,config)
    marker_path(store).unlink(missing_ok=True)


def pause(store,pid):
    with locked(store.lock_path),locked(store.config.with_suffix('.wechat-ai.lock')):
        marker=read_json(marker_path(store),{})
        if marker:
            if owner_alive(marker.get('pid')):
                raise ValueError('另一扫码窗口正在使用微信渠道。')
            _resume(store,marker,read_json(store.config,{}))
        config=read_json(store.config,{})
        channel=config.setdefault('channels',{}).setdefault('openclaw-weixin',{})
        marker={'pid':pid,'root':str(store.root),'config':str(store.config.resolve()),
                'accounts_before':account_ids(store),'enabled_present':'enabled' in channel,
                'enabled_before':channel.get('enabled',True)}
        accounts=channel.get('accounts',{})
        marker['account_flags']={account:{'present':'enabled' in accounts.get(account,{}),
            'value':accounts.get(account,{}).get('enabled',True),'entry_present':account in accounts}
            for account in set(marker['accounts_before']) | set(accounts)}
        write_json(marker_path(store),marker)
        channel['enabled']=False
        for account in marker['account_flags']:
            channel.setdefault('accounts',{}).setdefault(account,{})['enabled']=False
        write_json(store.config,config)


def resume(store,pid=None):
    with locked(store.lock_path),locked(store.config.with_suffix('.wechat-ai.lock')):
        marker=read_json(marker_path(store),{})
        if not marker:
            return False
        if marker.get('config')!=str(store.config.resolve()):
            raise ValueError('扫码暂停记录的配置路径不匹配。')
        if pid is None:
            if owner_alive(marker.get('pid')):
                raise ValueError('扫码仍在进行，未恢复微信渠道。')
        elif marker.get('pid')!=pid:
            raise ValueError('扫码窗口归属不匹配。')
        _resume(store,marker,read_json(store.config,{}))
        return True


if __name__=='__main__':
    import wechat_bot_app as app
    try:
        action=sys.argv[1]
        pid=int(sys.argv[2]) if len(sys.argv)>2 else None
        if action=='pause' and pid:pause(app.persona_store(),pid)
        elif action=='resume':resume(app.persona_store(),pid)
        else:raise ValueError('未知渠道操作。')
        print('微信渠道设置已保存。')
    except (OSError,ValueError,RuntimeError):
        print('微信渠道暂停或恢复失败，未开始新的扫码。',file=sys.stderr)
        raise SystemExit(1)
