"""Tk lifecycle for background runtime checks and workspace settings refresh."""
from __future__ import annotations

import hashlib
from pathlib import Path
import queue
import threading
import time

from runtime_status import POLL_SECONDS, fresh, probe_runtime


class RuntimeControls:
    def start_runtime_monitor(self):
        self._runtime_snapshot = None
        self._runtime_results = queue.SimpleQueue()
        self._runtime_check_busy = False
        self._runtime_closed = False
        self._runtime_next_check = 0.0
        self._runtime_signatures = {}
        self._runtime_sync_errors = {}
        self._runtime_refresh_again = False
        self._runtime_stale_shown = False
        self.protocol('WM_DELETE_WINDOW', self.close_workbench)
        self._runtime_timer = self.after(0, self._poll_runtime_results)

    def close_workbench(self):
        self._runtime_closed = True
        timer = getattr(self, '_runtime_timer', None)
        if timer:
            self.after_cancel(timer)
        voice_timer = getattr(self, '_voice_timer', None)
        if voice_timer:
            self.after_cancel(voice_timer)
        self.destroy()

    def refresh_runtime_status(self):
        self.render_accounts()
        self._runtime_next_check = 0.0
        self._request_runtime_check(force=True)

    def _request_runtime_check(self, force=False):
        if self._runtime_closed:
            return
        if self._runtime_check_busy:
            self._runtime_refresh_again |= force
            return
        self._runtime_check_busy = True
        previous = dict(self._runtime_signatures)
        api = self.services
        results = self._runtime_results

        def worker():
            signatures, errors, synced = {}, {}, []
            try:
                store = api.persona_store()
                from companion_media import MediaStore
                library = MediaStore(store)
                assignments = store.assignments()
                for persona in store.personas():
                    try:
                        profile = store.profile_path(persona['id'])
                        settings = profile.parent / 'media/settings.json'
                        voice_settings = profile.parent / 'voice-settings.json'
                        if profile.resolve() != profile or settings.resolve() != settings or voice_settings.resolve() != voice_settings:
                            raise ValueError('设定文件是链接，无法自动更新。')
                        if (profile.stat().st_size > 4 * 1024 * 1024 or settings.is_file() and settings.stat().st_size > 512 * 1024
                                or voice_settings.is_file() and voice_settings.stat().st_size > 512 * 1024):
                            raise ValueError('设定文件过大，无法自动更新。')
                        signature = hashlib.sha256(profile.read_bytes() + b'\0' +
                            (settings.read_bytes() if settings.is_file() else b'') + b'\0' +
                            (voice_settings.read_bytes() if voice_settings.is_file() else b'')).hexdigest()
                        expired = any(library.retirement_due(store.workspace(local_id, persona['id']))
                                      for local_id, entry in assignments.items() if entry.get('persona_id') == persona['id'])
                        if previous.get(persona['id']) != signature or expired:
                            store.sync_workspace_settings(persona['id'])
                            if previous.get(persona['id']) != signature and persona['id'] in previous:
                                synced.append(persona['name'])
                        signatures[persona['id']] = signature
                    except (OSError, ValueError, UnicodeError):
                        errors[persona['id']] = persona['name'] + '的设置未同步，请检查人格和表情文件。'
                snapshot = probe_runtime(Path(api.ROOT), store.config)
            except Exception:
                from runtime_status import RuntimeSnapshot
                snapshot = RuntimeSnapshot('unknown', rpc_error='config_unavailable')
            # The background thread never calls Tk; it can finish safely after close.
            results.put((snapshot, signatures, errors, synced))

        threading.Thread(target=worker, daemon=True, name='wechat-runtime-status').start()

    def _poll_runtime_results(self):
        if self._runtime_closed:
            return
        changed = False
        while True:
            try:
                snapshot, signatures, errors, synced = self._runtime_results.get_nowait()
            except queue.Empty:
                break
            self._runtime_snapshot = snapshot
            self._runtime_signatures = signatures
            self._runtime_check_busy = False
            self._runtime_next_check = 0.0 if self._runtime_refresh_again else time.monotonic() + POLL_SECONDS
            self._runtime_refresh_again = False
            for name in synced:
                self.write_log(name + '的设定已自动同步，下一轮聊天生效。')
            for identifier, message in errors.items():
                if self._runtime_sync_errors.get(identifier) != message:
                    self.write_log(message)
            self._runtime_sync_errors = errors
            changed = True
        stale = self._runtime_snapshot is not None and not fresh(self._runtime_snapshot)
        if changed or stale and not self._runtime_stale_shown:
            self.render_accounts()
        self._runtime_stale_shown = stale
        if time.monotonic() >= self._runtime_next_check:
            self._request_runtime_check()
        self._runtime_timer = self.after(250, self._poll_runtime_results)
