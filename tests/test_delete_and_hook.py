"""Deleting a clip really deletes it, and the hook teaser lands on the clip's loudest moment."""
from pathlib import Path
import json
import subprocess
import tempfile
import unittest
from fastapi import HTTPException
import app
import hooks


# A snap-packaged ffmpeg cannot read or write /tmp, so the media fixtures stay inside the project.
HERE = Path(__file__).resolve().parent


def workdir():
    return tempfile.TemporaryDirectory(dir=HERE, prefix='.tmp-')


def tone(path, seconds, loud_at):
    """`seconds` of quiet with one loud second starting at `loud_at`."""
    subprocess.run(['ffmpeg', '-y', '-v', 'error',
                    '-f', 'lavfi', '-i', f'sine=frequency=300:duration={seconds}',
                    '-f', 'lavfi', '-i', 'sine=frequency=300:duration=1',
                    '-filter_complex', f'[0:a]volume=0.02[q];[1:a]volume=1,adelay={int(loud_at*1000)}|{int(loud_at*1000)}[l];[q][l]amix=inputs=2:duration=first:normalize=0',
                    '-ac', '1', str(path)], check=True)


class HookTeaser(unittest.TestCase):
    def test_duration_is_off_or_two_to_ten_seconds(self):
        self.assertEqual(hooks.preroll({}), 0)
        self.assertEqual(hooks.preroll({'hook_clip': 0}), 0)
        self.assertEqual(hooks.preroll({'hook_clip': ''}), 0)
        self.assertEqual(hooks.preroll({'hook_clip': 5}), 5)
        for bad in (1, 1.9, 11, 60, float('nan'), float('inf')):
            with self.assertRaises(ValueError, msg=bad):
                hooks.preroll({'hook_clip': bad})

    def test_teaser_starts_just_before_the_loudest_moment(self):
        with workdir() as tmp:
            clip = Path(tmp) / 'a.wav'
            tone(clip, 20, 12)
            # a short run-in, then the bang: not three seconds of quiet with the payoff at the very end.
            # Pinned to a literal on purpose, so shrinking the run-in to nothing fails here.
            self.assertAlmostEqual(app.peak_window(clip, 4), 11.2, delta=.4)
            # a loud moment near the end still leaves room for the whole teaser
            tail = Path(tmp) / 'b.wav'
            tone(tail, 20, 19)
            self.assertLessEqual(app.peak_window(tail, 4), 16.01)

    def test_teaser_is_prepended_and_short_clips_are_left_alone(self):
        with workdir() as tmp:
            p = Path(tmp)
            clip = p / 'clip.mp4'
            subprocess.run(['ffmpeg', '-y', '-v', 'error', '-f', 'lavfi', '-i', 'testsrc2=s=320x568:r=30:d=18',
                            '-f', 'lavfi', '-i', 'sine=frequency=400:duration=18', '-shortest',
                            '-c:v', 'libx264', '-crf', '30', '-preset', 'ultrafast', '-pix_fmt', 'yuv420p',
                            '-c:a', 'aac', str(clip)], check=True)
            before = app._dur(clip)
            used = app.add_preroll(clip, 5, ['-c:v', 'libx264', '-crf', '30', '-preset', 'ultrafast'])
            self.assertEqual(used, 5)
            self.assertAlmostEqual(app._dur(clip), before + 5, delta=.6)

            tiny = p / 'tiny.mp4'
            subprocess.run(['ffmpeg', '-y', '-v', 'error', '-f', 'lavfi', '-i', 'testsrc2=s=320x568:r=30:d=3',
                            '-f', 'lavfi', '-i', 'sine=frequency=400:duration=3', '-shortest',
                            '-c:v', 'libx264', '-crf', '30', '-preset', 'ultrafast', '-pix_fmt', 'yuv420p',
                            '-c:a', 'aac', str(tiny)], check=True)
            short = app._dur(tiny)
            self.assertEqual(app.add_preroll(tiny, 5, ['-c:v', 'libx264', '-crf', '30', '-preset', 'ultrafast']), 0)
            self.assertAlmostEqual(app._dur(tiny), short, delta=.2)


