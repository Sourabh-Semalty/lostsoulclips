"""Posting is outward-facing and hard to undo, so the parts that decide *what* gets sent and
*whether* it gets sent twice are the parts under test. No network and no ffmpeg: every upload
is stubbed, which is the point - a real upload would be a real post.
"""
import json
import tempfile
import unittest
from pathlib import Path
from fastapi import HTTPException
import social

# Shaped after a real record in data/: 'game' and 'cam' are crop rectangles and 'creator' is an
# id, not a name. An earlier version of this fixture used strings there and happily asserted the
# broken behaviour, which is why the live check mattered.
CLIP={'settings':{'title':'Insane 1v4 clutch','hook':'he had one bullet left',
                  'game':{'x':0,'y':0,'w':0.76,'h':1},'cam':{'x':0,'y':0,'w':.25,'h':.3},
                  'creator':'abc123def456','source_url':'https://youtu.be/abc123'},
      'result':{'status':'done','meta':{'duration':31.4}}}

def clip_dir(stack,record=CLIP,size=1024):
    """A finished clip on disk: the mp4 plus its job record, which is all social.py reads."""
    data=Path(stack.enter_context(tempfile.TemporaryDirectory()))
    (data/'abc123.mp4').write_bytes(b'x'*size)
    (data/'abc123.json').write_text(json.dumps(record),encoding='utf-8')
    return data

class Text(unittest.TestCase):
    def test_draft_uses_the_clips_own_metadata(self):
        d=social.draft(CLIP)
        self.assertEqual(d['title'],'Insane 1v4 clutch')
        self.assertIn('he had one bullet left',d['description'])
        self.assertIn('Source: https://youtu.be/abc123',d['description'])
        self.assertIn('clutch',d['tags'])                    # from the title
        self.assertIn('shorts',d['tags'])                    # the fixed set
        self.assertNotIn('the',d['tags'])                    # stop word
        self.assertNotIn('abc123def456',d['tags'])           # a creator id with no store to name it
        self.assertTrue(d['caption'].startswith('Insane 1v4 clutch'))
        self.assertEqual(d['privacy'],'private')             # never public by accident

    def test_crop_rectangles_never_become_hashtags(self):
        """settings['game'] and ['cam'] are crop regions, not names. Stringifying one produced
        '#x00031645569620253164y0006790611814345992w...' on a real clip in data/."""
        d=social.draft({'settings':{'title':'Activity highlight 14','cam':{'x':0,'y':0,'w':.25,'h':.3},
                                    'game':{'x':0.0031645569620253164,'y':0.006790611814345992,'w':.99,'h':.94}}})
        for tag in d['tags']:
            self.assertLessEqual(len(tag),30,tag)
            self.assertNotIn('0031645569620253164',tag)
        self.assertNotIn('14',d['tags'])                     # a bare number is not a hashtag
        self.assertEqual(sorted(d['tags']),['activity','clips','gaming','highlight','shorts'])

    def test_creator_id_is_resolved_to_a_readable_handle(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp)/'creators.store').write_text(json.dumps([{'id':'abc123def456','name':'kaicenat'}]),encoding='utf-8')
            d=social.draft({'settings':{'title':'clutch','creator':'abc123def456'}},tmp)
            self.assertIn('kaicenat',d['tags'])
            self.assertNotIn('abc123def456',d['tags'])       # the raw id is not a hashtag
            # A missing or unreadable store must not break the draft.
            self.assertNotIn('kaicenat',social.draft({'settings':{'title':'clutch','creator':'nope'}},tmp)['tags'])
            self.assertTrue(social.draft({'settings':{'title':'clutch','creator':'abc123def456'}})['tags'])

    def test_draft_survives_a_clip_with_no_metadata(self):
        d=social.draft({})
        self.assertEqual(d['title'],'LostSoulClips clip')
        self.assertNotIn('Source:',d['description'])

    def test_a_hostile_source_url_never_reaches_the_description(self):
        d=social.draft({'settings':{'title':'x','source_url':'javascript:alert(1)'}})
        self.assertNotIn('javascript',d['description'])

    def test_fit_trims_chars_and_bytes_separately(self):
        self.assertEqual(social.fit('abcdef',6),'abcdef')
        self.assertEqual(len(social.fit('a'*200,100)),100)
        hindi='नमस्ते '*500                                   # 3 bytes a letter: chars would pass, bytes must not
        self.assertLessEqual(len(social.fit(hindi,5000,'bytes').encode('utf-8')),5000)
        self.assertGreater(len(hindi.encode('utf-8')),5000)

    def test_tag_budget_counts_commas_and_quotes(self):
        self.assertEqual(social.tag_length([]),0)
        self.assertEqual(social.tag_length(['ab']),2)
        self.assertEqual(social.tag_length(['ab','cd']),5)           # 2 + comma + 2
        self.assertEqual(social.tag_length(['a b']),5)               # 3 + two quotes
        self.assertLessEqual(social.tag_length(social.fit_tags(['x'*90]*40,500)),500)

