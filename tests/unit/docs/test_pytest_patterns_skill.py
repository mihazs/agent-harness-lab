"""The pytest-patterns adaptation states which runner rule wins over the conversion rule (review of #353); synthetic only."""
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[3]
SKILL = ROOT / '.agents' / 'skills' / 'pytest-patterns' / 'SKILL.md'


def adaptation_bullets():
    section = SKILL.read_text(encoding='utf-8').split('## Repository Adaptation', 1)[1]
    return {line.split('**')[1]: line for line in section.splitlines() if line.startswith('- **')}


class AdaptationPrecedenceTests(unittest.TestCase):
    def test_runner_rule_wins_and_conversion_waits_for_the_runner_move(self):
        bullets = adaptation_bullets()
        conversion, runners = bullets['Keep behaviour and strength.'], bullets['Runners.']
        self.assertIn('When a file moves to pytest style', conversion)
        self.assertIn('This rule wins over the conversion above', runners)
        self.assertIn('stays a `unittest.TestCase` and keeps its `subTest`', runners)
        self.assertIn('only in the same change that moves its runner to pytest', runners)

    def test_unittest_discovery_skips_a_converted_module_level_test(self):
        # The hazard the runner rule guards against: unittest collects TestCase methods only.
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, 'test_synthetic.py').write_text(
                'import unittest\n'
                'class Kept(unittest.TestCase):\n'
                '    def test_kept(self):\n'
                '        for value in (1, 2):\n'
                '            with self.subTest(value=value):\n'
                '                self.assertEqual(value, value)\n'
                'def test_converted():\n'
                '    assert False\n', encoding='utf-8')
            suite = unittest.defaultTestLoader.discover(tmp, pattern='test_*.py', top_level_dir=tmp)
            self.assertEqual(suite.countTestCases(), 1)


if __name__ == '__main__':
    unittest.main()
