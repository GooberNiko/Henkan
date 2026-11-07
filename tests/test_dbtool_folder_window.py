"""The DBTool's folder windows are found by the DBTool's own prompt, not by the title Windows gives them
(which is translated: "Telusuri File atau Folder" on Indonesian Windows, and the build waited 30 minutes).

    python -I -m unittest discover -s tests -v
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from u2v import dbtool_build as B  # noqa: E402


class FolderPrompt(unittest.TestCase):
    def test_english(self):
        self.assertEqual(B.folder_prompt(['Select wavefile folder', '', 'OK', 'Cancel']), 0)
        self.assertEqual(B.folder_prompt(['Select segmentation toolkit folder', 'OK', 'Cancel']), 1)

    def test_indonesian_windows(self):
        # what the reporter's log showed: the DBTool's prompt, Windows' own words in Indonesian
        texts = ['Select wavefile folder', 'Select wavefile folder', '&Folder:', 'Buat Folder Baru', 'OK', 'Batal']
        self.assertEqual(B.folder_prompt(texts), 0)

    def test_other_windows(self):
        self.assertIsNone(B.folder_prompt(['One or more of the selected articulations have already been '
                                           'segmented. Do you want to continue?', 'Yes', 'No']))
        self.assertIsNone(B.folder_prompt([]))


if __name__ == '__main__':
    unittest.main()
