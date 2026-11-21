"""Write the title, description and tags for a clip from what is actually in it.

Why this exists: the auto titles are "Live clip" and "Activity highlight 4", and the tags built
from them are noise. The useful signal is on screen, not in the audio - these clips' transcripts
are incidental stream chatter ("maybe we go to the shower") while the posts that work describe
the picture ("GTA 6 bar lighting is actually unreal"). So the frames are described first, and
the transcript is only supporting evidence.

Runs on the same local Ollama that vision.py already uses: no API key, no hosted provider, and
it reuses vision.review() for frame extraction, description and caching rather than repeating it.
Everything degrades to the plain template if Ollama or the model is missing - a missing model
must never break the Post panel.
"""
import json
import re
import urllib.request

OLLAMA='http://127.0.0.1:11434'
# Six real posts from the channel this writes for. Few-shot beats any amount of adjectives in
# an instruction: the model copies the shape, the emoji habit and the second-person voice.
EXAMPLES=[
 ('GTA 6 NPC crowds are insane 👀',
  'Vice City actually feels alive. Look how many NPCs are on screen 👀 GTA 6 NPC density in Vice City.'),
 ('Vice City at night is unreal 🌴',
  'No chase, no cops, just the city at night 🌴 GTA 6 Vice City ambience.'),
 ('GTA 6 bar lighting is actually unreal 🔥',
  'Real-time, not a render. The lighting in GTA 6 is unreal 🔥 GTA 6 graphics showcase.'),
 ('This GTA 6 beach drive ends badly 💀',
  "It was going so well 🌴 and then it really wasn't 💀 Beach driving in GTA 6 Vice City."),
 ('This GTA 6 night ride went very wrong 💀',
  "Everything was fine until it really, really wasn't 💀 Night driving in GTA 6 Vice City."),
 ('He never saw it coming 💀',
  'Driving along completely fine... and then this happens 💀 GTA 6 gameplay in Vice City.'),
]
INSTRUCTION="""You write YouTube Shorts copy for a GTA 6 gameplay channel.

Here is the channel's own voice. Copy this shape exactly:
{examples}

You are given descriptions of three frames from one clip, in order, and optionally what was
said. Write copy for THAT clip only.

Hard rules:
- Describe only what the frame descriptions support. Invent no crash, chase, death or outcome
  that is not described. If the frames are dull, write something true and dull rather than a lie.
- The spoken words are background chatter from a stream. Use them only if they clearly match
  the picture. Usually ignore them.
- title: under 70 characters, one emoji at most, no hashtags.
- description: one or two short sentences in the voice above, then a plain keyword phrase.
- tags: 6 to 10 lowercase single words, no '#', no spaces.

Return only JSON: {{"title": "...", "description": "...", "tags": ["...", "..."]}}"""

def model(kind='vision'):
    """The local model to use. vision.py already fixed the family: a gemma4 tag that reports both
    vision and completion. Text-only generation accepts any gemma4 with completion."""
    try:
        rows=json.load(urllib.request.urlopen(OLLAMA+'/api/tags',timeout=3))['models']
    except Exception:return None
    for row in rows:
        if not row['name'].startswith('gemma4:'):continue
        caps=row.get('capabilities')
        if caps is None:
            try:
                request=urllib.request.Request(OLLAMA+'/api/show',json.dumps({'model':row['name']}).encode(),{'Content-Type':'application/json'})
                caps=json.load(urllib.request.urlopen(request,timeout=5)).get('capabilities',[])
            except Exception:continue
        if 'completion' in caps and (kind!='vision' or 'vision' in caps):return row['name']
    return None

def available():
    return bool(model('text'))

def ask(name,prompt,budget=320):
    import smart
    payload=smart._payload(name,prompt,budget,.7)      # warmer than the scoring calls: this is copywriting
    request=urllib.request.Request(OLLAMA+'/api/generate',json.dumps(payload).encode(),{'Content-Type':'application/json'})
    return json.load(urllib.request.urlopen(request,timeout=300)).get('response','')

def clean(value,limit):
    return re.sub(r'\s+',' ',str(value or '')).strip().strip('"')[:limit]

def copy_for(frames,spoken='',name=None):
    """frames = the visual descriptions, in order. Returns {title, description, tags} or None."""
    name=name or model('text')
    seen=[(i,f) for i,f in enumerate(frames) if str(f).strip()]
    if not name or not seen:return None       # nothing was seen, so any title would be invention
    shown='\n'.join(f'Frame {i+1}: {f}' for i,f in seen)
    examples='\n'.join(f'- "{t}" / "{d}"' for t,d in EXAMPLES)
    prompt=INSTRUCTION.format(examples=examples)+'\n\nFRAME DESCRIPTIONS:\n'+shown
    if spoken.strip():prompt+='\n\nSPOKEN (usually background chatter, ignore unless it matches):\n'+spoken.strip()[:600]
    try:out=json.loads(ask(name,prompt) or '{}')
    except ValueError:return None
    if not isinstance(out,dict):return None
    title=clean(out.get('title'),100)
    if not title:return None
    import social      # one tag cleaner for the whole project, so a tag the model invents is
    tags=[]            # filtered exactly like a tag built from the clip's own metadata
    for raw in (out.get('tags') or []):
        tag=social.clean_tag(raw if isinstance(raw,str) else '')
        if len(tag)>2 and tag not in tags:tags.append(tag)
    return {'title':title,'description':clean(out.get('description'),4000),'tags':tags[:12],'model':name}

def write(data,file,source,start,duration,spoken='',report=lambda message:None):
    """Describe the clip's own frames, then write copy from those descriptions.

    vision.review does the frame extraction, the per-frame model call and the on-disk cache, so a
    second run over the same clip costs one text call instead of three vision calls."""
    import vision
    name=model('vision')
    if not name:raise ValueError('No local gemma4 vision model in Ollama. Install one to write copy from the picture.')
    report('Looking at the clip')
    review=vision.review(source,start,duration,data,report)
    frames=[f.get('description','') for f in review.get('frames',[])]
    report('Writing the copy')
    out=copy_for(frames,spoken,model('text') or name)
    if not out:raise ValueError('The local model did not return usable copy. Try again.')
    out['frames']=frames
    return out
