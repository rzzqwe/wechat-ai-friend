"""Isolated Tk workbench check; no Gateway, network, keys, or real accounts."""
import json
import io
from pathlib import Path
import sys
import shutil
import tempfile
import time
import wave
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import wechat_bot_app as api
from account_personas import PersonaStore, read_json, write_json
from companion_speech import VoiceStore
from scripts.migrate_runtime_config import migrate

with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    store = PersonaStore(root / 'project', root / 'state')
    a = store.save('甲', '# 甲')
    b = store.save('乙', '# 乙')
    store.assign('a', a['id'], 'wx_a')
    store.assign('b', b['id'], 'wx_b')
    VoiceStore(store).save_service(a['id'], 'a' * 32, '甲音色', 'fixture owner permission', True)
    VoiceStore(store).save_service(b['id'], 'b' * 32, '乙音色', 'fixture owner permission', True)
    shutil.copytree(api.ROOT / 'plugins/companion-voice', store.root / 'plugins/companion-voice')
    write_json(store.config, migrate(read_json(store.config, {})))
    accounts = [{'id': key, 'name': key, 'status': '已绑定', 'last_action': '', 'enabled': True,
                 'provider_id': 'wx_' + key} for key in ('a', 'b')]
    with patch.object(api, 'persona_store', return_value=store), patch.object(api, 'load_accounts', return_value=accounts), \
            patch.object(api, 'load_app_config', return_value={'persona_id': a['id']}), \
            patch.object(api, 'API_KEY_FILE', root / 'no-key.bin'), \
            patch.object(api.App, 'start_runtime_monitor', return_value=None), \
            patch.object(api.App, '_reload_voice_runtime', return_value=None), \
            patch('fish_audio_client.load_key', return_value='fixture-key'):
        workbench = api.App()
        workbench.withdraw()
        try:
            deadline = time.monotonic() + 8
            while workbench._voice_apply_busy and time.monotonic() < deadline:
                workbench.update()
                time.sleep(.02)
            workbench.update()
            assert not workbench._voice_apply_busy
            assert workbench.current_persona_id == a['id']
            assert workbench._ui_voice_choice_var.get() == '甲音色'
            assert workbench._ui_voice_mode_var.get() == '文字'
            assert '甲音色' in workbench._voice_choices
            workbench._ui_voice_mode_var.set('文字和语音')
            workbench.save_voice_selection()
            deadline = time.monotonic() + 8
            while workbench._voice_apply_busy and time.monotonic() < deadline:
                workbench.update()
                time.sleep(.02)
            workbench.update()
            assert not workbench._voice_apply_busy
            assert VoiceStore(store).load(a['id'])['mode'] == 'smart'
            assert VoiceStore(store).load(a['id'])['voice_id'] == 'custom-fish'
            def generate_preview(key, text, output, *, voice_config):
                assert voice_config['reference_id'] == 'a' * 32
                assert len(text) <= 15
                audio = io.BytesIO()
                with wave.open(audio, 'wb') as sound:
                    sound.setparams((1, 2, 44100, 0, 'NONE', 'not compressed'))
                    sound.writeframes(b'\x01\x00' * 441)
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_bytes(audio.getvalue())
                return {'audio_seconds': 0.01}
            with patch('winsound.PlaySound') as play_sample, \
                    patch('fish_audio_client.generate_sample', side_effect=generate_preview), \
                    patch('voice_controls.threading.Thread') as preview_thread:
                workbench.preview_selected_voice()
                preview_thread.call_args.kwargs['target']()
                workbench._poll_voice_results()
                assert 'voice-previews' in play_sample.call_args.args[0]
                assert workbench._voice_preview_window.winfo_exists()
            workbench.update()
            workbench.persona_tree.selection_set(b['id'])
            workbench.select_persona()
            assert workbench._ui_voice_mode_var.get() == '文字'
            assert VoiceStore(store).load(b['id'])['mode'] == 'text'
            assert workbench._ui_voice_choice_var.get() == '乙音色'
            workbench.persona_tree.selection_set(a['id'])
            workbench.select_persona()
            assert workbench._ui_voice_mode_var.get() == '文字和语音'
            assert workbench._ui_voice_choice_var.get() == '甲音色'
            print(json.dumps({'ok': True, 'voiceControlsInOriginalWorkbench': True,
                'modeSaved': True, 'selectionFollowsCompanion': True, 'configuredVoicePreview': True,
                'voice': 'user-configured Fish Audio'}, ensure_ascii=False))
        finally:
            workbench.after_cancel(workbench._voice_timer)
            workbench.destroy()
