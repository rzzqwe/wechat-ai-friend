from copy import deepcopy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from runtime_tuning import prefer_configured_api_key, project_runtime, project_runtime_environment, install_windows_gateway_acceleration


class RuntimeTuningTests(unittest.TestCase):
    def test_gateway_acceleration_preserves_parameters_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / 'source.cjs'
            source.write_text('// fixture')
            launcher = root / 'gateway.cmd'
            original = chr(10).join(['@echo off', 'set TOKEN=fixture',
                                    chr(34) + 'C:/node folder/node.exe' + chr(34) + ' C:/runtime/index.js gateway --port 18789', ''])
            launcher.write_text(original, encoding='utf-8')
            self.assertTrue(install_windows_gateway_acceleration(root, source))
            output = launcher.read_text(encoding='utf-8')
            self.assertIn('set TOKEN=fixture', output)
            self.assertIn('gateway --port 18789', output)
            self.assertIn('--require', output)
            self.assertEqual(launcher.with_suffix('.cmd.before-native-paths').read_text(), original)
            self.assertFalse(install_windows_gateway_acceleration(root, source))
            self.assertEqual(launcher.read_text(), output)

    def test_managed_runtime_is_opt_in(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            self.assertIsNone(project_runtime(root))
            (root / 'data').mkdir()
            (root / 'data/runtime.json').write_text('{"enabled":false}')
            self.assertIsNone(project_runtime(root))

    def test_managed_runtime_precedes_path_and_preserves_other_environment(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            node, binary = root / 'node', root / 'bin'
            for directory, filename in [(node, 'node.exe'), (binary, 'openclaw.cmd')]:
                directory.mkdir()
                (directory / filename).touch()
            (root / 'data').mkdir()
            (root / 'data/runtime.json').write_text(json.dumps({
                'enabled': True, 'node_dir': str(node), 'bin_dir': str(binary)}), encoding='utf-8-sig')
            self.assertEqual(project_runtime(root), (node, binary))
            with patch.dict(os.environ, {'PATH': 'old-path', 'OPENCLAW_STATE_DIR': 'custom-state'}):
                env = project_runtime_environment(root)
                self.assertEqual(env['PATH'].split(os.pathsep), [str(node), str(binary), 'old-path'])
                self.assertEqual(env['OPENCLAW_STATE_DIR'], 'custom-state')
                self.assertEqual(os.environ['PATH'], 'old-path')

    def test_invalid_managed_runtime_does_not_silently_fall_back(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'data').mkdir()
            for saved in ['broken', '[]', json.dumps({'enabled': True, 'node_dir': 'relative', 'bin_dir': 'relative'}),
                          json.dumps({'enabled': True, 'node_dir': str(root), 'bin_dir': str(root)})]:
                (root / 'data/runtime.json').write_text(saved)
                with self.assertRaises(RuntimeError):
                    project_runtime(root)

    def configuration(self, **overrides):
        provider = {'api': 'openai-completions', 'apiKey': 'fixture-key', **overrides}
        return {'agents': {'defaults': {'model': {'primary': 'custom-fixture/model'}}},
                'models': {'providers': {'custom-fixture': provider}}}

    def test_explicit_key_skips_discovery_without_changing_model_or_secret(self):
        config = self.configuration()
        original = deepcopy(config)
        self.assertTrue(prefer_configured_api_key(config))
        original['models']['providers']['custom-fixture']['auth'] = 'api-key'
        self.assertEqual(config, original)
        self.assertFalse(prefer_configured_api_key(config))

    def test_preserves_existing_auth_and_managed_secrets(self):
        for overrides in ({'auth': 'oauth'}, {'auth': 'token'}, {'apiKey': ''},
                          {'apiKey': {'source': 'env', 'id': 'KEY'}}, {'apiKey': '$' + '{KEY}'},
                          {'apiKey': 'secretref-managed'}, {'api': 'anthropic-messages'}):
            with self.subTest(overrides=overrides):
                config = self.configuration(**overrides)
                original = deepcopy(config)
                self.assertFalse(prefer_configured_api_key(config))
                self.assertEqual(config, original)

    def test_preserves_other_providers_and_unconfigured_defaults(self):
        for config in ({}, {'agents': {'defaults': {'model': 'other/model'}}}):
            original = deepcopy(config)
            self.assertFalse(prefer_configured_api_key(config))
            self.assertEqual(config, original)
