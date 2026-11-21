"""The copy writer turns frame descriptions into a post. The model is stubbed: these tests are
about what we send it and what we trust coming back, which is where the damage happens - a
hallucinated title goes out to a real channel.
"""
import json
import unittest
import viral
import social

FRAMES=['A red sports car parked outside a neon-lit bar at night.',
        'The car pulls away from the kerb as people cross the street.',
        'The car drives along a palm-lined seafront.']

class Prompt(unittest.TestCase):
    def setUp(self):
        self.sent=[];self.reply='{}'
        self.real=viral.ask;self.addCleanup(setattr,viral,'ask',self.real)
        viral.ask=lambda name,prompt,budget=320:(self.sent.append(prompt),self.reply)[1]

    def test_the_clips_own_frames_reach_the_model(self):
        self.reply=json.dumps({'title':'t','description':'d','tags':['a','b','c']})
        viral.copy_for(FRAMES,'',name='gemma4:e2b')
        prompt=self.sent[0]
        for frame in FRAMES:self.assertIn(frame,prompt)
        self.assertIn('Frame 1:',prompt);self.assertIn('Frame 3:',prompt)

    def test_channel_voice_is_shown_as_examples(self):
        self.reply=json.dumps({'title':'t','description':'d','tags':['a']})
        viral.copy_for(FRAMES,name='gemma4:e2b')
        prompt=self.sent[0]
        self.assertIn('GTA 6 bar lighting is actually unreal',prompt)      # few-shot, not adjectives
        self.assertIn('Invent no crash, chase, death or outcome',prompt)   # the anti-hallucination rule

    def test_chatter_is_passed_but_marked_as_unreliable(self):
        self.reply=json.dumps({'title':'t','description':'d','tags':['a']})
        viral.copy_for(FRAMES,'maybe we go to the shower',name='gemma4:e2b')
        prompt=self.sent[0]
        self.assertIn('maybe we go to the shower',prompt)
        self.assertIn('background chatter',prompt)

    def test_no_frames_means_no_invented_copy(self):
        """With nothing seen, writing a title would be pure invention. Return nothing instead."""
        self.assertIsNone(viral.copy_for([],'lots of talking',name='gemma4:e2b'))
        self.assertIsNone(viral.copy_for(['','  '],name='gemma4:e2b'))
        self.assertEqual(self.sent,[])                                      # the model was never called

class Parsing(unittest.TestCase):
    def setUp(self):
        self.reply='{}'
        self.real=viral.ask;self.addCleanup(setattr,viral,'ask',self.real)
        viral.ask=lambda name,prompt,budget=320:self.reply

    def out(self,reply):
        self.reply=reply;return viral.copy_for(FRAMES,name='gemma4:e2b')

    def test_a_good_answer_is_cleaned_not_trusted_raw(self):
        got=self.out(json.dumps({'title':'  Vice City at night is unreal 🌴\n\n','description':'Line  one.\nLine two.',
                                 'tags':['GTA 6','#Shorts','vice_city','ok','x','12345']}))
        self.assertEqual(got['title'],'Vice City at night is unreal 🌴')     # trimmed, emoji kept
        self.assertEqual(got['description'],'Line one. Line two.')           # newlines collapsed
        self.assertEqual(got['tags'],['gta6','shorts','vicecity'])           # '#' and '_' stripped, 'ok'/'x' too short
        self.assertNotIn('12345',got['tags'])                                # a bare number is not a tag

    def test_junk_from_the_model_never_becomes_a_post(self):
        for reply in ('not json at all','[]','null','{"description":"no title here"}','{"title":"   "}'):
            self.assertIsNone(self.out(reply),reply)

    def test_an_overlong_title_is_cut_to_the_youtube_limit(self):
        got=self.out(json.dumps({'title':'x'*400,'description':'d','tags':['aaa']}))
        self.assertEqual(len(got['title']),100)
        social.validate('youtube',{**got,'privacy':'private'},_AnyFile())   # must now pass YouTube's check

class _AnyFile:
    def stat(self):
        class S:st_size=1
        return S()

class DraftUsesIt(unittest.TestCase):
    def test_written_copy_wins_over_the_auto_title(self):
        record={'settings':{'title':'Activity highlight 4','game':{'x':0,'y':0,'w':1,'h':1},
                            'viral':{'title':'This GTA 6 beach drive ends badly 💀',
                                     'description':"It was going so well 🌴 and then it wasn't 💀",
                                     'tags':['gta6','vicecity','shorts'],'model':'gemma4:e2b'}}}
        d=social.draft(record)
        self.assertEqual(d['title'],'This GTA 6 beach drive ends badly 💀')
        self.assertIn("going so well",d['description'])
        self.assertEqual(d['tags'],['gta6','vicecity','shorts'])
        self.assertNotIn('activity',d['tags'])                               # the auto-title words are gone
        self.assertEqual(d['written_by'],'gemma4:e2b')

    def test_without_written_copy_the_old_template_still_works(self):
        d=social.draft({'settings':{'title':'Activity highlight 4'}})
        self.assertEqual(d['title'],'Activity highlight 4')
        self.assertIsNone(d['written_by'])
        self.assertIn('shorts',d['tags'])

    def test_a_model_that_returns_rubbish_tags_cannot_poison_the_draft(self):
        """Every junk tag is dropped, including a dict. With none left the template fills in,
        rather than the post going out with no tags at all."""
        d=social.draft({'settings':{'title':'x','viral':{'title':'T','description':'D',
                                                         'tags':['#ok','a',' ','12',{'x':1}]}}})
        self.assertEqual(d['title'],'T')
        self.assertEqual(d['tags'],['shorts','gaming','clips'])              # fell back, did not emit junk
        for tag in d['tags']:
            self.assertTrue(tag.isalnum() and not tag.isdigit() and tag.islower(),tag)

if __name__=='__main__':unittest.main()
