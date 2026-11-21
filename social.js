// LostSoulClips social publishing: a Post panel on every clip in My clips.
// Loaded after app.js. It only wraps refreshLibrary(); app.js stays as it is.
//
// Nothing is uploaded by opening the panel. /social/draft only reads the clip's own metadata;
// the one call that publishes is the Post button, after you have read the text.
(function () {
  const PLATFORMS = [['youtube', 'YouTube'], ['instagram', 'Instagram']];

  function el(tag, props, kids) {
    const node = document.createElement(tag);
    Object.assign(node, props || {});
    for (const kid of kids || []) node.append(kid);
    return node;
  }
  function field(label, input) {
    return el('label', { className: 'social-field' }, [el('span', { textContent: label }), input]);
  }
  const clipId = card => (card.querySelector('video')?.src || '').split('/media/')[1] || '';

  // One-time setup. The ids come from the Google and Meta dashboards; they are stored in
  // data/social.json on this machine and are never sent anywhere except to those two APIs.
  function setupPanel(accounts, reload) {
    const box = el('details', { className: 'social-setup' }, [el('summary', { textContent: 'Connect accounts' })]);
    const inputs = {
      yt_client_id: el('input', { type: 'text', placeholder: 'Google OAuth client id' }),
      yt_client_secret: el('input', { type: 'password', placeholder: 'Google OAuth client secret' }),
      ig_token: el('input', { type: 'password', placeholder: 'Instagram long-lived access token' }),
      ig_user: el('input', { type: 'text', placeholder: 'Instagram account id (numeric)' }),
    };
    const out = el('p', { className: 'muted' });
    const save = el('button', { textContent: 'Save credentials' });
    const connect = el('button', { textContent: 'Connect YouTube' });
    save.onclick = async () => {
      const body = {};
      for (const key in inputs) if (inputs[key].value.trim()) body[key] = inputs[key].value.trim();
      if (!Object.keys(body).length) { out.textContent = 'Fill in at least one field first.'; return; }
      save.disabled = true;
      try { await request('/social/credentials', body); for (const key in inputs) inputs[key].value = ''; out.textContent = 'Saved. Now press Connect YouTube, or reopen this panel for Instagram.'; await reload(); }
      catch (e) { out.textContent = 'Could not save: ' + e.message; }
      finally { save.disabled = false; }
    };
    connect.onclick = async () => {
      try { const r = await request('/social/youtube/connect'); window.open(r.url, '_blank', 'noopener'); out.textContent = 'Approve access in the Google tab, then press Refresh library.'; }
      catch (e) { out.textContent = e.message; }
    };
    box.append(
      el('p', { className: 'muted', textContent: 'YouTube: create a Google Cloud project, enable YouTube Data API v3, create an OAuth client, and register this exact redirect URI — ' + accounts.redirect_uri + ' — then save the id and secret below and press Connect. Instagram: a Business or Creator account plus a Meta app token and account id.' }),
      field('YouTube client id', inputs.yt_client_id), field('YouTube client secret', inputs.yt_client_secret),
      field('Instagram token', inputs.ig_token), field('Instagram account id', inputs.ig_user),
      el('div', { className: 'card-links' }, [save, connect]), out);
    return box;
  }

  function postPanel(clipKey, title) {
    const panel = el('div', { className: 'social-panel' });
    const out = el('p', { className: 'muted', textContent: 'Loading the draft…' });
    panel.append(out);

    const load = async () => {
      const draft = await request('/social/draft/' + clipKey);
      panel.replaceChildren();
      const accounts = draft.accounts;
      const inputs = {
        title: el('input', { type: 'text', value: draft.title, maxLength: draft.limits.youtube.title }),
        description: el('textarea', { rows: 4, value: draft.description }),
        tags: el('input', { type: 'text', value: draft.tags.join(', ') }),
        caption: el('textarea', { rows: 4, value: draft.caption }),
      };
      const privacy = el('select', {}, ['private', 'unlisted', 'public'].map(v =>
        el('option', { value: v, textContent: 'YouTube: ' + v, selected: v === draft.privacy })));

      // A platform you have not connected is shown disabled with the reason, rather than hidden.
      const boxes = PLATFORMS.map(([key, label]) => {
        const ready = accounts[key].ready, done = draft.posted[key];
        const input = el('input', { type: 'checkbox', checked: ready && !done, disabled: !ready || !!done });
        const note = done ? ' — already posted' : (ready ? '' : ' — not connected');
        return { key, input, row: el('label', { className: 'social-platform' }, [input, el('span', { textContent: label + note })]) };
      });

      const result = el('p', { className: 'muted' });
      const post = el('button', { textContent: 'Post now' });
      const repost = el('input', { type: 'checkbox' });

      // Rewrites the fields from what the local model sees in the clip's own frames. Explicit
      // rather than automatic: on a CPU box this takes a minute, and it overwrites your edits.
      const write = el('button', { textContent: '✨ Write from the clip' });
      write.onclick = async () => {
        if (inputs.title.value.trim() && !confirm('Replace the title, description and tags with copy written from this clip’s frames?')) return;
        write.disabled = true; result.className = 'muted'; result.textContent = 'Looking at the clip…';
        try {
          const { job } = await request('/social/write', { file: clipKey });
          for (;;) {
            const s = await request('/status/' + job);
            result.textContent = s.message || s.status;
            if (s.status === 'error') throw Error(s.error);
            if (s.status !== 'running') break;
            await sleep(2000);
          }
          await load();
        } catch (e) { result.className = 'social-result bad'; result.textContent = e.message; write.disabled = false; }
      };

      post.onclick = async () => {
        const platforms = boxes.filter(b => b.input.checked).map(b => b.key);
        if (!platforms.length) { result.textContent = 'Tick at least one connected platform.'; return; }
        if (!confirm('Post “' + inputs.title.value + '” to ' + platforms.join(' and ') + '?\n\nYouTube privacy: ' + privacy.value + '\n\nThis publishes to your real accounts and cannot be undone from here.')) return;
        post.disabled = true; result.textContent = 'Starting…';
        try {
          const { job } = await request('/social/post', {
            file: clipKey, platforms, repost: repost.checked, privacy: privacy.value,
            title: inputs.title.value, description: inputs.description.value,
            tags: inputs.tags.value, caption: inputs.caption.value,
          });
          for (;;) {
            const s = await request('/status/' + job);
            result.textContent = s.message || s.status;
            if (s.status !== 'running') { report(result, s); break; }
            await sleep(1500);
          }
          await load();
        } catch (e) { result.textContent = 'Could not post: ' + e.message; }
        finally { post.disabled = false; }
      };

      panel.append(
        field('Title (YouTube)', inputs.title), field('Description (YouTube)', inputs.description),
        field('Tags, comma separated (YouTube)', inputs.tags), field('Caption (Instagram)', inputs.caption),
        el('div', { className: 'card-links' }, [privacy]),
        ...boxes.map(b => b.row),
        el('label', { className: 'social-platform' }, [repost, el('span', { textContent: 'Post again even if already posted' })]),
        el('p', { className: 'muted', textContent: draft.size_mb + ' MB · ' + (draft.duration || 0).toFixed(1) + ' s'
            + (draft.written_by ? ' · copy written by ' + draft.written_by : ' · copy not written yet') }),
        el('div', { className: 'card-links' }, [write, post]), result,
        setupPanel(accounts, load));
      if (!accounts.youtube.ready && !accounts.instagram.ready)
        panel.prepend(el('p', { className: 'muted', textContent: 'No account connected yet — open “Connect accounts” below. Posting stays disabled until then.' }));
    };

    // Every outcome is shown, including the half-successful one: a platform that failed is
    // named with its error instead of being folded into a single "done".
    function report(node, s) {
      const lines = [];
      for (const p of s.posted || []) {
        lines.push('✓ ' + p.platform + ': ' + (p.url || p.id));
        if (p.privacy_warning) lines.push('  ⚠ ' + p.privacy_warning);
      }
      for (const p of s.skipped || []) lines.push('• ' + p.platform + ' skipped (' + p.reason + ')');
      for (const p of s.failed || []) lines.push('✗ ' + p.platform + ': ' + p.error);
      if (!lines.length) lines.push(s.error || s.status);
      node.textContent = lines.join('\n');
      node.className = (s.failed || []).length ? 'social-result bad' : 'social-result';
      if (s.posted?.length) status('Posted “' + title + '” to ' + s.posted.map(p => p.platform).join(', ') + '.');
    }

    load().catch(e => { panel.replaceChildren(el('p', { className: 'muted', textContent: 'Could not load the draft: ' + e.message })); });
    return panel;
  }

  function decorate() {
    for (const card of document.querySelectorAll('#library .clip-card')) {
      if (card.dataset.socialReady) continue;
      const key = clipId(card), links = card.querySelector('.card-links');
      if (!key || !links) continue;
      card.dataset.socialReady = '1';
      const button = el('button', { textContent: '↗ Post' });
      button.onclick = () => {
        const open = card.querySelector('.social-panel');
        if (open) { open.remove(); button.textContent = '↗ Post'; return; }
        button.textContent = '↗ Close';
        card.append(postPanel(key, card.querySelector('h3')?.textContent || 'clip'));
      };
      links.append(button);
    }
  }

  const baseRefresh = window.refreshLibrary;
  window.refreshLibrary = async function () {
    const r = await baseRefresh.apply(this, arguments);
    decorate();
    return r;
  };
})();
