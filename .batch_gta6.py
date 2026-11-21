"""3 gameplay-first Shorts each from two popular GTA 6 Twitch creators.

layout=pip_tr: gameplay fills the full 1080x1920, the facecam rides in a small
344x308 box top-right. Less face, more gameplay.

cam rects are measured by hand from a real frame, NOT cam_auto: the auto detector
missed both (jasontheween's box it never found, and these facecams are borderless
overlays rather than bordered rectangles). Passing the rect explicitly is the only
way to guarantee the right crop.
"""
import json, time, urllib.request

B = 'http://127.0.0.1:8770'
FULL_FRAME = {'x': 0, 'y': 0, 'w': 1, 'h': 1}
JOBS = [
    ('quackity', 'https://www.twitch.tv/videos/2932346022',
     {'x': 0.030, 'y': 0.385, 'w': 0.165, 'h': 0.270}),   # 76m, 357k views, cam left
    ('jasontheween', 'https://www.twitch.tv/videos/2934613923',
     {'x': 0.808, 'y': 0.394, 'w': 0.145, 'h': 0.253}),   # 152m, cam right
]


def post(path, obj):
    req = urllib.request.Request(B + path, json.dumps(obj).encode(), {'Content-Type': 'application/json'})
    return json.loads(urllib.request.urlopen(req, timeout=180).read())


def log(*a):
    print(*a, flush=True)


results = {}
for who, url, cam in JOBS:
    log(f'\n===== {who} :: {url} =====')
    job = post('/make', {
        'url': url, 'count': 3, 'min_len': 15, 'max_len': 45,
        'layout': 'pip_tr',
        'cam': cam, 'game': FULL_FRAME,      # explicit, no cam_auto
        'caption_style': 'gta', 'mode': 'fast', 'language': '',
        'aspect': 'vertical', 'resolution': 1080,
    })['job']
    log(f'job {job}')
    last, t0 = None, time.time()
    while time.time() - t0 < 3000:
        s = json.loads(urllib.request.urlopen(B + '/make/' + job, timeout=60).read())
        stage = (s.get('stage') or s.get('message') or '')[:90]
        if stage != last:
            log(f'  [{int(time.time()-t0):>4}s] {s.get("status"):8} {stage}')
            last = stage
        if s.get('status') not in ('running', 'queued'):
            break
        time.sleep(3)
    results[who] = s
    log(f'  -> {s.get("status")} in {int(time.time()-t0)}s')
    if s.get('status') == 'error':
        log('  ERROR:', s.get('error'))
    for c in s.get('clips', []):
        log(f'  clip {c.get("file")}  {c.get("start")}-{c.get("end")}  {c.get("title")}')

json.dump({k: {'status': v.get('status'), 'clips': v.get('clips', []), 'error': v.get('error')}
           for k, v in results.items()}, open('.batch_gta6.json', 'w'), indent=2, default=str)
log('\nALL DONE')
