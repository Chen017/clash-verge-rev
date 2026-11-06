import unittest
from release import next_build


class ReleaseRetryTest(unittest.TestCase):
    def test_failed_publication_does_not_reuse_an_existing_version(self):
        refs = [{'ref': 'refs/tags/v2.5.10-personal.9'},
                {'ref': 'refs/tags/v2.5.11-personal.10'},
                {'ref': 'refs/tags/v2.5.11'}]
        self.assertEqual(next_build(refs), 11)
        self.assertEqual(next_build([]), 1)


if __name__ == '__main__':
    unittest.main()