class DeleteClip(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        p = Path(self.tmp.name)
        self.data, app.DATA, app.CLIPS = p, p, p / 'clips'
        app.CLIPS.mkdir()
        app.state.clear()
        # one finished clip: its MP4, record, subtitles and the dated copy in data/clips
        (p / 'aaa111.mp4').write_bytes(b'clip')
        (p / 'aaa111.srt').write_text('1\n', encoding='utf-8')
        (p / 'aaa111.ass').write_text('x', encoding='utf-8')
        (p / 'aaa111.vtt').write_text('x', encoding='utf-8')
        (p / 'aaa111.state.json').write_text('{}', encoding='utf-8')
        (p / 'clips' / '20261120-the-heist.mp4').write_bytes(b'clip')
        (p / 'aaa111.json').write_text(json.dumps({'source': 'src999', 'settings': {'title': 'The heist'},
                                                   'result': {'status': 'done', 'saved_as': '20261120-the-heist.mp4'}}), encoding='utf-8')
        (p / 'src999.mp4').write_bytes(b'the shared source video')
        # an unrelated neighbour that must survive every delete below
        (p / 'bbb222.mp4').write_bytes(b'other')
        (p / 'bbb222.srt').write_text('1\n', encoding='utf-8')
        (p / 'clips' / '20261120-keep-me.mp4').write_bytes(b'other')
        (p / 'bbb222.json').write_text(json.dumps({'source': 'src999', 'settings': {'title': 'Keep me'},
                                                   'result': {'status': 'done', 'saved_as': '20261120-keep-me.mp4'}}), encoding='utf-8')
        app.state['aaa111'] = {'status': 'done'}
        app.state['bbb222'] = {'status': 'done'}

    def assertNeighbourSurvived(self):
        for name in ('bbb222.mp4', 'bbb222.srt', 'bbb222.json', 'clips/20261120-keep-me.mp4'):
            self.assertTrue((self.data / name).exists(), name + ' belongs to another clip and must survive')
        self.assertIn('bbb222', app.state)

    def tearDown(self):
        self.tmp.cleanup()

    def test_removes_every_file_the_clip_owns_but_never_the_source(self):
        result = app.delete_clip('aaa111')
        self.assertEqual(result['clips'], 1, 'only the neighbour clip should be left in data/clips')
        for name in ('aaa111.mp4', 'aaa111.json', 'aaa111.srt', 'aaa111.ass', 'aaa111.vtt', 'aaa111.state.json'):
            self.assertIn(name, result['removed'])
            self.assertFalse((self.data / name).exists(), name)
        self.assertIn('20261120-the-heist.mp4', result['removed'])
        self.assertFalse((self.data / 'clips' / '20261120-the-heist.mp4').exists())
        self.assertTrue((self.data / 'src999.mp4').exists(), 'the shared source video must survive')
        self.assertNotIn('aaa111', app.state)
        self.assertNeighbourSurvived()

    def test_earlier_re_edited_versions_go_too(self):
        (self.data / 'old222.mp4').write_bytes(b'old')
        (self.data / 'clips' / '20261119-old.mp4').write_bytes(b'old')
        (self.data / 'old222.json').write_text(json.dumps({'source': 'src999', 'superseded_by': 'aaa111', 'settings': {},
                                                           'result': {'status': 'done', 'saved_as': '20261119-old.mp4'}}), encoding='utf-8')
        result = app.delete_clip('aaa111')
        self.assertEqual(result['versions'], 2)
        self.assertFalse((self.data / 'old222.mp4').exists())
        self.assertFalse((self.data / 'clips' / '20261119-old.mp4').exists())
        self.assertNeighbourSurvived()

    def test_a_planted_path_in_saved_as_cannot_escape_the_clips_folder(self):
        outside = self.data / 'src999.mp4'
        (self.data / 'aaa111.json').write_text(json.dumps({'source': 'src999', 'settings': {},
                                                           'result': {'status': 'done', 'saved_as': '../src999.mp4'}}), encoding='utf-8')
        app.delete_clip('aaa111')
        self.assertTrue(outside.exists(), 'saved_as must be treated as a bare name inside data/clips')

    def test_unknown_and_malformed_names_are_refused(self):
        for name in ('../app', 'nope123', 'a b'):
            with self.assertRaises(HTTPException, msg=name):
                app.delete_clip(name)


if __name__ == '__main__':
    unittest.main()
