/* Clavis app permissions screen.
 * A web app (PWA) cannot declare permissions in a manifest. The phone only lists
 * Microphone / Camera / Notifications under Clavis in Settings AFTER the app has
 * asked for them. This screen asks for each one on a tap, shows the current
 * status, and explains how to fix a blocked one.
 */
(function () {
  if (window.ClavisPermissions) return;

  var ITEMS = [
    { key: 'microphone', icon: '\uD83C\uDFA4', title: 'Microphone', why: 'Voice calls, live lessons and speaking practice.' },
    { key: 'camera', icon: '\uD83D\uDCF7', title: 'Camera', why: 'Video calls, live lessons and scanning homework.' },
    { key: 'notifications', icon: '\uD83D\uDD14', title: 'Notifications', why: 'Call ringing, reminders and study streaks.' },
    { key: 'storage', icon: '\uD83D\uDCBE', title: 'Offline storage', why: 'Keeps your lessons and progress from being cleared.' },
    { key: 'files', icon: '\uD83D\uDCC1', title: 'Files and photos', why: 'Upload homework, images and documents.' }
  ];

  var css = document.createElement('style');
  css.textContent =
    '#cpOverlay{position:fixed;inset:0;z-index:99998;display:none;align-items:flex-end;justify-content:center;background:rgba(8,20,13,.6);padding:0}' +
    '#cpOverlay.open{display:flex}' +
    '#cpSheet{width:100%;max-width:520px;max-height:90vh;overflow:auto;background:#fffdf8;color:#21281f;border-radius:20px 20px 0 0;padding:20px 18px 26px;font-family:inherit}' +
    '@media(min-width:600px){#cpOverlay{align-items:center}#cpSheet{border-radius:20px}}' +
    '#cpSheet h2{margin:0 0 4px;font-size:20px;color:#1f3b30}#cpSheet .sub{margin:0 0 14px;font-size:13px;color:#6e7a6c}' +
    '.cp-row{display:flex;align-items:center;gap:12px;padding:12px 0;border-top:1px solid #e6e1d2}' +
    '.cp-ico{font-size:24px;width:34px;text-align:center}.cp-txt{flex:1;min-width:0}' +
    '.cp-txt b{display:block;font-size:14px}.cp-txt small{display:block;color:#6e7a6c;font-size:12px;line-height:1.4}' +
    '.cp-help{margin-top:4px;color:#b8433a!important}' +
    '.cp-btn{min-height:38px;padding:0 14px;border:0;border-radius:9px;background:#1f3b30;color:#fff;font-weight:700;font-size:13px;cursor:pointer;font-family:inherit}' +
    '.cp-btn.ok{background:#e4f3ea;color:#1f6b3f;cursor:default}.cp-btn.bad{background:#fbe9e7;color:#b8433a}' +
    '.cp-close{margin-top:14px;width:100%;min-height:44px;border:1px solid #dbd5c5;border-radius:10px;background:#fff;font-weight:700;cursor:pointer;font-family:inherit}';
  document.head.appendChild(css);

  var overlay = document.createElement('div');
  overlay.id = 'cpOverlay';
  overlay.innerHTML = '<div id="cpSheet" role="dialog" aria-modal="true" aria-label="App permissions"><h2>App permissions</h2>' +
    '<p class="sub">Clavis only asks for what a feature needs. You can change these any time.</p><div id="cpList"></div>' +
    '<input type="file" id="cpFile" accept="image/*,application/pdf,.doc,.docx,.txt" hidden>' +
    '<button class="cp-close" id="cpClose" type="button">Done</button></div>';

  function mount() {
    document.body.appendChild(overlay);
    document.getElementById('cpClose').addEventListener('click', close);
    overlay.addEventListener('click', function (e) { if (e.target === overlay) close(); });
  }
  if (document.body) mount(); else document.addEventListener('DOMContentLoaded', mount);

  function platformHelp() {
    var ua = navigator.userAgent;
    if (/iphone|ipad|ipod/i.test(ua)) return 'Open the iPhone Settings app, find Clavis (or Safari, then Camera / Microphone), and switch it to Allow.';
    if (/android/i.test(ua)) return 'Open Android Settings, then Apps, then Clavis, then Permissions, and switch it to Allow. (In Chrome: lock icon in the address bar, then Permissions.)';
    return 'Click the lock icon next to the address bar, set it to Allow, then reload.';
  }

  async function query(name) {
    try {
      if (!navigator.permissions || !navigator.permissions.query) return 'unknown';
      var s = await navigator.permissions.query({ name: name });
      return s.state; // granted | denied | prompt
    } catch (e) { return 'unknown'; }
  }

  async function status(key) {
    if (key === 'microphone') return navigator.mediaDevices ? query('microphone') : 'unsupported';
    if (key === 'camera') return navigator.mediaDevices ? query('camera') : 'unsupported';
    if (key === 'notifications') {
      if (!('Notification' in window)) return 'unsupported';
      return Notification.permission === 'default' ? 'prompt' : Notification.permission;
    }
    if (key === 'storage') {
      if (!(navigator.storage && navigator.storage.persisted)) return 'unsupported';
      return (await navigator.storage.persisted()) ? 'granted' : 'prompt';
    }
    return 'files';
  }

  async function ask(key) {
    try {
      if (key === 'microphone' || key === 'camera') {
        if (!window.isSecureContext || !navigator.mediaDevices) return;
        var s = await navigator.mediaDevices.getUserMedia(key === 'microphone' ? { audio: true } : { video: true });
        s.getTracks().forEach(function (t) { t.stop(); });
      } else if (key === 'notifications') {
        if ('Notification' in window) await Notification.requestPermission();
      } else if (key === 'storage') {
        if (navigator.storage && navigator.storage.persist) await navigator.storage.persist();
      } else if (key === 'files') {
        document.getElementById('cpFile').click();
        return;
      }
    } catch (e) { /* denied or dismissed: status below shows it */ }
  }

  async function render() {
    var list = document.getElementById('cpList');
    list.innerHTML = '';
    for (var i = 0; i < ITEMS.length; i++) {
      var it = ITEMS[i], st = await status(it.key);
      var row = document.createElement('div'); row.className = 'cp-row';
      var txt = '<div class="cp-ico">' + it.icon + '</div><div class="cp-txt"><b>' + it.title + '</b><small>' + it.why + '</small>';
      var btn = document.createElement('button'); btn.type = 'button'; btn.className = 'cp-btn';
      if (st === 'granted') { btn.textContent = 'Allowed'; btn.className += ' ok'; btn.disabled = true; }
      else if (st === 'denied') { btn.textContent = 'Blocked'; btn.className += ' bad'; txt += '<small class="cp-help">' + platformHelp() + '</small>'; btn.addEventListener('click', render); }
      else if (st === 'unsupported') { btn.textContent = 'Not available'; btn.disabled = true; }
      else if (st === 'files') { btn.textContent = 'Choose file'; btn.addEventListener('click', (function (k) { return function () { ask(k); }; })(it.key)); txt += '<small>No permission needed. You pick each file yourself.</small>'; }
      else { btn.textContent = 'Allow'; btn.addEventListener('click', (function (k) { return async function () { await ask(k); render(); }; })(it.key)); }
      row.innerHTML = txt + '</div>';
      row.appendChild(btn);
      list.appendChild(row);
    }
  }

  function open() { overlay.classList.add('open'); render(); }
  function close() { overlay.classList.remove('open'); }

  window.ClavisPermissions = { open: open, close: close, refresh: render };
})();
