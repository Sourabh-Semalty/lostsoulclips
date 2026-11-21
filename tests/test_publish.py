from pathlib import Path
import tempfile
import unittest
import app
class Publish(unittest.TestCase):
    """Every finished clip lands in data/clips under a readable name, and a failed copy never breaks an export."""
    def test_readable_name_unique_and_safe(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);app.CLIPS=p/'clips'
            src=p/'deadbeef1234.mp4';src.write_bytes(b'x')
            first=app.publish(src,'GTA 6: the heist / part 2!')
            self.assertEqual(first.parent,p/'clips')
            self.assertTrue(first.name.endswith('-GTA-6-the-heist-part-2.mp4'),first.name)
            self.assertEqual(first.read_bytes(),b'x')
            self.assertTrue(app.publish(src,'').name.endswith('-clip.mp4'))
            second=app.publish(src,'GTA 6: the heist / part 2!')   # same second, same title: no overwrite
            self.assertNotEqual(second,first)
            self.assertTrue(first.exists() and second.exists())
            self.assertIsNone(app.publish(p/'missing.mp4','x'))    # a broken publish returns None, does not raise
if __name__=='__main__':unittest.main()
