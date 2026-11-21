"""Post a finished clip to YouTube and Instagram, after you have read the text that goes with it.

Nothing leaves this machine until POST /social/post. GET /social/draft/{clip} only fills in
title, description, tags and caption from what the clip already knows about itself.

Credentials live in data/social.json (the data directory is gitignored) or in environment
variables, are written chmod 600, and are stripped out of every error message by scrub().

Why urllib instead of google-api-python-client: the entire job is two POSTs and a PUT per
platform. The official client would pull google-auth, oauthlib, httplib2 and protobuf into an
install that needs none of them today.

Why Instagram works at all without a public URL: the documented video_url path makes Meta
fetch the file off a public server, which would mean ngrok or a bucket. upload_type=resumable
lets us POST the bytes straight to rupload.facebook.com instead, so a local clip is enough.
"""
import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from fastapi import HTTPException
from fastapi.responses import HTMLResponse

YT_SCOPE='https://www.googleapis.com/auth/youtube.upload'
YT_AUTH='https://accounts.google.com/o/oauth2/v2/auth'
YT_TOKEN='https://oauth2.googleapis.com/token'
YT_UPLOAD='https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&part=snippet,status'
GRAPH='https://graph.facebook.com/v27.0'
RUPLOAD='https://rupload.facebook.com/ig-api-upload/v27.0'
GAMING_CATEGORY='20'
# What the platforms actually enforce. Checked before any request, so an over-long caption
# fails here with a sentence worth reading instead of as Meta code 36004 / subcode 2207010.
LIMITS={'youtube':{'title':100,'description':5000,'tags':500},
        'instagram':{'caption':2200,'hashtags':30,'min_seconds':3,'max_seconds':900,'max_bytes':300*1024*1024}}
PRIVACY=('private','public','unlisted')
CREDENTIALS=(('yt_client_id','LOSTSOULCLIPS_YT_CLIENT_ID'),
             ('yt_client_secret','LOSTSOULCLIPS_YT_CLIENT_SECRET'),
             ('yt_refresh_token','LOSTSOULCLIPS_YT_REFRESH_TOKEN'),
             ('ig_token','LOSTSOULCLIPS_IG_TOKEN'),
             ('ig_user','LOSTSOULCLIPS_IG_USER'))

def scrub(text):
    """Keep tokens and secrets out of anything we show or store. The OAuth `code=` parameter is
    matched only in its query-string form, so Meta's numeric error code - which is the useful
    half of a failure message - still comes through."""
    return re.sub(r'((?:access_token|client_secret|refresh_token)["\']?\s*[=:]\s*["\']?|\bcode=)[^&\s"\',}]+',r'\1***',str(text))

def http(url,data=None,headers=None,method=None,timeout=120):
    """One request. Returns (status, headers, body); raises with the API's own words, scrubbed."""
    request=urllib.request.Request(url,data=data,headers=headers or {},method=method)
    try:
        with urllib.request.urlopen(request,timeout=timeout) as response:
            return response.status,dict(response.headers),response.read()
    except urllib.error.HTTPError as e:
        raise HTTPException(502,scrub(f'{urllib.parse.urlsplit(url).netloc} refused the request ({e.code}): '+e.read()[:1500].decode("utf-8","replace")))
    except (urllib.error.URLError,TimeoutError,OSError) as e:
        raise HTTPException(502,scrub(f'Could not reach {urllib.parse.urlsplit(url).netloc}: {e}'))

# ---------------------------------------------------------------- credentials

def config(data):
    """data/social.json, with an environment variable winning over the file (as donate() does)."""
    stored=read_json(Path(data)/'social.json')
    return {key:str(os.environ.get(env) or stored.get(key) or '').strip() for key,env in CREDENTIALS}

def read_json(path):
    try:value=json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError,ValueError):return {}
    return value if isinstance(value,dict) else {}

def save_config(data,**fields):
    path=Path(data)/'social.json';stored=read_json(path)
    stored.update(fields);path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(stored,indent=1),encoding='utf-8')
    try:os.chmod(path,0o600)      # tokens: this user only
    except OSError:pass           # Windows and some mounts have no mode bits; the file still works