class Guards(unittest.TestCase):
    def test_clip_id_must_be_alphanumeric(self):
        with tempfile.TemporaryDirectory() as tmp:
            for bad in ('../../etc/passwd','a/b','..','abc-123','abc 123',''):
                with self.assertRaises(HTTPException,msg=bad) as c:social.clip_file(tmp,bad)
                self.assertEqual(c.exception.status_code,400)
            with self.assertRaises(HTTPException) as c:social.clip_file(tmp,'deadbeef')
            self.assertEqual(c.exception.status_code,404)             # well-formed but absent

    def test_over_limit_text_is_refused_not_quietly_trimmed(self):
        with tempfile.TemporaryDirectory() as tmp:
            mp4=Path(tmp)/'c.mp4';mp4.write_bytes(b'x')
            ok={'title':'t','description':'d','tags':['a'],'caption':'c','privacy':'private',
                'share_to_feed':True,'duration':30}
            social.validate('youtube',ok,mp4);social.validate('instagram',ok,mp4)   # the baseline passes
            for field,value,platform in (('title','t'*101,'youtube'),('title','  ','youtube'),
                                         ('description','d'*5001,'youtube'),('privacy','world','youtube'),
                                         ('caption','c'*2201,'instagram'),('caption','#t '*31,'instagram'),
                                         ('duration',1,'instagram'),('duration',9000,'instagram')):
                with self.assertRaises(HTTPException,msg=f'{platform}.{field}'):
                    social.validate(platform,{**ok,field:value},mp4)
            with self.assertRaises(HTTPException):
                social.validate('youtube',{**ok,'tags':['x'*90]*40},mp4)

    def test_oversized_clip_is_refused_before_upload(self):
        with tempfile.TemporaryDirectory() as tmp:
            mp4=Path(tmp)/'c.mp4';mp4.write_bytes(b'x')
            fields={'caption':'c','duration':30,'share_to_feed':True}
            social.validate('instagram',fields,mp4)
            social.LIMITS['instagram']['max_bytes']=0
            try:
                with self.assertRaises(HTTPException):social.validate('instagram',fields,mp4)
            finally:social.LIMITS['instagram']['max_bytes']=300*1024*1024

    def test_tokens_never_survive_into_a_message(self):
        for secret,text in (('EAAB123secret','?access_token=EAAB123secret&x=1'),
                            ('abc123','{"client_secret": "abc123"}'),
                            ('1//04xyz','{"refresh_token":"1//04xyz"}'),
                            ('4/0Axyz','https://x/cb?code=4/0Axyz&scope=y')):
            cleaned=social.scrub(text)
            self.assertNotIn(secret,cleaned,text)
            self.assertIn('***',cleaned,text)
        # Meta's numeric error code is the useful half of a failure and must survive.
        self.assertIn('190',social.scrub('{"message":"Invalid OAuth access token","code":190}'))

class ExpiredAccess(unittest.TestCase):
    """A refresh token dying is the normal case, not the exotic one: Google expires every refresh
    token after 7 days while the consent screen is in Testing."""

    def setUp(self):
        self.real=social.http;self.addCleanup(setattr,social,'http',self.real)
        self.cfg={'yt_client_id':'id','yt_client_secret':'secret','yt_refresh_token':'stale'}

    def fails_with(self, message):
        def boom(*a,**k):raise HTTPException(502,message)
        social.http=boom

    def test_a_dead_refresh_token_says_what_to_do(self):
        self.fails_with('oauth2.googleapis.com refused the request (400): {"error":"invalid_grant"}')
        with self.assertRaises(HTTPException) as c:
            social.yt_token(self.cfg,grant_type='refresh_token',refresh_token='stale')
        self.assertEqual(c.exception.status_code,400)
        self.assertIn('Connect YouTube again',c.exception.detail)
        self.assertIn('7 days',c.exception.detail)

    def test_other_failures_are_not_disguised_as_expiry(self):
        self.fails_with('oauth2.googleapis.com refused the request (500): upstream exploded')
        with self.assertRaises(HTTPException) as c:
            social.yt_token(self.cfg,grant_type='refresh_token',refresh_token='stale')
        self.assertEqual(c.exception.status_code,502)
        self.assertIn('upstream exploded',c.exception.detail)

