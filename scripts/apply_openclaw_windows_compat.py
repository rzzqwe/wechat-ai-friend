import json
from pathlib import Path
import sys

DEFAULTS = {
    'Triggers.LogonTrigger.Enabled': 'true',
    'Settings.AllowHardTerminate': 'true',
    'Settings.StartWhenAvailable': 'false',
    'Settings.RunOnlyIfNetworkAvailable': 'false',
    'Settings.AllowStartOnDemand': 'true',
    'Settings.Enabled': 'true',
    'Settings.Hidden': 'false',
    'Settings.RunOnlyIfIdle': 'false',
    'Settings.WakeToRun': 'false',
    'Settings.Priority': '7',
}

def patch_task_audit(source):
    marker = '// WeChat workbench: Windows omits task fields equal to native defaults.'
    if marker in source:
        return source
    tab, nl = chr(9), chr(10)
    anchor = tab + 'const nativeDefaults = {' + nl
    check = tab * 2 + 'if (seen.has(key) || node.children.length ||'
    if source.count(anchor) != 1 or source.count(check) != 1:
        raise ValueError('Unrecognized task audit implementation; no patch applied')
    entries = ''.join(tab * 2 + json.dumps(k) + ': ' + json.dumps(v) + ',' + nl for k, v in DEFAULTS.items())
    source = source.replace(anchor, tab + marker + nl + anchor + entries, 1)
    return source.replace(check, tab * 2 + 'if (nativeDefaults[key] === node.textContent || seen.has(key) || node.children.length ||', 1)


def patch_windows_entry(source, supervisor_module):
    marker='// WeChat workbench: cache the managed runtime and dispatch its supervisor directly.'
    if marker in source:
        return source.replace('setInterval(() => module.flushCompileCache?.(), 2000).unref();',
                              '// Cache flush occurs at process exit, not on a repeating startup timer.')
    anchor='async function loadLegacyCliDeps() {\n'
    imports='import process from "node:process";\n'
    if source.count(anchor)!=1 or source.count(imports)!=1:
        raise ValueError('Unrecognized Windows runtime entry; no patch applied')
    bootstrap='''
// WeChat workbench: cache the managed runtime and dispatch its supervisor directly.
const wechatManagedGateway = process.platform === "win32" &&
  process.env.OPENCLAW_SERVICE_MARKER === "openclaw" && process.env.OPENCLAW_SERVICE_KIND === "gateway";
if (wechatManagedGateway && process.env.OPENCLAW_STATE_DIR && !process.env.NODE_DISABLE_COMPILE_CACHE) {
  const module = await import("node:module");
  const path = await import("node:path");
  const directory = process.env.NODE_COMPILE_CACHE || path.join(process.env.OPENCLAW_STATE_DIR, "runtime", "node-compile-cache");
  const status = module.enableCompileCache?.(directory);
  if (status && [module.constants.compileCacheStatus.ENABLED, module.constants.compileCacheStatus.ALREADY_ENABLED].includes(status.status)) {
    process.env.NODE_COMPILE_CACHE = directory;
    // Cache flush occurs at process exit, not on a repeating startup timer.
  }
}
'''
    dispatch='''\tif (wechatManagedGateway && process.argv.includes("--task-supervisor") &&
\t\t!process.argv.some(value => value.startsWith("--task-supervisor-child"))) {
\t\tconst { runWindowsGatewayTaskSupervisor } = await import("./SUPERVISOR_MODULE");
\t\tconst module = await import("node:module");
\t\tmodule.flushCompileCache?.();
\t\treturn { runCli: () => runWindowsGatewayTaskSupervisor() };
\t}
'''.replace('SUPERVISOR_MODULE',supervisor_module)
    return source.replace(imports,imports+bootstrap,1).replace(anchor,anchor+dispatch,1)


def write_patch(target, result, suffix):
    original=target.read_bytes()
    updated=result.encode('utf-8')
    if updated==original:
        return False
    backup=target.with_suffix(target.suffix+suffix)
    if not backup.exists():
        backup.write_bytes(original)
    temporary=target.with_suffix(target.suffix+'.wechat-tmp')
    temporary.write_bytes(updated)
    temporary.replace(target)
    return True

def install(root):
    root = Path(root).resolve()
    metadata = json.loads((root / 'package.json').read_text(encoding='utf-8'))
    if metadata.get('name') != 'openclaw' or metadata.get('version') != '2026.9.6':
        raise ValueError('This patch is only verified for OpenClaw 2026.9.6')
    files = list((root / 'dist').glob('service-audit-schtasks-*.mjs'))
    if len(files) != 1:
        raise ValueError('Cannot uniquely identify the task audit module')
    target = files[0]
    changed=write_patch(target,patch_task_audit(target.read_text(encoding='utf-8')),'.before-wechat-windows-defaults')
    entry=root/'dist/index.js'
    supervisors=list((root/'dist').glob('task-supervisor-*.mjs'))
    if entry.is_file() and len(supervisors)==1:
        result=patch_windows_entry(entry.read_text(encoding='utf-8'),supervisors[0].name)
        changed=write_patch(entry,result,'.before-wechat-startup') or changed
    return changed

if __name__ == '__main__':
    print(json.dumps({'patched': install(sys.argv[1])}))