def connected(data):
    cfg=config(data)
    return {'youtube':{'ready':bool(cfg['yt_refresh_token']),'configured':bool(cfg['yt_client_id'] and cfg['yt_client_secret'])},
            'instagram':{'ready':bool(cfg['ig_token'] and cfg['ig_user']),'configured':bool(cfg['ig_token'] and cfg['ig_user'])}}

# ------------------------------------------------------------------ post text

HASHTAG_STOP={'the','a','an','and','or','but','of','to','in','on','for','with','is','it','its','this',
              'that','you','your','my','me','we','they','he','she','at','as','be','was','are','from','got'}

def fit(text,limit,unit='chars'):
    """Trim to a platform limit. YouTube counts the description in bytes, which starts to matter
    the moment a Hindi or emoji title goes through."""
    text=str(text or '')
    if unit=='bytes':
        raw=text.encode('utf-8')
        return text if len(raw)<=limit else raw[:limit-3].decode('utf-8','ignore').rstrip()+'…'
    return text if len(text)<=limit else text[:limit-1].rstrip()+'…'

def tag_length(tags):
    """YouTube's 500-character tag budget also counts the commas between tags and the quotation
    marks it adds around any tag containing a space."""
    return sum(len(t)+(2 if ' ' in t else 0) for t in tags)+max(0,len(tags)-1)

def fit_tags(tags,limit):
    kept=[]
    for tag in tags:
        if tag_length(kept+[tag])>limit:break
        kept.append(tag)
    return kept

def clean_tag(raw):
    """A hashtag both platforms accept. Anything that is not a string is dropped rather than
    stringified: settings['game'] and settings['cam'] hold crop rectangles, not names, and
    str() of one makes a 76-character nonsense tag (seen on real clips, not hypothetically)."""
    if not isinstance(raw,str):return ''
    tag=re.sub(r'[^0-9a-z]','',raw.lower())[:30]
    return '' if tag.isdigit() else tag      # '#14' off the end of an auto title is noise

def creator_name(data,settings):
    """settings['creator'] is an id; the readable handle lives in creators.store (not *.json,
    because app.py reads every data/*.json as a clip record)."""
    cid=settings.get('creator')
    if not isinstance(cid,str) or not cid or data is None:return ''
    try:rows=json.loads((Path(data)/'creators.store').read_text(encoding='utf-8'))
    except (OSError,ValueError):return ''
    for row in rows if isinstance(rows,list) else []:
        if isinstance(row,dict) and row.get('id')==cid:return str(row.get('name') or '')
    return ''

def hashtags(settings,limit,creator=''):
    """Tags out of what the clip actually knows: its creator, then words from the title."""
    out=[]
    for raw in (creator,'shorts','gaming','clips'):
        tag=clean_tag(raw)
        if tag and tag not in out:out.append(tag)
    title=settings.get('title')
    for word in re.findall(r"[A-Za-z0-9']+",title if isinstance(title,str) else ''):
        tag=clean_tag(word)
        if len(tag)>2 and tag not in HASHTAG_STOP and tag not in out:out.append(tag)
    return out[:limit]

def draft(record,data=None):
    """Prefill the post from the clip's own metadata.

    settings['viral'] is the copy written from the clip's frames by viral.py and is preferred when
    present. Without it this falls back to the clip's auto title, which for a batch export is
    something like 'Activity highlight 4' - honest, but not a post."""
    settings=record.get('settings') or {}
    written=settings.get('viral') if isinstance(settings.get('viral'),dict) else {}
    title=str(written.get('title') or settings.get('title') or 'LostSoulClips clip').strip() or 'LostSoulClips clip'
    hook=str(written.get('description') or settings.get('hook') or settings.get('post_caption') or '').strip()
    source=str(settings.get('source_url') or '').strip()
    saved=[t for t in (clean_tag(x) for x in (written.get('tags') or [])) if len(t)>2]
    tags=saved[:LIMITS['instagram']['hashtags']] or hashtags(settings,LIMITS['instagram']['hashtags'],creator_name(data,settings))
    extra=[hook] if hook and hook.lower()!=title.lower() else []
    if source.startswith(('http://','https://')):extra.append('Source: '+source)
    tagline=' '.join('#'+t for t in tags)
    return {'title':fit(title,LIMITS['youtube']['title']),
            'description':fit('\n\n'.join(extra+[tagline]).strip(),LIMITS['youtube']['description'],'bytes'),
            'tags':fit_tags(tags,LIMITS['youtube']['tags']),
            'caption':fit('\n\n'.join([title]+extra+[tagline]).strip(),LIMITS['instagram']['caption']),
            'written_by':written.get('model') or None,
            'privacy':'private'}

