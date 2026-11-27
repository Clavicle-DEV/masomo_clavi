/* Mentis team calls: real ringing on every page for admins and co-admins.
 *
 * - Polls /api/team/call and broadcasts 'mentis:call-state' to the page.
 * - Plays a looping phone ring + vibration while someone is calling you.
 * - Plays a ringback tone for the caller while the other person's phone rings.
 * - Shows its own "incoming call" overlay so it works outside /team-chat too.
 */
(function () {
  if (window.MentisCalls) return;

  var POLL_VISIBLE_MS = 2000;
  var POLL_HIDDEN_MS = 4000;
  var state = { call: null, incoming: null, outgoing: [] };
  var inCallId = null;
  var onTeamChat = location.pathname.indexOf('/team-chat') === 0;
  var lastIncomingId = null;

  /* ---------- sound ---------- */
  var ctx = null;
  var tone = null;          // 'incoming' | 'outgoing' | null
  var toneTimer = null;
  var vibeTimer = null;

  function getCtx() {
    if (!ctx) {
      var AC = window.AudioContext || window.webkitAudioContext;
      if (!AC) return null;
      try { ctx = new AC(); } catch (e) { return null; }
    }
    return ctx;
  }

  function unlockAudio() {
    var c = getCtx();
    if (c && c.state === 'suspended') c.resume().catch(function () {});
    hideSoundHint();
  }
  ['pointerdown', 'keydown', 'touchstart', 'click'].forEach(function (evt) {
    window.addEventListener(evt, unlockAudio, { passive: true });
  });

  function burst(freqs, startAt, seconds, volume) {
    var c = getCtx();
    if (!c) return;
    var gain = c.createGain();
    gain.gain.setValueAtTime(0.0001, startAt);
    gain.gain.exponentialRampToValueAtTime(volume, startAt + 0.03);
    gain.gain.setValueAtTime(volume, startAt + seconds - 0.05);
    gain.gain.exponentialRampToValueAtTime(0.0001, startAt + seconds);
    gain.connect(c.destination);
    freqs.forEach(function (f) {
      var osc = c.createOscillator();
      osc.type = 'sine';
      osc.frequency.value = f;
      osc.connect(gain);
      osc.start(startAt);
      osc.stop(startAt + seconds + 0.05);
    });
  }

  // One cycle of the incoming ring: ring-ring ... pause (about 3 seconds)
  function playIncomingCycle() {
    var c = getCtx();
    if (!c) return;
    if (c.state === 'suspended') { showSoundHint(); return; }
    var t = c.currentTime + 0.05;
    burst([880, 1100], t, 0.35, 0.22);
    burst([880, 1100], t + 0.5, 0.35, 0.22);
    burst([880, 1100], t + 1.0, 0.35, 0.22);
    burst([880, 1100], t + 1.5, 0.35, 0.22);
  }

  // Ringback heard by the caller: one long soft tone, long pause (about 5 seconds)
  function playOutgoingCycle() {
    var c = getCtx();
    if (!c || c.state === 'suspended') return;
    burst([440, 480], c.currentTime + 0.05, 2.0, 0.12);
  }

  function vibrateCycle() {
    if (navigator.vibrate) { try { navigator.vibrate([400, 200, 400, 200, 400]); } catch (e) {} }
  }

  function startTone(kind) {
    if (tone === kind) return;
    stopTone();
    tone = kind;
    var every = kind === 'incoming' ? 3000 : 5000;
    var play = kind === 'incoming' ? playIncomingCycle : playOutgoingCycle;
    unlockAudio();
    play();
    toneTimer = setInterval(play, every);
    if (kind === 'incoming') {
      vibrateCycle();
      vibeTimer = setInterval(vibrateCycle, 2500);
    }
  }

  function stopTone() {
    tone = null;
    if (toneTimer) clearInterval(toneTimer);
    if (vibeTimer) clearInterval(vibeTimer);
    toneTimer = vibeTimer = null;
    if (navigator.vibrate) { try { navigator.vibrate(0); } catch (e) {} }
  }

  /* ---------- UI (injected so it works on every page) ---------- */
  var css = document.createElement('style');
  css.textContent =
    '#mentisRing{position:fixed;inset:0;z-index:99999;display:none;align-items:center;justify-content:center;' +
    'background:rgba(8,20,13,.88);backdrop-filter:blur(6px);padding:24px;font-family:inherit}' +
    '#mentisRing.show{display:flex}' +
    '#mentisRing .card{width:100%;max-width:340px;text-align:center;color:#fff}' +
    '#mentisRing .avatar{width:96px;height:96px;border-radius:50%;margin:0 auto 18px;background:#1f7a4a;' +
    'display:flex;align-items:center;justify-content:center;font-size:40px;font-weight:800;position:relative}' +
    '#mentisRing .avatar::before,#mentisRing .avatar::after{content:"";position:absolute;inset:0;border-radius:50%;' +
    'border:2px solid rgba(74,222,128,.7);animation:mentisPulse 1.8s ease-out infinite}' +
    '#mentisRing .avatar::after{animation-delay:.9s}' +
    '@keyframes mentisPulse{from{transform:scale(1);opacity:.9}to{transform:scale(1.9);opacity:0}}' +
    '#mentisRing h2{margin:0 0 4px;font-size:24px}#mentisRing p{margin:0 0 28px;opacity:.8;font-size:15px}' +
    '#mentisRing .row{display:flex;justify-content:center;gap:48px}' +
    '#mentisRing button{border:0;border-radius:50%;width:68px;height:68px;font-size:28px;cursor:pointer;color:#fff}' +
    '#mentisRing .ans{background:#16a34a;animation:mentisShake 1s ease-in-out infinite}' +
    '#mentisRing .dec{background:#dc2626}' +
    '#mentisRing small{display:block;margin-top:8px;font-size:12px;opacity:.85}' +
    '@keyframes mentisShake{0%,100%{transform:rotate(0)}20%{transform:rotate(-12deg)}40%{transform:rotate(12deg)}60%{transform:rotate(-8deg)}80%{transform:rotate(8deg)}}' +
    '#mentisSoundHint{position:fixed;left:50%;bottom:20px;transform:translateX(-50%);z-index:100000;display:none;' +
    'background:#14301f;color:#fff;padding:12px 18px;border-radius:999px;font-size:14px;box-shadow:0 6px 24px rgba(0,0,0,.35);cursor:pointer}' +
    '#mentisToast{position:fixed;top:16px;left:50%;transform:translateX(-50%);z-index:100000;display:none;' +
    'background:#14301f;color:#fff;padding:10px 16px;border-radius:12px;font-size:14px;max-width:90vw}';
  document.head.appendChild(css);

  var overlay = document.createElement('div');
  overlay.id = 'mentisRing';
  overlay.setAttribute('role', 'alertdialog');
  overlay.setAttribute('aria-live', 'assertive');
  overlay.innerHTML =
    '<div class="card"><div class="avatar" id="mentisRingAvatar">?</div>' +
    '<h2 id="mentisRingName">Incoming call</h2><p id="mentisRingKind">Mentis team call</p>' +
    '<div class="row"><div><button class="dec" id="mentisDecline" aria-label="Decline">&#10005;</button><small>Decline</small></div>' +
    '<div><button class="ans" id="mentisAnswer" aria-label="Answer">&#128222;</button><small>Answer</small></div></div></div>';

  var hint = document.createElement('div');
  hint.id = 'mentisSoundHint';
  hint.textContent = '\uD83D\uDD14 Tap anywhere to turn on ringing sound';

  var toast = document.createElement('div');
  toast.id = 'mentisToast';

  function mount() {
    document.body.appendChild(overlay);
    document.body.appendChild(hint);
    document.body.appendChild(toast);
    document.getElementById('mentisAnswer').addEventListener('click', answer);
    document.getElementById('mentisDecline').addEventListener('click', decline);
  }
  if (document.body) mount(); else document.addEventListener('DOMContentLoaded', mount);

  function showSoundHint() { hint.style.display = 'block'; }
  function hideSoundHint() { hint.style.display = 'none'; }

  var toastTimer = null;
  function showToast(text) {
    toast.textContent = text;
    toast.style.display = 'block';
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { toast.style.display = 'none'; }, 5000);
  }

  var titleTimer = null;
  var originalTitle = document.title;
  function flashTitle(on, text) {
    clearInterval(titleTimer);
    if (!on) { document.title = originalTitle; return; }
    var flip = false;
    titleTimer = setInterval(function () {
      flip = !flip;
      document.title = flip ? '\uD83D\uDCDE ' + text : originalTitle;
    }, 900);
  }

  function showIncoming(inc) {
    document.getElementById('mentisRingName').textContent = inc.from;
    document.getElementById('mentisRingAvatar').textContent = (inc.from || '?').charAt(0).toUpperCase();
    document.getElementById('mentisRingKind').textContent = 'Incoming ' + inc.kind + ' call \u00B7 Mentis team';
    overlay.classList.add('show');
    startTone('incoming');
    flashTitle(true, inc.from + ' is calling');
  }

  function hideIncoming() {
    overlay.classList.remove('show');
    if (tone === 'incoming') stopTone();
    flashTitle(false);
  }

  /* ---------- actions ---------- */
  function post(url, body) {
    return fetch(url, {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body || {})
    });
  }

  function answer() {
    var inc = state.incoming;
    if (!inc) return;
    unlockAudio();
    hideIncoming();
    if (onTeamChat) {
      document.dispatchEvent(new CustomEvent('mentis:answer', { detail: inc }));
    } else {
      location.href = '/team-chat?answer=' + inc.inviteId;
    }
  }

  function decline() {
    var inc = state.incoming;
    hideIncoming();
    if (!inc) return;
    state.incoming = null;
    post('/api/team/call/respond', { inviteId: inc.inviteId, action: 'decline' }).catch(function () {});
  }

  /* ---------- polling ---------- */
  var polling = false;
  var timer = null;

  function schedule() {
    clearTimeout(timer);
    timer = setTimeout(poll, document.hidden ? POLL_HIDDEN_MS : POLL_VISIBLE_MS);
  }

  function poll() {
    if (polling) return;
    polling = true;
    var url = '/api/team/call' + (inCallId ? '?inCall=' + encodeURIComponent(inCallId) : '');
    fetch(url, { credentials: 'same-origin', cache: 'no-store' })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (data) { if (data) apply(data); })
      .catch(function () {})
      .then(function () { polling = false; schedule(); });
  }

  function apply(data) {
    var prevIncoming = state.incoming;
    state = { call: data.call, incoming: data.incoming, outgoing: data.outgoing || [] };

    // Incoming ring
    if (state.incoming && !state.call) {
      if (!prevIncoming || prevIncoming.inviteId !== state.incoming.inviteId) showIncoming(state.incoming);
      lastIncomingId = state.incoming.inviteId;
    } else if (prevIncoming) {
      var wasAnsweredHere = state.call && state.call.id === prevIncoming.callId;
      hideIncoming();
      if (!wasAnsweredHere && lastIncomingId === prevIncoming.inviteId) {
        showToast('Missed call from ' + prevIncoming.from);
        lastIncomingId = null;
      }
    }

    // Ringback for the caller while the other phone is ringing
    var someoneRinging = state.outgoing.some(function (o) { return o.status === 'ringing'; });
    if (someoneRinging && !state.incoming) startTone('outgoing');
    else if (tone === 'outgoing') stopTone();

    document.dispatchEvent(new CustomEvent('mentis:call-state', { detail: state }));
  }

  document.addEventListener('visibilitychange', function () { if (!document.hidden) poll(); });
  window.addEventListener('online', poll);

  window.MentisCalls = {
    getState: function () { return state; },
    setInCall: function (id) { inCallId = id || null; if (id) poll(); },
    refresh: poll,
    post: post,
    unlockAudio: unlockAudio,
    toast: showToast,
    stopTone: stopTone
  };

  poll();
})();
