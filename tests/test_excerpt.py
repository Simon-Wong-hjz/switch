"""最新回复摘录的回归用例。运行：python3 test_excerpt.py"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tb.excerpt import LIMIT, excerpt  # noqa: E402


class Excerpt(unittest.TestCase):
    def test_memory_footer_is_removed_before_excerpt_limit(self):
        footer = '<oai-mem-citation>\n<citation_entries>source</citation_entries>\n</oai-mem-citation>'
        self.assertEqual(excerpt('完成。\n\n' + footer), '完成。')
        self.assertEqual(excerpt('字' * LIMIT + '\n' + footer), '字' * LIMIT)
        self.assertEqual(excerpt(footer), '')

    def test_citation_examples_and_incomplete_tags_remain_visible(self):
        for text in ['`<oai-mem-citation>example</oai-mem-citation>`',
                     '```xml\n<oai-mem-citation>example</oai-mem-citation>\n```',
                     '<oai-mem-citation>unfinished',
                     '<oai-mem-citation>example</oai-mem-citation>\nExplanation follows.']:
            self.assertEqual(excerpt(text), text)

    def test_spans_lines_without_rewriting_markdown_or_blank_lines(self):
        text = '## 结论\n\n**验证完成**，三个 workflow 均已通过：\n\n- Demo：[main](https://example.com/build) 和 `test-build`\n'
        self.assertEqual(excerpt(text), text)

    def test_code_operators_paths_comments_and_indentation_are_preserved(self):
        for text in ['```python\n# 注释\nresult = 2 ** 3\n```',
                     '不要改动 `**/*.py`。', '2**3**4', '    # indented code\n    result = 8']:
            with self.subTest(text=text):
                self.assertEqual(excerpt(text), text)

    def test_short_reply_is_kept_whole_without_ellipsis(self):
        self.assertEqual(excerpt('好了'), '好了')
        self.assertEqual(excerpt(''), '')
        self.assertEqual(excerpt(None), '')

    def test_long_reply_is_cut_at_limit_with_ellipsis(self):
        out = excerpt('字' * (LIMIT + 50))
        self.assertEqual(len(out), LIMIT + 1)
        self.assertTrue(out.endswith('…'))
        self.assertEqual(excerpt('字' * LIMIT), '字' * LIMIT)   # 恰好到上限不加省略号
        text = '## 结论\n\n```python\n# 注释\nresult = 2 ** 3\n```\n' * 10
        self.assertEqual(excerpt(text), text[:LIMIT] + '…')


if __name__ == '__main__':
    unittest.main(verbosity=1)