def fields_for(data,file,body):
    """Reviewed text wins; anything the user left out falls back to the draft."""
    record=read_json(Path(data)/(file+'.json'))
    base=draft(record,data);tags=body.get('tags')
    if isinstance(tags,str):tags=[t.strip().lstrip('#') for t in tags.split(',') if t.strip().lstrip('#')]
    if not isinstance(tags,list):tags=base['tags']
    return {'title':str(body.get('title') or base['title'])[:1000],
            'description':str(base['description'] if body.get('description') is None else body['description'])[:20000],
            'tags':[str(t)[:100] for t in tags][:60],
            'caption':str(base['caption'] if body.get('caption') is None else body['caption'])[:10000],
            'privacy':str(body.get('privacy') or base['privacy']),
            'share_to_feed':body.get('share_to_feed',True) is not False,
            'duration':(record.get('result') or {}).get('meta',{}).get('duration')}

def validate(platform,fields,mp4):
    """Everything the platform would reject, refused here first. Truncating silently would mean
    posting text the user never read, so these raise instead."""
    if platform=='youtube':
        limit=LIMITS['youtube']
        if not fields['title'].strip():raise HTTPException(400,'Give the video a title.')
        if len(fields['title'])>limit['title']:raise HTTPException(400,f"YouTube titles stop at {limit['title']} characters; this one is {len(fields['title'])}.")
        size=len(fields['description'].encode('utf-8'))
        if size>limit['description']:raise HTTPException(400,f"YouTube descriptions stop at {limit['description']} bytes; this one is {size}.")
        if tag_length(fields['tags'])>limit['tags']:raise HTTPException(400,f"YouTube counts all tags together and stops at {limit['tags']} characters; these are {tag_length(fields['tags'])}.")
        if fields['privacy'] not in PRIVACY:raise HTTPException(400,'Privacy must be private, public or unlisted.')
        return
    limit=LIMITS['instagram'];caption=fields['caption']
    if len(caption)>limit['caption']:raise HTTPException(400,f"Instagram captions stop at {limit['caption']} characters; this one is {len(caption)}.")
    if caption.count('#')>limit['hashtags']:raise HTTPException(400,f"Instagram allows {limit['hashtags']} hashtags; this caption has {caption.count('#')}.")
    size=mp4.stat().st_size
    if size>limit['max_bytes']:raise HTTPException(400,f"Instagram stops at {limit['max_bytes']//1048576} MB; this clip is {size/1048576:.0f} MB.")
    seconds=fields.get('duration')
    if seconds and not limit['min_seconds']<=seconds<=limit['max_seconds']:
        raise HTTPException(400,f"Instagram Reels must be {limit['min_seconds']}–{limit['max_seconds']} seconds; this clip is {seconds:.0f}.")

# -------------------------------------------------------------------- uploads

def clip_file(data,file):
    """The only way this module resolves a path. isalnum() matches delete_clip in app.py and is
    what stops a '../' id from reaching the filesystem."""
    if not str(file).isalnum():raise HTTPException(400,'Choose a saved clip.')
    mp4=Path(data)/(str(file)+'.mp4')
    if not mp4.exists():raise HTTPException(404,'Clip not found.')
    return mp4

def yt_token(cfg,**grant):
    if not (cfg['yt_client_id'] and cfg['yt_client_secret']):
        raise HTTPException(400,'Add your Google OAuth client id and secret first.')
    body=urllib.parse.urlencode({'client_id':cfg['yt_client_id'],'client_secret':cfg['yt_client_secret'],**grant}).encode()
    try:
        _,_,raw=http(YT_TOKEN,body,{'Content-Type':'application/x-www-form-urlencoded'},'POST',30)
    except HTTPException as e:
        # invalid_grant means the refresh token is dead, and the usual reason is mundane: while
        # the consent screen is in Testing, Google expires every refresh token after 7 days.
        # Saying so beats making the user decode an OAuth error string.
        if 'invalid_grant' in str(e.detail):
            raise HTTPException(400,'YouTube access expired. Press Connect YouTube again. To stop this '
                'recurring every 7 days, set the OAuth consent screen to "In production" instead of "Testing".')
        raise
    return json.loads(raw or b'{}')

