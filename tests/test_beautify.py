"""core 固定 + 前后缀拼接测试。"""

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from component import beautify  # noqa: E402


class BeautifyTest(unittest.TestCase):
    def test_core_is_never_changed(self):
        core = "张三投掷 1d100=61/100……【成功】"
        output = (
            '{"prefix":"灯影摇了一下……","suffix":"……但路还很长。"}'
        )
        self.assertEqual(
            beautify.assemble_decoration(core, output),
            "灯影摇了一下……张三投掷 1d100=61/100……【成功】……但路还很长。",
        )

    def test_empty_prefix_or_suffix_is_allowed(self):
        core = "李四 1d20=15……【失败】"
        self.assertEqual(
            beautify.assemble_decoration(core, '{"prefix":"","suffix":"……再来一次。"}'),
            "李四 1d20=15……【失败】……再来一次。",
        )
        self.assertEqual(
            beautify.assemble_decoration(core, '{"prefix":"他看了一眼。","suffix":""}'),
            "他看了一眼。李四 1d20=15……【失败】",
        )

    def test_markdown_fence_and_extra_text_are_tolerated(self):
        core = "王五 3d6=12"
        output = '好的：\n```json\n{"prefix":"骰子落下。","suffix":"……"}\n```'
        self.assertEqual(
            beautify.assemble_decoration(core, output),
            "骰子落下。王五 3d6=12……",
        )

    def test_invalid_json_falls_back_to_core(self):
        core = "赵六 1d100=88/70……【失败】"
        for bad in ("", "不是 JSON", '{"prefix": 1, "suffix": "x"}', "[]"):
            self.assertEqual(beautify.assemble_decoration(core, bad), core)

    def test_prompt_selection_migrates_legacy_default(self):
        self.assertEqual(
            beautify.select_system_prompt(
                "decorate", beautify.LEGACY_REWRITE_PROMPT
            ),
            beautify.DEFAULT_DECORATE_PROMPT,
        )
        self.assertEqual(
            beautify.select_system_prompt(
                "rewrite", beautify.DEFAULT_DECORATE_PROMPT
            ),
            beautify.LEGACY_REWRITE_PROMPT,
        )
        self.assertEqual(
            beautify.select_system_prompt("decorate", "自定义提示词"),
            "自定义提示词",
        )
        self.assertEqual(
            beautify.select_system_prompt("rewrite", "自定义提示词"),
            "自定义提示词",
        )

    def test_protocol_is_appended_to_custom_prompt(self):
        prompt = beautify.ensure_protocol("自定义风格")
        self.assertIn("自定义风格", prompt)
        self.assertIn("【输出协议】", prompt)
        self.assertIn('"prefix"', prompt)
        self.assertIn('"suffix"', prompt)

    def test_protocol_is_not_duplicated(self):
        prompt = beautify.ensure_protocol(beautify.DEFAULT_DECORATE_PROMPT)
        self.assertEqual(prompt.count("【输出协议】"), 1)

    def test_bonus_penalty_and_multi_shot_core_pass_through(self):
        core = (
            "第1发……十位骰[3,7]……取7……个位4……D100=74/70……【失败】\n"
            "第2发……十位骰[5,9]……取9……个位2……D100=92/70……【失败】"
        )
        output = '{"prefix":"枪声连着响起。","suffix":"……烟雾还没散。"}'
        rendered = beautify.assemble_decoration(core, output)
        self.assertTrue(rendered.startswith("枪声连着响起。"))
        self.assertIn(core, rendered)
        self.assertTrue(rendered.endswith("……烟雾还没散。"))


if __name__ == "__main__":
    unittest.main()
