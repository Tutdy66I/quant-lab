import json
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]


class NotebookTextTests(unittest.TestCase):
    def test_chinese_source_and_documentation_link_are_intact(self):
        notebook = json.loads(
            (ROOT / "notebooks/btc_ma_cross_backtest.ipynb").read_text(encoding="utf-8")
        )
        for index, cell in enumerate(notebook["cells"]):
            source = "".join(cell["source"])
            with self.subTest(cell=index):
                self.assertNotRegex(source, r"\?{2,}|\ufffd", "Lossy text replacement in notebook")
                self.assertRegex(source, r"[\u4e00-\u9fff]", "Chinese text is missing")
        markdown = "\n".join(
            "".join(cell["source"]) for cell in notebook["cells"]
            if cell["cell_type"] == "markdown"
        )
        for heading in ("BTC 双均线策略回测", "## 交易明细", "## 结果统计", "## 中文解读"):
            self.assertIn(heading, markdown)
        links = re.findall(r"`(docs/[^`]+\.md)`", markdown)
        self.assertIn("docs/策略迭代报告.md", links)
        for link in links:
            self.assertTrue((ROOT / link).is_file(), link)