def yt_consent(cfg,redirect):
    if not cfg['yt_client_id']:raise HTTPException(400,'Add your Google OAuth client id first.')
    return YT_AUTH+'?'+urllib.parse.urlencode({'client_id':cfg['yt_client_id'],'redirect_uri':redirect,
        'response_type':'code','scope':YT_SCOPE,'access_type':'offline','prompt':'consent'})

def youtube_upload(data,mp4,fields,note):
    cfg=config(data)
    if not cfg['yt_refresh_token']:raise HTTPException(400,'Connect your YouTube channel first.')
    token=yt_token(cfg,grant_type='refresh_token',refresh_token=cfg['yt_refresh_token']).get('access_token')
    if not token:raise HTTPException(502,'Google returned no access token. Connect the channel again.')
    size=mp4.stat().st_size
    meta={'snippet':{'title':fields['title'],'description':fields['description'],
                     'tags':fields['tags'],'categoryId':GAMING_CATEGORY},
          'status':{'privacyStatus':fields['privacy'],'selfDeclaredMadeForKids':False}}
    note('Opening the YouTube upload')
    _,headers,_=http(YT_UPLOAD,json.dumps(meta).encode(),
        {'Authorization':'Bearer '+token,'Content-Type':'application/json; charset=UTF-8',
         'X-Upload-Content-Length':str(size),'X-Upload-Content-Type':'video/mp4'},'POST',60)
    session=headers.get('Location') or headers.get('location')
    if not session:raise HTTPException(502,'YouTube did not open an upload session.')
    note(f'Uploading {size/1048576:.0f} MB to YouTube')
    # ponytail: whole clip held in memory. Clips are seconds long; chunk the PUT if long-form lands.
    _,_,raw=http(session,mp4.read_bytes(),{'Authorization':'Bearer '+token,'Content-Type':'video/mp4'},'PUT',1800)
    video=json.loads(raw or b'{}')
    if not video.get('id'):raise HTTPException(502,'YouTube took the upload but returned no video id.')
    # Report what YouTube says, not what we asked for. A private-requested upload has been seen
    # going out public, and echoing the request made that look like a success. The upload-only
    # scope cannot read the video back, so when YouTube stays silent this stays unconfirmed.
    got=(video.get('status') or {}).get('privacyStatus')
    return {'platform':'youtube','id':video['id'],'url':'https://youtu.be/'+video['id'],
            'privacy_requested':fields['privacy'],'privacy':got,
            'privacy_warning':None if got==fields['privacy'] else
                f"YouTube did not confirm '{fields['privacy']}'"+(f", it reports '{got}'" if got else ' (upload-only scope cannot read it back)')+'. Check the video in YouTube Studio.'}

