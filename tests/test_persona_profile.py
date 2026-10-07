import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import wechat_bot_app as app
import persona_support as support


class PersonaRegressionTests(unittest.TestCase):





    def test_no_target_never_falls_back_to_user_voice(self):
        for speaker in ['我', '未知']:
            with self.subTest(speaker=speaker), self.assertRaises(ValueError):
                app.fallback_soul('无目标', [app.Message(speaker, '只属于我的话', 'other.txt')])


    def test_generic_stats_count_complete_repeated_utterances(self):
        messages = [app.Message('对方', text, 'other.txt')
                    for text in ['@全体成员 新通知', '好呀', '好呀']]
        profile = app.build_local_profile(messages)
        self.assertEqual(profile['selected_target_total'], 2)
        self.assertEqual(profile['common'], ['好呀'])
        self.assertNotIn('问句约占', app.profile_text(profile))


    def test_unreviewed_online_prompt_filters_noise_and_redacts(self):
        messages = [app.Message('我', '号码13900001111', 'other.txt'),
                    app.Message('对方', '@全体成员 通知内容', 'other.txt'),
                    app.Message('对方', '好呀', 'other.txt')]
        with patch.object(app, 'call_model', return_value='offline test') as call:
            self.assertEqual(app.model_soul('新朋友', messages, 'unused', 'unused', 'unused'), 'offline test')
        prompt = call.call_args.args[-1]
        self.assertNotIn('13900001111', prompt)
        self.assertIn('[手机号]', prompt)
        self.assertNotIn('@全体成员', prompt)
        self.assertNotIn('二次元冷萌', prompt)

    def test_missing_messages_are_not_presented_as_adjacent(self):
        messages = [app.Message('我', '甲', 'other.txt'),
                    app.Message('对方', '乙', 'other.txt'),
                    app.Message('对方', '丙', 'other.txt')]
        prompt = support.generation_prompt('新朋友', messages, [messages[0], messages[2]])
        self.assertEqual(prompt.count('[另一个选取片段：不代表与上段连续]'), 2)

    def test_left_right_labels_do_not_change_message_pronouns(self):
        text = chr(10).join(['# 微信聊天记录', '左侧：我今天不想聊', '右侧：你先休息'])
        parsed = app.parse_lines(text)
        self.assertEqual([(m.speaker, m.content) for m in parsed],
                         [('对方', '我今天不想聊'), ('我', '你先休息')])

    def test_revised_export_excludes_preface_and_preserves_ordinals(self):
        text = chr(10).join([
            '# 聊天记录（2026-09-29 视频复核修订版，沿用原文件名）',
            '本轮核对视频：example.mp4。保留原序号及分行。',
            '### 04/17 22:20',
            '我：准备吃口饭',
            '对方：没吃早饭呢？',
            '核对说明：这一行不是聊天原话',
            '对方：先去吃饭',
        ])
        parsed = app.parse_lines(text)
        self.assertEqual([(m.speaker, m.content, m.line_number) for m in parsed], [
            ('我', '准备吃口饭', 4),
            ('对方', '没吃早饭呢？', 5),
            ('对方', '先去吃饭', 7),
        ])

    def test_explicit_bubble_position_overrides_sender_label(self):
        rows = [{'发送方': '我方', 'side': 'left', '内容': '左侧原话'},
                {'发送方': '对方', 'screen_side': 'right', '内容': '右侧原话'}]
        parsed = app.parse_json_messages(rows)
        self.assertEqual([(m.speaker, m.content) for m in parsed],
                         [('对方', '左侧原话'), ('我', '右侧原话')])

    def test_right_only_text_cannot_supply_target_persona(self):
        messages = [app.Message('right', '这只是右侧的人说的', 'other.txt')]
        with self.assertRaises(ValueError):
            support.build_local_profile(messages)

    def test_generation_prompt_separates_left_voice_from_right_context(self):
        messages = [app.Message('右侧', '右侧的研究经历', 'other.txt'),
                    app.Message('左侧', '左侧的回答', 'other.txt')]
        prompt = support.generation_prompt('测试朋友', messages, messages)
        self.assertIn('左侧（模仿对象）：左侧的回答', prompt)
        self.assertIn('右侧（仅作上下文）：右侧的研究经历', prompt)
        self.assertNotIn('左侧（模仿对象）：右侧的研究经历', prompt)




if __name__ == '__main__':
    unittest.main()