class PrivacyIsNeverAssumed(unittest.TestCase):
    """A private-requested upload was seen going out public while the code reported 'private',
    because it echoed the request instead of YouTube's answer. It must never do that again."""

    def setUp(self):
        self.real=social.http;self.addCleanup(setattr,social,'http',self.real)
        self.data=tempfile.TemporaryDirectory();self.addCleanup(self.data.cleanup)
        self.mp4=Path(self.data.name)/'c.mp4';self.mp4.write_bytes(b'x')
        social.config=lambda data:{'yt_client_id':'i','yt_client_secret':'s','yt_refresh_token':'r','ig_token':'','ig_user':''}
        self.addCleanup(setattr,social,'config',social.__dict__['config'])
        social.yt_token=lambda cfg,**g:{'access_token':'tok'}
        self.addCleanup(setattr,social,'yt_token',social.__dict__['yt_token'])

    def upload(self,returned_status):
        calls={'n':0}
        def http(url,data=None,headers=None,method=None,timeout=120):
            calls['n']+=1
            if calls['n']==1:return 200,{'Location':'https://upload/session'},b''
            body={'id':'VID123'}
            if returned_status is not None:body['status']={'privacyStatus':returned_status}
            return 200,{},json.dumps(body).encode()
        social.http=http
        return social.youtube_upload(self.data.name,self.mp4,
            {'title':'t','description':'d','tags':[],'privacy':'private'},lambda m:None)

    def test_youtube_disagreeing_is_surfaced_not_hidden(self):
        out=self.upload('public')
        self.assertEqual(out['privacy'],'public')              # what YouTube said
        self.assertEqual(out['privacy_requested'],'private')   # what we asked for
        self.assertIn('public',out['privacy_warning'])
        self.assertIn('YouTube Studio',out['privacy_warning'])

    def test_silence_is_reported_as_unconfirmed_not_as_success(self):
        out=self.upload(None)
        self.assertIsNone(out['privacy'])
        self.assertIn('did not confirm',out['privacy_warning'])

    def test_agreement_raises_no_warning(self):
        out=self.upload('private')
        self.assertEqual(out['privacy'],'private')
        self.assertIsNone(out['privacy_warning'])

class Posting(unittest.TestCase):
    """Nothing here touches the network: UPLOADERS is replaced with stubs."""

    def setUp(self):
        self.real=dict(social.UPLOADERS);self.addCleanup(social.UPLOADERS.update,self.real)
        self.calls=[]
        self.stack=tempfile.TemporaryDirectory();self.addCleanup(self.stack.cleanup)
        self.data=Path(self.stack.name)
        (self.data/'abc123.mp4').write_bytes(b'x'*1024)
        (self.data/'abc123.json').write_text(json.dumps(CLIP),encoding='utf-8')
        self.fields=social.fields_for(self.data,'abc123',{})

    def stub(self,platform,fail=False):
        def upload(data,mp4,fields,note):
            self.calls.append(platform)
            if fail:raise HTTPException(502,'platform said no')
            return {'platform':platform,'id':platform+'-1','url':'https://example/'+platform}
        social.UPLOADERS[platform]=upload

    def test_one_platform_failing_neither_stops_nor_fakes_the_other(self):
        self.stub('youtube');self.stub('instagram',fail=True)
        out=social.post(self.data,'abc123',self.fields,['youtube','instagram'])
        self.assertEqual(self.calls,['youtube','instagram'])          # the failure did not abort the loop
        self.assertEqual([p['platform'] for p in out['posted']],['youtube'])
        self.assertEqual([f['platform'] for f in out['failed']],['instagram'])
        self.assertIn('platform said no',out['failed'][0]['error'])

    def test_every_requested_platform_lands_in_exactly_one_bucket(self):
        """A platform must never drop out silently: posted + failed + skipped == requested."""
        self.stub('youtube');self.stub('instagram',fail=True)
        out=social.post(self.data,'abc123',self.fields,['youtube','instagram'])
        seen=[r['platform'] for r in out['posted']+out['failed']+out['skipped']]
        self.assertEqual(sorted(seen),sorted(out['requested']))
        self.assertEqual(len(seen),len(set(seen)))

    def test_retry_after_a_partial_failure_does_not_double_post(self):
        self.stub('youtube');self.stub('instagram',fail=True)
        social.post(self.data,'abc123',self.fields,['youtube','instagram'])
        self.calls.clear();self.stub('instagram')                     # Instagram recovers; retry everything
        out=social.post(self.data,'abc123',self.fields,['youtube','instagram'])
        self.assertEqual(self.calls,['instagram'])                    # YouTube was not uploaded a second time
        self.assertEqual([s['platform'] for s in out['skipped']],['youtube'])
        self.assertEqual(out['skipped'][0]['url'],'https://example/youtube')
        self.assertEqual([p['platform'] for p in out['posted']],['instagram'])

    def test_repost_is_possible_but_only_when_asked_for(self):
        self.stub('youtube')
        social.post(self.data,'abc123',self.fields,['youtube'])
        self.calls.clear()
        out=social.post(self.data,'abc123',self.fields,['youtube'],repost=True)
        self.assertEqual(self.calls,['youtube'])
        self.assertEqual(len(out['posted']),1)

    def test_a_failed_platform_is_not_written_to_the_ledger(self):
        self.stub('youtube',fail=True)
        social.post(self.data,'abc123',self.fields,['youtube'])
        self.assertEqual(social.ledger(self.data).get('abc123',{}),{})

    def test_reviewed_text_wins_over_the_draft(self):
        fields=social.fields_for(self.data,'abc123',{'title':'My own title','tags':'#one, two','privacy':'unlisted'})
        self.assertEqual(fields['title'],'My own title')
        self.assertEqual(fields['tags'],['one','two'])
        self.assertEqual(fields['privacy'],'unlisted')
        self.assertEqual(fields['duration'],31.4)
        self.assertEqual(social.fields_for(self.data,'abc123',{'caption':''})['caption'],'')

if __name__=='__main__':unittest.main()