def instagram_upload(data,mp4,fields,note):
    cfg=config(data)
    # ponytail: a 60-day token pasted from the Meta dashboard. Swap in the full OAuth refresh
    # dance only once re-pasting it twice a year actually becomes annoying.
    if not (cfg['ig_token'] and cfg['ig_user']):raise HTTPException(400,'Add your Instagram token and account id first.')
    size=mp4.stat().st_size
    note('Opening the Instagram container')
    _,_,raw=http(f"{GRAPH}/{urllib.parse.quote(cfg['ig_user'])}/media",
        urllib.parse.urlencode({'media_type':'REELS','upload_type':'resumable','caption':fields['caption'],
            'share_to_feed':'true' if fields['share_to_feed'] else 'false','access_token':cfg['ig_token']}).encode(),
        {'Content-Type':'application/x-www-form-urlencoded'},'POST',60)
    container=json.loads(raw or b'{}').get('id')
    if not container:raise HTTPException(502,'Instagram did not return a media container.')
    note(f'Uploading {size/1048576:.0f} MB to Instagram')
    # Content-Type is left at urllib's default, which is what curl --data-binary sends in Meta's own example.
    http(f'{RUPLOAD}/{container}',mp4.read_bytes(),
         {'Authorization':'OAuth '+cfg['ig_token'],'offset':'0','file_size':str(size)},'POST',1800)
    note('Instagram is processing the reel')
    deadline=time.monotonic()+300
    while True:
        _,_,raw=http(f'{GRAPH}/{container}?'+urllib.parse.urlencode({'fields':'status_code,status','access_token':cfg['ig_token']}),timeout=30)
        status=json.loads(raw or b'{}')
        if status.get('status_code')=='FINISHED':break
        if status.get('status_code')=='ERROR':raise HTTPException(502,'Instagram could not process the reel: '+scrub(status.get('status') or 'no reason given'))
        if time.monotonic()>deadline:
            raise HTTPException(504,'Instagram is still processing after 5 minutes. Check the app before retrying, the reel may yet appear.')
        time.sleep(5)
    _,_,raw=http(f"{GRAPH}/{urllib.parse.quote(cfg['ig_user'])}/media_publish",
        urllib.parse.urlencode({'creation_id':container,'access_token':cfg['ig_token']}).encode(),
        {'Content-Type':'application/x-www-form-urlencoded'},'POST',120)
    media=json.loads(raw or b'{}').get('id')
    if not media:raise HTTPException(502,'Instagram processed the reel but did not publish it.')
    link=''
    try:
        _,_,raw=http(f'{GRAPH}/{media}?'+urllib.parse.urlencode({'fields':'permalink','access_token':cfg['ig_token']}),timeout=30)
        link=json.loads(raw or b'{}').get('permalink') or ''
    except HTTPException:pass      # published already; a missing permalink is not worth failing over
    return {'platform':'instagram','id':media,'url':link}

UPLOADERS={'youtube':youtube_upload,'instagram':instagram_upload}

# --------------------------------------------------------------------- ledger

def ledger(data):
    return read_json(Path(data)/'posted.json')

def remember(data,file,result):
    """Record what actually went out. This is what stops a retry after a half-failed post from
    publishing a second copy to the platform that already succeeded."""
    book=ledger(data);book.setdefault(file,{})[result['platform']]=dict(result,at=time.time())
    (Path(data)/'posted.json').write_text(json.dumps(book,indent=1),encoding='utf-8')

def post(data,file,fields,platforms,note=lambda message:None,repost=False):
    """Publish one clip to each chosen platform.

    Every requested platform ends up in exactly one of posted / failed / skipped, so a platform
    can never drop out quietly: one failing upload neither stops the other nor is reported as
    success, and one that already went out is skipped rather than duplicated."""
    mp4=clip_file(data,file);already={} if repost else ledger(data).get(file,{})
    posted,failed,skipped=[],[],[]
    for platform in platforms:
        if platform in already:
            skipped.append({'platform':platform,'reason':'already posted','url':already[platform].get('url')});continue
        try:
            validate(platform,fields,mp4)
            note('Posting to '+platform)
            result=UPLOADERS[platform](data,mp4,fields,note)
            remember(data,file,result);posted.append(result)
        except Exception as e:
            failed.append({'platform':platform,'error':scrub(getattr(e,'detail',e))[:500]})
    return {'requested':list(platforms),'posted':posted,'failed':failed,'skipped':skipped}

# ------------------------------------------------------------------ endpoints

