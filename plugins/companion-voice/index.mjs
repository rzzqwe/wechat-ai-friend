import {spawn} from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
export function synthesizeWithPython(pythonExecutable, request, root) {
  return new Promise((resolve, reject) => {
    const child = spawn(pythonExecutable, ['-B', '-X', 'utf8', path.join(root, 'scripts/synthesize_companion_voice.py')],
      {cwd: root, windowsHide: true, shell: false, stdio: ['pipe', 'pipe', 'pipe']});
    let output = '', bytes = 0, finished = false;
    const fail = message => {
      if (finished) return;
      finished = true;
      clearTimeout(timer);
      child.kill();
      reject(new Error(message));
    };
    const timer = setTimeout(() => fail('Companion speech timed out; no paid fallback was used.'), 55000);
    child.stdout.on('data', data => {
      bytes += data.length;
      if (bytes > 15 * 1024 * 1024) return fail('Companion speech exceeded the response limit.');
      output += data.toString('utf8');
    });
    // The helper reports sanitized errors in JSON. Never retain raw diagnostic stderr.
    child.stderr.resume();
    child.on('error', () => fail('The companion speech helper could not start.'));
    child.stdin.on('error', () => fail('The companion speech helper closed unexpectedly.'));
    child.on('close', code => {
      if (finished) return;
      finished = true;
      clearTimeout(timer);
      try {
        const result = JSON.parse(output);
        if (code !== 0 || !result.ok) throw new Error('Companion speech unavailable; the text reply is retained.');
        const audio = Buffer.from(result.audio, 'base64');
        if (audio.length < 44 || audio.subarray(0, 4).toString() !== 'RIFF') throw new Error('Invalid companion audio.');
        resolve({audioBuffer: audio, outputFormat: 'wav', fileExtension: '.wav', voiceCompatible: false});
      } catch {
        reject(new Error('Companion speech unavailable; the text reply is retained.'));
      }
    });
    child.stdin.end(JSON.stringify(request));
  });
}

export default {
  id: 'wechat-companion-voice',
  name: '当前陪伴的语音',
  register(api) {
    const python = api.pluginConfig?.pythonExecutable;
    // OpenClaw can compile plugins into a cache. The approved project directory
    // comes from operator config, not the compiled entry's import.meta.url.
    const root = api.pluginConfig?.projectRoot;
    api.registerSpeechProvider({
      id: 'wechat-companion-voice', label: '当前陪伴（Fish Audio 在线语音）', defaultTimeoutMs: 55000,
      isConfigured: () => typeof python === 'string' && typeof root === 'string' && path.isAbsolute(root)
        && (fs.existsSync(path.join(root, 'data/fish-audio-key.bin'))
            || Boolean(process.env.FISH_API_KEY?.trim() || process.env.FISH_AUDIO_API_KEY?.trim())),
      synthesize: req => synthesizeWithPython(python, {
        localId: req.providerConfig?.localId, personaId: req.providerConfig?.personaId, text: req.text
      }, root)
    });
  }
};