def attach(app,data,state):
    data=Path(data)

    def redirect_uri():
        return 'http://127.0.0.1:'+str(os.environ.get('LOSTSOULCLIPS_PORT','8770'))+'/social/youtube/callback'

    @app.get('/social/accounts')
    def accounts():
        return {**connected(data),'redirect_uri':redirect_uri(),'limits':LIMITS}

    @app.post('/social/credentials')
    def credentials(b:dict):
        """Store the ids you made in the Google and Meta dashboards, into data/social.json
        (gitignored, chmod 600). Values are never echoed back."""
        fields={}
        for key,_ in CREDENTIALS:
            if key in b:
                value=str(b.get(key) or '').strip()
                if len(value)>700:raise HTTPException(400,key+' is longer than any real credential.')
                fields[key]=value
        if not fields:raise HTTPException(400,'Nothing to save.')
        save_config(data,**fields);return accounts()

    @app.get('/social/youtube/connect')
    def youtube_connect():
        return {'url':yt_consent(config(data),redirect_uri()),'redirect_uri':redirect_uri()}

    @app.get('/social/youtube/callback')
    def youtube_callback(code:str='',error:str=''):
        if error or not code:raise HTTPException(400,'YouTube did not grant access: '+(error or 'no code came back'))
        token=yt_token(config(data),grant_type='authorization_code',code=code,redirect_uri=redirect_uri())
        if not token.get('refresh_token'):
            raise HTTPException(502,'Google returned no refresh token. Remove LostSoulClips at myaccount.google.com/permissions and connect again.')
        save_config(data,yt_refresh_token=token['refresh_token'])
        return HTMLResponse('<h2>YouTube connected.</h2><p>Close this tab and go back to LostSoulClips.</p>')

    @app.get('/social/draft/{file}')
    def clip_draft(file:str):
        """Prefilled post text. Reads only; nothing is sent anywhere."""
        mp4=clip_file(data,file);record=read_json(data/(file+'.json'))
        return {**draft(record,data),'duration':(record.get('result') or {}).get('meta',{}).get('duration'),
                'size_mb':round(mp4.stat().st_size/1048576,1),'posted':ledger(data).get(file,{}),
                'accounts':{**connected(data),'redirect_uri':redirect_uri()},'limits':LIMITS}

    @app.post('/social/write')
    def write_copy(b:dict):
        """Write title/description/tags from the clip's own frames, with the local model.

        Runs as a background job because on a CPU-only box three frame descriptions plus the text
        call take a minute or more; blocking the panel for that would be worse than useless."""
        import viral
        file=str(b.get('file',''));clip_file(data,file)
        if not viral.available():
            raise HTTPException(409,'No local gemma4 model found in Ollama. Start Ollama and pull a gemma4 vision model.')
        record=read_json(data/(file+'.json'));settings=record.get('settings') or {}
        source=settings.get('id');start=settings.get('start');end=settings.get('end')
        if not (isinstance(source,str) and source.isalnum() and (data/(source+'.mp4')).exists()):
            # the source was deleted; the rendered clip itself is the only footage left
            source=file;start=0;end=(record.get('result') or {}).get('meta',{}).get('duration')
        try:start=float(start or 0);end=float(end or 0)
        except (TypeError,ValueError):start,end=0.,0.
        if end<=start:raise HTTPException(400,'This clip has no usable time range to look at.')
        spoken=' '.join(w.get('text','') for w in (settings.get('words') or []) if isinstance(w,dict))
        job=uuid.uuid4().hex;state[job]={'status':'running','message':'Starting'}
        def work():
            try:
                out=viral.write(data,file,data/(source+'.mp4'),start,end-start,spoken,
                                lambda message:state[job].update(message=message))
                fresh=read_json(data/(file+'.json'))
                fresh.setdefault('settings',{})['viral']={k:out[k] for k in ('title','description','tags','model')}
                (data/(file+'.json')).write_text(json.dumps(fresh),encoding='utf-8')
                state[job]={'status':'done','file':file,**out}
            except Exception as e:
                state[job]={'status':'error','error':scrub(getattr(e,'detail',e))[:500]}
        threading.Thread(target=work,daemon=True).start();return {'job':job}

    def post_job(b:dict):
        """Upload on a background thread, reporting into `state` the way the render jobs do, so
        /status and the processing queue in jobs.py both work without a second mechanism."""
        file=str(b.get('file',''));mp4=clip_file(data,file)
        platforms=[p for p in (b.get('platforms') or []) if p in UPLOADERS]
        if not platforms:raise HTTPException(400,'Choose YouTube, Instagram, or both.')
        fields=fields_for(data,file,b);repost=bool(b.get('repost'))
        ready=connected(data)
        for platform in platforms:
            if not ready[platform]['ready']:raise HTTPException(400,f'Connect {platform} first.')
            validate(platform,fields,mp4)      # refuse bad text before a thread or a byte goes out
        job=uuid.uuid4().hex;state[job]={'status':'running','message':'Starting upload'}
        def work():
            try:
                out=post(data,file,fields,platforms,lambda message:state[job].update(message=message),repost)
                state[job]={'status':'error' if out['failed'] else 'done','file':file,**out,
                            'error':'; '.join(f['platform']+': '+f['error'] for f in out['failed']) or None}
            except Exception as e:
                state[job]={'status':'error','error':scrub(getattr(e,'detail',e))[:500]}
        threading.Thread(target=work,daemon=True).start();return {'job':job}

    app.post('/social/post')(post_job)
    return post_job
