  let currentLevel = ACCOUNT_TRACK === 'campus' ? 'campus' : 'secondary';
  let currentGradeForm = ACCOUNT_TRACK === 'campus' ? 'Year 1' : 'Form 1';
  let currentSubject = null;
  let teachState = null; // { topics: [...], topicIndex: 0, pendingTopicStart: bool }
  let teachPickerMode = false;
  let conversationHistory = [];

  /* Permanently remove the level-toggle buttons that don't belong to this
     account's track, so switching between school-grade levels and Campus
     is never possible from within the app — the choice is made once, at
     signup. Uses getElementById directly (not the consts below) so it's
     safe to call before every toggle's const has been declared yet. */
  function pruneLevelTogglesForTrack() {
    ['levelToggle', 'defaultLevelToggle', 'papersLevelToggle'].forEach(id => {
      const el = document.getElementById(id);
      if (!el) return;
      if (ACCOUNT_TRACK === 'campus') {
        [...el.children].forEach(b => { if (b.dataset.level !== 'campus') b.remove(); });
        el.style.display = 'none'; // nothing left to toggle between
      } else {
        const campusBtn = [...el.children].find(b => b.dataset.level === 'campus');
        if (campusBtn) campusBtn.remove();
      }
    });
  }
  pruneLevelTogglesForTrack();

  /* DOM references needed early (before any settings/init logic runs) */
  const levelToggle = document.getElementById('levelToggle');
  const notebook = document.getElementById('notebook');
  const emptyHint = document.getElementById('emptyHint');
  const teachStarter = document.getElementById('teachStarter');
  const teachStarterBtn = document.getElementById('teachStarterBtn');
  const teachBar = document.getElementById('teachBar');
  const teachBarLabel = document.getElementById('teachBarLabel');
  const teachContinueBtn = document.getElementById('teachContinueBtn');
  const teachExitBtn = document.getElementById('teachExitBtn');

  // A subject-appropriate example question shown as a starter hint when a
  // chat is empty — used to be the same "quadratic equation" example for
  // every subject, which looked broken for anything that wasn't Math.
  const SUBJECT_EXAMPLE_QUESTIONS = {
    'Mathematics': 'How do I factorise a quadratic equation?',
    'English': 'What\'s the difference between a metaphor and a simile?',
    'Kiswahili': 'Ni tofauti gani kati ya nomino na kivumishi?',
    'Physics': 'Why does a heavier object not fall faster than a lighter one?',
    'Chemistry': 'How do I balance a chemical equation?',
    'Biology': 'How does photosynthesis actually work?',
    'Agriculture': 'What\'s the difference between organic and inorganic fertiliser?',
    'Home Science': 'What are the food groups and why do we need each one?',
    'Creative Arts': 'What\'s the difference between primary and secondary colours?',
    'Geography': 'What causes rainfall to form?',
    'Religious Education': 'What is the significance of the Ten Commandments?',
    'Business Studies': 'What\'s the difference between a debtor and a creditor?',
    'History': 'What were the main causes of the First World War?',
    'Computer Studies': 'What\'s the difference between hardware and software?',
    'Science and Technology': 'How do plants make their own food?',
    'Social Studies': 'What are the three levels of government in Kenya?',
    'Pre-Technical Studies': 'What\'s the difference between a bolt and a screw?',
    'ICT': 'What\'s the difference between RAM and storage?',
    'Statistics': 'What\'s the difference between mean, median and mode?',
    'Accounting': 'What\'s the accounting equation, and why does it always balance?',
    'Economics': 'What\'s the difference between demand and quantity demanded?',
    'Marketing': 'What are the 4 Ps of marketing?',
    'Finance': 'What\'s the difference between simple and compound interest?',
    'Human Resource Management': 'What\'s the difference between recruitment and selection?',
    'Public Administration': 'What\'s the difference between centralisation and devolution?',
    'Law': 'What\'s the difference between civil law and criminal law?',
    'Computer Science': 'What\'s the difference between a stack and a queue?',
    'Psychology': 'What\'s the difference between classical and operant conditioning?',
    'Sociology': 'What\'s the difference between a norm and a value?',
    'Political Science': 'What\'s the difference between a unitary and a federal system?',
    'Communication and Media Studies': 'What makes a news story newsworthy?',
    'Education': 'What\'s the difference between formative and summative assessment?',
    'Literature': 'How do I identify the theme of a novel?',
    'Philosophy': 'What\'s the difference between deductive and inductive reasoning?',
  };
  function exampleQuestionFor(subject) {
    return SUBJECT_EXAMPLE_QUESTIONS[subject] || 'What would you like help understanding?';
  }

  const questionInput = document.getElementById('questionInput');
  const sendBtn = document.getElementById('sendBtn');
  const typingIndicator = document.getElementById('typingIndicator');
  const typingLabel = document.getElementById('typingLabel');
  const chatSubjectLabel = document.getElementById('chatSubjectLabel');
  const chatLevelLabel = document.getElementById('chatLevelLabel');
  const backBtn = document.getElementById('backBtn');
  const gradeFormSelect = document.getElementById('gradeFormSelect');
  const subjectCount = document.getElementById('subjectCount');
  const subjectsTitle = document.getElementById('subjectsTitle');

  const SUBJECTS_BY_LEVEL = {
    cbc: ['Mathematics', 'English', 'Kiswahili', 'Science and Technology', 'Social Studies', 'Creative Arts'],
    upper_primary: ['Mathematics', 'English', 'Kiswahili', 'Science and Technology', 'Social Studies', 'Agriculture', 'Creative Arts'],
    junior_school: ['Mathematics', 'English', 'Kiswahili', 'Science and Technology', 'Social Studies', 'Pre-Technical Studies', 'Agriculture', 'Creative Arts', 'ICT', 'Business Studies'],
    secondary: ['Mathematics', 'English', 'Physics', 'Chemistry', 'Agriculture', 'Home Science', 'Creative Arts', 'Biology', 'Kiswahili', 'Geography', 'Religious Education', 'Business Studies', 'History', 'Computer Studies'],
    senior_school: ['Mathematics', 'English', 'Physics', 'Chemistry', 'Agriculture', 'Home Science', 'Creative Arts', 'Biology', 'Kiswahili', 'Geography', 'Religious Education', 'Business Studies', 'History', 'Computer Studies'],
    campus: ['Mathematics', 'Statistics', 'Accounting', 'Economics', 'Business Studies', 'Marketing', 'Finance', 'Human Resource Management', 'Public Administration', 'Law', 'Computer Science', 'Psychology', 'Sociology', 'Political Science', 'Communication and Media Studies', 'Education', 'History', 'Literature', 'Philosophy', 'Kiswahili']
  };

  function levelDisplayName(level) {
    return ({ cbc: 'CBC', upper_primary: 'Upper Primary', junior_school: 'Junior School', secondary: 'Secondary (KCSE)', senior_school: 'Senior School', campus: 'Campus' })[level] || 'Your level';
  }

  function getVisibleSubjects() {
    return SUBJECTS_BY_LEVEL[currentLevel] || [];
  }

  function filterSubjectsForLevel() {
    const allowed = new Set(getVisibleSubjects());
    document.querySelectorAll('.subject-card').forEach(card => {
      card.classList.toggle('level-hidden', !allowed.has(card.dataset.subject));
    });
    document.querySelector('.subjects').classList.toggle('campus-mode', currentLevel === 'campus');
    subjectsTitle.textContent = `${levelDisplayName(currentLevel)} subjects`;
    subjectCount.textContent = `${allowed.size} subjects available`;
  }

  /* ---------------- Navigation ---------------- */
  const mainNav = document.getElementById('mainNav');
  const screens = document.querySelectorAll('.screen');

  function showScreen(id) {
    stopSpeaking();
    screens.forEach(s => s.classList.toggle('active', s.id === id));
    [...mainNav.children].forEach(b => b.classList.toggle('active', b.dataset.screen === id));
    if (id === 'dashboardScreen') renderDashboard();
    if (id === 'leaderboardScreen') renderLeaderboard();
  }

  mainNav.addEventListener('click', (e) => {
    const isDesktop = window.matchMedia('(min-width: 721px)').matches;

    if (isDesktop && !mainNav.classList.contains('expanded')) {
      // First click on a collapsed nav just expands it and reveals labels.
      mainNav.classList.add('expanded');
      return;
    }

    const btn = e.target.closest('button');
    if (!btn) return;
    showScreen(btn.dataset.screen);

    if (isDesktop) mainNav.classList.remove('expanded');
  });

  document.addEventListener('click', (e) => {
    const isDesktop = window.matchMedia('(min-width: 721px)').matches;
    if (isDesktop && mainNav.classList.contains('expanded') && !mainNav.contains(e.target)) {
      mainNav.classList.remove('expanded');
    }
  });

  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') mainNav.classList.remove('expanded');
  });

  /* ---------------- Settings: theme + default level ---------------- */
  const themeToggle = document.getElementById('themeToggle');
  const defaultLevelToggle = document.getElementById('defaultLevelToggle');
  const responseStyleToggle = document.getElementById('responseStyleToggle');
  const languageToggle = document.getElementById('languageToggle');
  const focusModeToggle = document.getElementById('focusModeToggle');
  let tutorResponseStyle = 'detailed';
  let tutorLanguage = 'en';

  function applyTheme(theme) {
    document.documentElement.setAttribute('data-theme', theme);
    [...themeToggle.children].forEach(b => b.classList.toggle('active', b.dataset.theme === theme));
    localStorage.setItem('clavi_theme', theme);
  }

  themeToggle.addEventListener('click', (e) => {
    const btn = e.target.closest('button');
    if (!btn) return;
    applyTheme(btn.dataset.theme);
  });

  /* ---------------- Wallpaper ---------------- */
  const appWallpaper = document.getElementById('appWallpaper');
  const wallpaperGrid = document.getElementById('wallpaperGrid');
  const WALLPAPER_CLASSES = ['wp-midnight', 'wp-savanna', 'wp-aurora', 'wp-emerald', 'wp-rosegold', 'wp-obsidian', 'wp-ocean', 'wp-ocean-sunset', 'wp-mountains', 'wp-hills', 'wp-classroom'];

  function applyWallpaper(name) {
    if (!appWallpaper) return;
    WALLPAPER_CLASSES.forEach(c => appWallpaper.classList.remove(c));
    appWallpaper.removeAttribute('data-animated');
    if (name) {
      appWallpaper.classList.add(name);
      appWallpaper.setAttribute('data-animated', 'true');
    }
    if (wallpaperGrid) {
      wallpaperGrid.querySelectorAll('.wallpaper-swatch').forEach(sw => {
        sw.classList.toggle('active', sw.dataset.wallpaper === (name || ''));
      });
    }
    localStorage.setItem('clavi_wallpaper', name || '');
  }

  if (wallpaperGrid) {
    wallpaperGrid.addEventListener('click', (e) => {
      const swatch = e.target.closest('.wallpaper-swatch');
      if (!swatch) return;
      applyWallpaper(swatch.dataset.wallpaper);
    });
  }

  function applyResponseStyle(style) {
    tutorResponseStyle = style;
    [...responseStyleToggle.children].forEach(b => b.classList.toggle('active', b.dataset.style === style));
    localStorage.setItem('clavi_response_style', style);
  }

  responseStyleToggle.addEventListener('click', (e) => {
    const btn = e.target.closest('button');
    if (btn) applyResponseStyle(btn.dataset.style);
  });

  function applyLanguage(lang) {
    tutorLanguage = lang;
    [...languageToggle.children].forEach(b => b.classList.toggle('active', b.dataset.lang === lang));
    localStorage.setItem('clavi_language', lang);
  }

  languageToggle.addEventListener('click', (e) => {
    const btn = e.target.closest('button');
    if (btn) applyLanguage(btn.dataset.lang);
  });

  function applyFocusMode(enabled) {
    document.body.classList.toggle('focus-mode', enabled);
    [...focusModeToggle.children].forEach(b => b.classList.toggle('active', (b.dataset.focus === 'on') === enabled));
    localStorage.setItem('clavi_focus_mode', enabled ? 'on' : 'off');
  }

  focusModeToggle.addEventListener('click', (e) => {
    const btn = e.target.closest('button');
    if (btn) applyFocusMode(btn.dataset.focus === 'on');
  });

  /* ---------------- CBC grade / Secondary form options ---------------- */
  const CBC_GRADES = ['Grade 3', 'Grade 4', 'Grade 5', 'Grade 6', 'Grade 7', 'Grade 8', 'Grade 9'];
  const UPPER_PRIMARY_GRADES = ['Grade 4', 'Grade 5', 'Grade 6'];
  const JUNIOR_SCHOOL_GRADES = ['Grade 7', 'Grade 8', 'Grade 9'];
  const SECONDARY_FORMS = ['Form 1', 'Form 2', 'Form 3', 'Form 4'];
  const SENIOR_SCHOOL_GRADES = ['Grade 10', 'Grade 11', 'Grade 12'];
  const CAMPUS_YEARS = ['Year 1', 'Year 2', 'Year 3', 'Year 4', 'Year 5'];

  function subLevelLabelFor(level) {
    if (level === 'cbc' || level === 'upper_primary' || level === 'junior_school' || level === 'senior_school') return 'Grade:';
    if (level === 'campus') return 'Year:';
    return 'Form:';
  }

  function populateGradeFormSelect(selectEl, level, savedValue) {
    const options = level === 'cbc'
      ? CBC_GRADES
      : level === 'upper_primary'
        ? UPPER_PRIMARY_GRADES
        : level === 'junior_school'
          ? JUNIOR_SCHOOL_GRADES
          : level === 'senior_school'
            ? SENIOR_SCHOOL_GRADES
            : level === 'campus'
              ? CAMPUS_YEARS
              : SECONDARY_FORMS;
    selectEl.innerHTML = options.map(o => `<option value="${o}">${o}</option>`).join('');
    if (savedValue && options.includes(savedValue)) {
      selectEl.value = savedValue;
    }
  }

  function applyDefaultLevel(level, gradeForm) {
    [...defaultLevelToggle.children].forEach(b => b.classList.toggle('active', b.dataset.level === level));
    localStorage.setItem('clavi_default_level', level);

    document.getElementById('settingsSubLevelLabel').textContent = subLevelLabelFor(level);
    populateGradeFormSelect(document.getElementById('settingsGradeFormSelect'), level, gradeForm);
    const chosenGradeForm = document.getElementById('settingsGradeFormSelect').value;
    localStorage.setItem('clavi_default_grade_form', chosenGradeForm);

    // sync the home screen toggle + selector too
    currentLevel = level;
    currentGradeForm = chosenGradeForm;
    [...levelToggle.children].forEach(b => b.classList.toggle('active', b.dataset.level === level));
    document.getElementById('subLevelLabel').textContent = subLevelLabelFor(level);
    populateGradeFormSelect(document.getElementById('gradeFormSelect'), level, chosenGradeForm);
    filterSubjectsForLevel();
  }

  defaultLevelToggle.addEventListener('click', (e) => {
    const btn = e.target.closest('button');
    if (!btn) return;
    applyDefaultLevel(btn.dataset.level, null);
  });

  document.getElementById('settingsGradeFormSelect').addEventListener('change', (e) => {
    localStorage.setItem('clavi_default_grade_form', e.target.value);
    currentGradeForm = e.target.value;
    document.getElementById('gradeFormSelect').value = e.target.value;
  });

  const copyReferralBtn = document.getElementById('copyReferralBtn');
  if (copyReferralBtn) {
    copyReferralBtn.addEventListener('click', async () => {
      const input = document.getElementById('referralLinkInput');
      const text = input.value;
      const originalLabel = copyReferralBtn.textContent;
      try {
        if (navigator.clipboard && window.isSecureContext) {
          await navigator.clipboard.writeText(text);
        } else {
          input.select();
          document.execCommand('copy');
        }
        copyReferralBtn.textContent = 'Copied!';
        localStorage.setItem('clavi_referral_copied', 'true');
      } catch (_) {
        input.select();
        copyReferralBtn.textContent = 'Select & copy';
      }
      setTimeout(() => { copyReferralBtn.textContent = originalLabel; }, 1800);
    });
  }

  /* ---------------- Account track (high school vs campus) ---------------- */
  const accountTrackToggle = document.getElementById('accountTrackToggle');
  if (accountTrackToggle) {
    const trackLocked = accountTrackToggle.dataset.locked === 'true';
    [...accountTrackToggle.children].forEach(b => b.classList.toggle('active', b.dataset.track === ACCOUNT_TRACK));
    if (trackLocked) {
      accountTrackToggle.classList.add('disabled');
      [...accountTrackToggle.children].forEach(b => { b.disabled = true; });
    }
    accountTrackToggle.addEventListener('click', (e) => {
      if (trackLocked) return;
      const btn = e.target.closest('button');
      if (!btn || btn.dataset.track === ACCOUNT_TRACK) return;
      const label = btn.dataset.track === 'campus' ? 'Campus' : 'High school';
      if (!confirm(`Switch to ${label}? This changes your whole subject list and reloads the app.`)) return;
      localStorage.setItem('clavi_student_type', btn.dataset.track);
      fetch('/api/profile', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ studentTrack: btn.dataset.track })
      }).finally(() => { window.location.reload(); });
    });
  }

  /* ---------------- Leaderboard profile settings ---------------- */
  const displayNameInput = document.getElementById('displayNameInput');
  const saveDisplayNameBtn = document.getElementById('saveDisplayNameBtn');
  const leaderboardOptToggle = document.getElementById('leaderboardOptToggle');
  const phoneNumberInput = document.getElementById('phoneNumberInput');
  const savePhoneBtn = document.getElementById('savePhoneBtn');
  const phoneNumberError = document.getElementById('phoneNumberError');

  fetch('/api/profile')
    .then(res => res.ok ? res.json() : Promise.reject())
    .then(profile => {
      displayNameInput.value = profile.displayName || '';
      const opt = profile.leaderboardOptIn ? 'on' : 'off';
      [...leaderboardOptToggle.children].forEach(b => b.classList.toggle('active', b.dataset.opt === opt));
      if (phoneNumberInput) phoneNumberInput.value = profile.phoneNumber || '';
    })
    .catch(() => {});

  saveDisplayNameBtn.addEventListener('click', () => {
    const name = displayNameInput.value.trim();
    if (!name) return;
    const originalLabel = saveDisplayNameBtn.textContent;
    saveDisplayNameBtn.textContent = 'Saving…';
    fetch('/api/profile', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ displayName: name })
    })
      .then(res => res.ok ? res.json() : Promise.reject())
      .then(() => { saveDisplayNameBtn.textContent = 'Saved!'; })
      .catch(() => { saveDisplayNameBtn.textContent = 'Failed'; })
      .finally(() => { setTimeout(() => { saveDisplayNameBtn.textContent = originalLabel; }, 1500); });
  });

  leaderboardOptToggle.addEventListener('click', (e) => {
    const btn = e.target.closest('button');
    if (!btn) return;
    const optedIn = btn.dataset.opt === 'on';
    [...leaderboardOptToggle.children].forEach(b => b.classList.toggle('active', b === btn));
    fetch('/api/profile', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ leaderboardOptIn: optedIn })
    }).catch(() => {});
  });

  if (savePhoneBtn) {
    savePhoneBtn.addEventListener('click', () => {
      phoneNumberError.style.display = 'none';
      const originalLabel = savePhoneBtn.textContent;
      savePhoneBtn.textContent = 'Saving…';
      fetch('/api/profile', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ phoneNumber: phoneNumberInput.value.trim() })
      })
        .then(async res => {
          if (!res.ok) {
            const errData = await res.json().catch(() => ({}));
            throw new Error(errData.error || 'Could not save phone number');
          }
          savePhoneBtn.textContent = 'Saved!';
        })
        .catch(err => {
          savePhoneBtn.textContent = 'Failed';
          phoneNumberError.textContent = err.message;
          phoneNumberError.style.display = 'block';
        })
        .finally(() => { setTimeout(() => { savePhoneBtn.textContent = originalLabel; }, 1500); });
    });
  }

  document.getElementById('clearChatBtn').addEventListener('click', () => {
    conversationHistory = [];
    notebook.innerHTML = '';
    notebook.appendChild(emptyHint);
    emptyHint.innerHTML = `Type a question below to get started — e.g. "${exampleQuestionFor(currentSubject)}"`;
    emptyHint.style.display = 'block';
    if (currentSubject) {
      try { localStorage.removeItem(chatStorageKey(currentLevel, currentGradeForm, currentSubject)); } catch (_) {}
    }
  });

  document.getElementById('resetStatsBtn').addEventListener('click', () => {
    if (!confirm('Reset all your progress stats and streak? This can\'t be undone.')) return;
    localStorage.removeItem('clavi_stats');
    renderDashboard();
  });

  /* Load saved theme/level on startup */
  (function loadSettings() {
    const savedTheme = localStorage.getItem('clavi_theme') || 'light';
    applyTheme(savedTheme);
    applyWallpaper(localStorage.getItem('clavi_wallpaper') || '');
    let savedLevel = localStorage.getItem('clavi_default_level') || (ACCOUNT_TRACK === 'campus' ? 'campus' : 'secondary');
    // The account's track is fixed server-side — never let a stale
    // localStorage value put someone in the wrong track's level.
    if (ACCOUNT_TRACK === 'campus') savedLevel = 'campus';
    if (ACCOUNT_TRACK === 'highschool' && savedLevel === 'campus') savedLevel = 'secondary';
    const savedGradeForm = localStorage.getItem('clavi_default_grade_form') || null;
    applyDefaultLevel(savedLevel, savedGradeForm);
    applyResponseStyle(localStorage.getItem('clavi_response_style') || 'detailed');
    applyLanguage(localStorage.getItem('clavi_language') || 'en');
    applyFocusMode(localStorage.getItem('clavi_focus_mode') === 'on');
  })();

  /* ---------------- Stats tracking ---------------- */
  function getStats() {
    const raw = localStorage.getItem('clavi_stats');
    const stats = raw ? JSON.parse(raw) : { total: 0, bySubject: {}, lastActiveDate: null, streak: 0 };
    if (typeof stats.freezesAvailable !== 'number') stats.freezesAvailable = 1;
    return stats;
  }

  function saveStats(stats) {
    localStorage.setItem('clavi_stats', JSON.stringify(stats));
  }

  function recordQuestion(subject, amount) {
    amount = amount || 1;
    const stats = getStats();
    stats.total += amount;
    stats.bySubject[subject] = (stats.bySubject[subject] || 0) + amount;

    const today = new Date().toISOString().slice(0, 10);
    if (stats.lastActiveDate !== today) {
      const y = new Date();
      y.setDate(y.getDate() - 1);
      const yesterday = y.toISOString().slice(0, 10);

      const twoDaysAgo = new Date();
      twoDaysAgo.setDate(twoDaysAgo.getDate() - 2);
      const twoDaysAgoStr = twoDaysAgo.toISOString().slice(0, 10);

      if (stats.lastActiveDate === yesterday) {
        stats.streak += 1;
      } else if (stats.lastActiveDate === twoDaysAgoStr && stats.freezesAvailable > 0) {
        // Exactly one day was missed and a freeze is available — spend it
        // to protect the streak instead of resetting to 1.
        stats.freezesAvailable -= 1;
        stats.streak += 1;
        showStreakFreezeUsedToast();
      } else {
        stats.streak = 1;
      }

      // Earn a new freeze every 7-day streak milestone, capped at 2 stored
      // at once — mirrors how Duolingo-style streak freezes are earned.
      if (stats.streak > 0 && stats.streak % 7 === 0 && stats.freezesAvailable < 2) {
        stats.freezesAvailable += 1;
      }

      stats.lastActiveDate = today;
    }
    saveStats(stats);
    syncStatsToServer();
    const banner = document.getElementById('streakReminderBanner');
    if (banner) banner.style.display = 'none';
    if (typeof renderOnboardingChecklist === 'function') renderOnboardingChecklist();
  }

  function showStreakFreezeUsedToast() {
    const toast = document.createElement('div');
    toast.textContent = '🧊 A streak freeze saved your streak — you missed a day, but you\'re still going!';
    toast.style.cssText = 'position:fixed; bottom:24px; left:50%; transform:translateX(-50%); background:var(--chalk); color:#fff; padding:12px 20px; border-radius:10px; font-size:13.5px; font-weight:600; z-index:9999; box-shadow:0 8px 24px rgba(0,0,0,.25); max-width:90vw; text-align:center;';
    document.body.appendChild(toast);
    setTimeout(() => toast.remove(), 5000);
  }

  let statsSyncInFlight = false;
  let statsSyncQueued = false;

  function syncStatsToServer() {
    if (statsSyncInFlight) { statsSyncQueued = true; return; }
    statsSyncInFlight = true;
    const stats = getStats(); // always read the freshest saved state at send time
    fetch('/api/sync-stats', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(stats)
    }).catch(() => { /* offline or server hiccup — local copy still safe, will retry next question */ })
      .finally(() => {
        statsSyncInFlight = false;
        if (statsSyncQueued) {
          statsSyncQueued = false;
          syncStatsToServer();
        }
      });
  }

  // ---------------- Resume card + Daily Spark (home screen extras) ----------------
  function comebackMessageFor(last) {
    if (!last.savedAt) return `${last.subject} — continue where you left off`;
    const daysAway = Math.floor((Date.now() - last.savedAt) / 86400000);
    if (daysAway <= 0) return `${last.subject} — continue where you left off`;
    if (daysAway === 1) return `Welcome back! Pick up ${last.subject} from yesterday`;
    if (daysAway <= 6) return `It's been ${daysAway} days — jump back into ${last.subject}?`;
    if (daysAway <= 20) return `We've missed you! Let's get back into ${last.subject}`;
    return `Long time no see! Ease back in with ${last.subject}`;
  }

  function renderResumeCard() {
    const el = document.getElementById('resumeCard');
    const textEl = document.getElementById('resumeCardText');
    const btn = document.getElementById('resumeCardBtn');
    if (!el || !textEl || !btn) return;

    let last = null;
    try { last = JSON.parse(localStorage.getItem('clavi_last_subject') || 'null'); } catch (_) {}

    // Only offer to resume within the account's own track — never suggest a
    // stale subject from the other track (can happen if someone switched
    // tracks in Settings after previously studying under the old one).
    if (!last || !last.subject ||
        (ACCOUNT_TRACK === 'campus' && last.level !== 'campus') ||
        (ACCOUNT_TRACK === 'highschool' && last.level === 'campus')) {
      el.style.display = 'none';
      return;
    }

    textEl.textContent = comebackMessageFor(last);
    el.style.display = 'flex';
    btn.onclick = () => {
      currentLevel = last.level;
      currentGradeForm = last.gradeForm;
      [...levelToggle.children].forEach(b => b.classList.toggle('active', b.dataset.level === currentLevel));
      document.getElementById('subLevelLabel').textContent = subLevelLabelFor(currentLevel);
      populateGradeFormSelect(gradeFormSelect, currentLevel, currentGradeForm);
      filterSubjectsForLevel();
      showScreen('chatScreen');
      openSubjectChat(last.subject);
    };
  }

  const DAILY_SPARK_FACTS = [
    "Kenya has 47 counties, each led by an elected governor.",
    "The word \"algebra\" comes from the Arabic \"al-jabr\", meaning \"reunion of broken parts\".",
    "Mount Kenya is Africa's second-highest peak, after Kilimanjaro.",
    "A group of lions is called a \"pride\" — a group of hyenas is a \"clan\".",
    "The human heart beats about 100,000 times a day.",
    "Water expands by about 9% when it freezes — that's why ice floats.",
    "The Great Rift Valley runs right through Kenya, and is slowly widening every year.",
    "Shakespeare invented or popularised over 1,700 words still used in English today.",
    "Photosynthesis produces the oxygen in roughly 2 out of every 3 breaths you take.",
    "Nairobi is one of the few capital cities in the world with a national park inside it.",
    "The Pythagorean theorem was known and used by Babylonian mathematicians over 1,000 years before Pythagoras.",
    "Kiswahili is spoken by over 200 million people across East Africa.",
    "A bolt of lightning is roughly five times hotter than the surface of the sun.",
    "The first computer \"bug\" was an actual moth found stuck in a relay in 1947.",
    "Honey never spoils — archaeologists have found edible honey in 3,000-year-old Egyptian tombs.",
    "The Maasai Mara hosts one of the largest wildlife migrations on Earth every year.",
    "Compound interest was once called \"the eighth wonder of the world\" — famously attributed to Einstein.",
    "Octopuses have three hearts and blue blood.",
    "Kenya's Lake Turkana is the world's largest permanent desert lake.",
    "The shortest war in recorded history lasted just 38 minutes, between Britain and Zanzibar in 1896.",
  ];
  function renderDailySpark() {
    const el = document.getElementById('dailySparkCard');
    const textEl = document.getElementById('dailySparkText');
    if (!el || !textEl) return;
    const start = new Date(new Date().getFullYear(), 0, 0);
    const dayOfYear = Math.floor((Date.now() - start) / 86400000);
    textEl.textContent = DAILY_SPARK_FACTS[dayOfYear % DAILY_SPARK_FACTS.length];
    el.style.display = 'flex';
  }

  // ---------------- Confetti (used by Daily Challenge, streak badges, level-ups) ----------------
  function fireConfetti() {
    const colors = ['#d9a62e', '#5c8d36', '#2E4C6E', '#B8433A', '#6C3483'];
    const count = 60;
    for (let i = 0; i < count; i++) {
      const piece = document.createElement('div');
      piece.className = 'confetti-piece';
      piece.style.left = Math.random() * 100 + 'vw';
      piece.style.background = colors[Math.floor(Math.random() * colors.length)];
      piece.style.animationDuration = (2 + Math.random() * 1.5) + 's';
      piece.style.opacity = String(0.7 + Math.random() * 0.3);
      document.body.appendChild(piece);
      setTimeout(() => piece.remove(), 3600);
    }
  }

  // ---------------- Daily Challenge ----------------
  function renderDailyChallenge() {
    const card = document.getElementById('dailyChallengeCard');
    const loadingEl = document.getElementById('dcLoading');
    const subjectEl = document.getElementById('dcSubject');
    const questionEl = document.getElementById('dcQuestion');
    const optionsEl = document.getElementById('dcOptions');
    const resultEl = document.getElementById('dcResult');
    const streakBadge = document.getElementById('dcStreakBadge');
    if (!card) return;

    card.style.display = 'block';
    loadingEl.style.display = 'block';
    subjectEl.textContent = '';
    questionEl.textContent = '';
    optionsEl.innerHTML = '';
    resultEl.style.display = 'none';
    document.getElementById('dcShareBtn').style.display = 'none';

    fetch('/api/daily-challenge')
      .then(res => res.ok ? res.json() : res.json().then(e => Promise.reject(e)))
      .then(data => {
        loadingEl.style.display = 'none';
        subjectEl.textContent = data.subject;
        questionEl.textContent = data.question;

        if (data.streak > 0) {
          streakBadge.textContent = `🔥 ${data.streak} day streak`;
          streakBadge.style.display = 'inline-block';
        } else {
          streakBadge.style.display = 'none';
        }

        renderDailyChallengeOptions(data.options);

        if (data.answered) {
          disableDailyChallengeOptions();
          if (typeof data.correctIndex === 'number') {
            // We only know whether they were right overall on reload — not
            // which specific wrong option they picked — so just mark the
            // correct one; no red highlight in this reload path.
            const correctBtn = optionsEl.querySelector(`.dc-option[data-index="${data.correctIndex}"]`);
            if (correctBtn) correctBtn.classList.add('dc-correct');
          }
          resultEl.style.display = 'block';
          resultEl.textContent = (data.wasCorrect ? '✅ Correct! ' : '➜ ') + (data.explanation || '');
          updateDailyChallengeShareButton(data.streak);
        }
      })
      .catch((err) => {
        loadingEl.textContent = (err && err.error) || "Couldn't load today's challenge — try again in a moment.";
      });
  }

  function renderDailyChallengeOptions(options) {
    const optionsEl = document.getElementById('dcOptions');
    optionsEl.innerHTML = options.map((opt, i) => `<button type="button" class="dc-option" data-index="${i}">${opt}</button>`).join('');
    optionsEl.querySelectorAll('.dc-option').forEach(btn => {
      btn.addEventListener('click', () => submitDailyChallengeAnswer(parseInt(btn.dataset.index, 10)));
    });
  }

  function disableDailyChallengeOptions() {
    document.querySelectorAll('#dcOptions .dc-option').forEach(b => { b.disabled = true; });
  }

  function updateDailyChallengeShareButton(streak) {
    const btn = document.getElementById('dcShareBtn');
    if (!btn) return;
    if (streak > 0) {
      btn.style.display = 'inline-block';
      btn.onclick = () => shareDailyChallengeStreak(streak);
    } else {
      btn.style.display = 'none';
    }
  }

  function shareDailyChallengeStreak(streak) {
    const link = REFERRAL_LINK || window.location.origin;
    const message = `🔥 I'm on a ${streak}-day Daily Challenge streak on Clavis! Think you can beat me? Try it here: ${link}`;
    window.open(`https://wa.me/?text=${encodeURIComponent(message)}`, '_blank');
  }

  function submitDailyChallengeAnswer(choiceIndex) {
    const optionsEl = document.getElementById('dcOptions');
    const resultEl = document.getElementById('dcResult');
    const streakBadge = document.getElementById('dcStreakBadge');
    const chosenBtn = optionsEl.querySelector(`.dc-option[data-index="${choiceIndex}"]`);
    if (chosenBtn) chosenBtn.classList.add('dc-chosen');
    disableDailyChallengeOptions();

    fetch('/api/daily-challenge/answer', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ choiceIndex })
    })
      .then(res => res.ok ? res.json() : res.json().then(e => Promise.reject(e)))
      .then(data => {
        document.querySelectorAll('#dcOptions .dc-option').forEach(b => {
          const idx = parseInt(b.dataset.index, 10);
          if (idx === data.correctIndex) b.classList.add('dc-correct');
          else if (idx === choiceIndex && !data.correct) b.classList.add('dc-wrong');
        });
        resultEl.style.display = 'block';
        resultEl.textContent = (data.correct ? '✅ Correct! ' : '➜ ') + (data.explanation || '');
        if (data.streak > 0) {
          streakBadge.textContent = `🔥 ${data.streak} day streak`;
          streakBadge.style.display = 'inline-block';
        } else {
          streakBadge.style.display = 'none';
        }
        updateDailyChallengeShareButton(data.streak);
        if (data.correct) fireConfetti();
      })
      .catch((err) => {
        resultEl.style.display = 'block';
        resultEl.textContent = (err && err.error) || 'Something went wrong submitting your answer.';
      });
  }

  // On load, pull the account's saved stats from the server so progress
  // carries across devices/browsers instead of being stuck in one browser.
  (function syncStatsFromServer() {
    if (typeof showStreakReminderIfNeeded === 'function') showStreakReminderIfNeeded();
    if (typeof renderOnboardingChecklist === 'function') renderOnboardingChecklist();
    renderResumeCard();
    renderDailySpark();
    renderDailyChallenge();
    fetch('/api/sync-stats')
      .then(res => res.ok ? res.json() : null)
      .then(serverStats => {
        if (!serverStats) return;
        if (serverStats.total > 0) {
          // Server has real history — it's the source of truth.
          saveStats(serverStats);
        } else {
          // Server has nothing yet (new account, or first sync after this
          // update shipped) — push up whatever this browser already has.
          const local = getStats();
          if (local.total > 0) syncStatsToServer();
        }
        if (typeof renderDashboard === 'function') renderDashboard();
        if (typeof showStreakReminderIfNeeded === 'function') showStreakReminderIfNeeded();
      })
      .catch(() => { /* stay on local cache if offline */ });
  })();

  function renderOnboardingChecklist() {
    const el = document.getElementById('onboardingChecklist');
    if (!el) return;
    if (localStorage.getItem('clavi_onboarding_dismissed') === 'true') {
      el.style.display = 'none';
      return;
    }
    const stats = getStats();
    let paperCount = 0;
    try { paperCount = JSON.parse(localStorage.getItem('clavi_paper_results') || '[]').length; } catch (_) {}
    const invitedFriend = localStorage.getItem('clavi_referral_copied') === 'true';

    const steps = [
      { label: 'Ask your first question', done: stats.total > 0 },
      { label: 'Try a practice exam paper', done: paperCount > 0 },
      { label: 'Invite a friend for bonus days', done: invitedFriend },
    ];

    if (steps.every(s => s.done)) {
      el.style.display = 'none';
      return;
    }

    el.style.display = 'block';
    el.innerHTML = `
      <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:10px;">
        <div style="font-weight:700; font-size:14px; color:var(--chalk);">Getting started</div>
        <button type="button" id="dismissOnboardingBtn" style="background:none; border:none; color:var(--muted); font-size:18px; cursor:pointer; line-height:1;">&times;</button>
      </div>
      ${steps.map(s => `
        <div style="display:flex; align-items:center; gap:8px; padding:5px 0; font-size:14px; color:${s.done ? 'var(--muted)' : 'var(--ink)'}; ${s.done ? 'text-decoration:line-through;' : ''}">
          <span style="display:inline-flex; align-items:center; justify-content:center; width:18px; height:18px; border-radius:50%; border:1.5px solid ${s.done ? 'var(--chalk)' : 'var(--line)'}; background:${s.done ? 'var(--chalk)' : 'transparent'}; color:#fff; font-size:11px; flex-shrink:0;">${s.done ? '✓' : ''}</span>
          ${s.label}
        </div>
      `).join('')}
    `;
    document.getElementById('dismissOnboardingBtn').addEventListener('click', () => {
      localStorage.setItem('clavi_onboarding_dismissed', 'true');
      el.style.display = 'none';
    });
  }

  function showStreakReminderIfNeeded() {
    const banner = document.getElementById('streakReminderBanner');
    if (!banner) return;
    const stats = getStats();
    const today = new Date().toISOString().slice(0, 10);

    if (stats.total === 0 || stats.lastActiveDate === today) {
      banner.style.display = 'none';
      return;
    }

    const y = new Date();
    y.setDate(y.getDate() - 1);
    const yesterday = y.toISOString().slice(0, 10);

    if (stats.lastActiveDate === yesterday && stats.streak > 0) {
      banner.textContent = `🔥 Keep your ${stats.streak}-day streak alive — ask a question today!`;
    } else {
      banner.textContent = `👋 Welcome back! Ask a question today to start a new streak.`;
    }
    banner.style.display = 'block';
  }

  const LEVEL_TITLES = [
    'Fresh Start', 'Curious Mind', 'Sharp Thinker', 'Rising Scholar', 'Dedicated Learner',
    'Subject Explorer', 'Knowledge Seeker', 'Study Champion', 'Clavis Scholar', 'Clavis Legend'
  ];
  const QUESTIONS_PER_LEVEL = 15;

  function computeLevelInfo(total) {
    const level = Math.floor(total / QUESTIONS_PER_LEVEL) + 1;
    const titleIdx = Math.min(level, LEVEL_TITLES.length) - 1;
    const inLevel = total % QUESTIONS_PER_LEVEL;
    return {
      level,
      title: LEVEL_TITLES[titleIdx],
      inLevel,
      needed: QUESTIONS_PER_LEVEL,
      progressPct: (inLevel / QUESTIONS_PER_LEVEL) * 100,
    };
  }

  function renderXpCard(stats) {
    const card = document.getElementById('xpCard');
    if (!card) return;
    if (!stats.total) { card.style.display = 'none'; return; }
    card.style.display = 'block';

    const info = computeLevelInfo(stats.total);
    document.getElementById('xpLevelTitle').textContent = info.title;
    document.getElementById('xpLevelNum').textContent = `Level ${info.level}`;
    document.getElementById('xpBarFill').style.width = info.progressPct + '%';
    document.getElementById('xpProgressText').textContent = `${info.inLevel} / ${info.needed} questions to next level`;

    // Celebrate crossing into a new level since we last checked — but not
    // on the very first load (that would confetti-blast every new account).
    let seenLevel = 0;
    try { seenLevel = parseInt(localStorage.getItem('clavi_seen_level') || '0', 10); } catch (_) {}
    if (info.level > seenLevel) {
      if (seenLevel > 0) fireConfetti();
      try { localStorage.setItem('clavi_seen_level', String(info.level)); } catch (_) {}
    }
  }

  function renderDashboard() {
    const stats = getStats();
    const dashEmptyState = document.getElementById('dashEmptyState');
    const subjectBarsWrap = document.getElementById('subjectBarsWrap');

    renderXpCard(stats);
    document.getElementById('statTotal').textContent = stats.total;
    document.getElementById('statStreak').textContent = stats.streak;

    const freezeEl = document.getElementById('freezeIndicator');
    if (freezeEl) {
      if (stats.freezesAvailable > 0) {
        freezeEl.textContent = `🧊 x${stats.freezesAvailable} freeze${stats.freezesAvailable > 1 ? 's' : ''} saved`;
        freezeEl.style.display = 'block';
      } else {
        freezeEl.style.display = 'none';
      }
    }

    const entries = Object.entries(stats.bySubject).sort((a, b) => b[1] - a[1]);
    document.getElementById('statTopSubject').textContent = entries.length ? entries[0][0] : '—';

    if (stats.total === 0) {
      dashEmptyState.style.display = 'block';
      subjectBarsWrap.style.display = 'none';
    } else {
      dashEmptyState.style.display = 'none';
      subjectBarsWrap.style.display = 'block';
      const maxCount = entries.length ? entries[0][1] : 1;
      const barsEl = document.getElementById('subjectBars');
      barsEl.innerHTML = entries.map(([subject, count]) => `
        <div class="subject-bar-row">
          <div class="bar-name">${subject}</div>
          <div class="bar-track"><div class="bar-fill" style="width:${(count / maxCount) * 100}%"></div></div>
          <div class="bar-count">${count}</div>
        </div>
      `).join('');
    }

    const quickLinksEl = document.getElementById('dashQuickLinks');
    quickLinksEl.innerHTML = getVisibleSubjects().map(s => `<button class="quick-link-btn" data-subject="${s}">${s}</button>`).join('');
    quickLinksEl.querySelectorAll('.quick-link-btn').forEach(btn => {
      btn.addEventListener('click', () => openSubjectChat(btn.dataset.subject));
    });

    renderBadges(stats, null);
    renderExamProgress();
  }

  function renderBadges(stats, averagePercent) {
    const badges = [];
    if (stats.streak >= 3) badges.push({ key: 'streak3', label: '🔥 3-day streak' });
    if (stats.streak >= 7) badges.push({ key: 'streak7', label: '🔥 Week warrior' });
    if (stats.streak >= 30) badges.push({ key: 'streak30', label: '🔥 30-day streak' });
    if (stats.total >= 10) badges.push({ key: 'total10', label: '📚 10 questions asked' });
    if (stats.total >= 50) badges.push({ key: 'total50', label: '📚 50 questions club' });
    if (stats.total >= 200) badges.push({ key: 'total200', label: '📚 200 questions club' });
    if (averagePercent !== null && averagePercent >= 70) badges.push({ key: 'avg70', label: '⭐ Averaging 70%+' });
    if (averagePercent !== null && averagePercent >= 90) badges.push({ key: 'avg90', label: '🏆 Top scorer' });

    const el = document.getElementById('badgeRow');
    el.innerHTML = badges.map(b => `<div class="badge-chip">${b.label}</div>`).join('');

    // Celebrate any badge earned since the last time we checked — but skip
    // the very first-ever render, so an account with existing history
    // doesn't confetti-blast for every badge it already has all at once.
    let seen = [];
    let hasSeenBefore = false;
    try {
      const raw = localStorage.getItem('clavi_seen_badges');
      hasSeenBefore = raw !== null;
      seen = raw ? JSON.parse(raw) : [];
    } catch (_) {}
    const newlyEarned = badges.map(b => b.key).filter(k => !seen.includes(k));
    if (hasSeenBefore && newlyEarned.length) fireConfetti();
    try { localStorage.setItem('clavi_seen_badges', JSON.stringify(badges.map(b => b.key))); } catch (_) {}
  }

  function scoreTier(pct) {
    if (pct >= 70) return 'tier-strong';
    if (pct >= 50) return 'tier-mid';
    return 'tier-weak';
  }

  function renderExamProgress() {
    fetch('/api/exam-progress')
      .then(res => res.ok ? res.json() : Promise.reject())
      .then(data => {
        const wrap = document.getElementById('examProgressWrap');
        if (!data || !data.totalPapers) {
          wrap.style.display = 'none';
          return;
        }
        wrap.style.display = 'block';

        document.getElementById('progAverage').textContent = data.averagePercent + '%';
        document.getElementById('progTotal').textContent = data.totalPapers;

        const weakestEl = document.getElementById('progWeakestCallout');
        if (data.weakestSubject) {
          weakestEl.style.display = 'flex';
          document.getElementById('progWeakestText').textContent =
            `${data.weakestSubject.subject} — averaging ${data.weakestSubject.average}% over ${data.weakestSubject.attempts} papers. A bit more practice here should help.`;
        } else {
          weakestEl.style.display = 'none';
        }

        const scoreBarsEl = document.getElementById('scoreBars');
        scoreBarsEl.innerHTML = data.bySubject.map(s => `
          <div class="score-bar-row">
            <div class="bar-name">${s.subject}</div>
            <div class="bar-track"><div class="bar-fill ${scoreTier(s.average)}" style="width:${s.average}%"></div></div>
            <div class="bar-pct">${s.average}%</div>
          </div>
        `).join('');

        const trendEl = document.getElementById('trendStrip');
        trendEl.innerHTML = data.recentTrend.map(r => `
          <div class="trend-dot ${scoreTier(r.percent)}" title="${r.subject} — ${r.percent}% on ${r.date ? new Date(r.date).toLocaleDateString() : ''}">${r.percent}</div>
        `).join('');

        renderBadges(getStats(), data.averagePercent);
      })
      .catch(() => {
        document.getElementById('examProgressWrap').style.display = 'none';
      });
  }

  let lbCurrentPeriod = 'all';

  const LB_PERIOD_META = {
    all: { subtitle: "Ranked by questions asked, all time. Only students who've chosen to appear are shown.", totalLabel: 'Your questions', unit: 'questions', showStreak: true },
    week: { subtitle: "Ranked by questions asked this week — resets every Monday, so it's a fresh start for everyone.", totalLabel: 'Your questions this week', unit: 'questions', showStreak: true },
    inviters: { subtitle: 'Ranked by friends invited to Clavis. Share your link from Settings to climb the board!', totalLabel: 'Friends invited', unit: 'invited', showStreak: false },
  };

  function renderLeaderboard() {
    const listEl = document.getElementById('lbList');
    const emptyEl = document.getElementById('lbEmptyState');
    const myRankCard = document.getElementById('lbMyRankCard');
    const optedOutNotice = document.getElementById('lbOptedOutNotice');
    const subtitleEl = document.getElementById('lbSubtitle');
    const meta = LB_PERIOD_META[lbCurrentPeriod] || LB_PERIOD_META.all;

    subtitleEl.textContent = meta.subtitle;
    document.getElementById('lbMyTotalLabel').textContent = meta.totalLabel;

    fetch(`/api/leaderboard?period=${lbCurrentPeriod}`)
      .then(res => res.ok ? res.json() : Promise.reject())
      .then(data => {
        optedOutNotice.style.display = data.optedIn ? 'none' : 'block';

        if (data.myRank) {
          myRankCard.style.display = 'grid';
          document.getElementById('lbMyRank').textContent = '#' + data.myRank;
          document.getElementById('lbMyTotal').textContent = data.myTotal;
        } else {
          myRankCard.style.display = 'none';
        }

        if (!data.top || !data.top.length) {
          emptyEl.style.display = 'block';
          emptyEl.textContent = lbCurrentPeriod === 'inviters'
            ? "No one's invited a friend yet — share your link from Settings and be the first!"
            : "No one's on the board yet — ask a few questions and be the first!";
          listEl.innerHTML = '';
          return;
        }
        emptyEl.style.display = 'none';

        listEl.innerHTML = data.top.map(e => `
          <div class="leaderboard-row ${e.isMe ? 'is-me' : ''}">
            <div class="lb-rank">${e.rank <= 3 ? ['🥇','🥈','🥉'][e.rank - 1] : '#' + e.rank}</div>
            <div class="lb-name">${e.name}${e.isMe ? ' (you)' : ''}</div>
            <div class="lb-total">${e.total} ${meta.unit}${meta.showStreak ? ' · 🔥 ' + e.streak : ''}</div>
          </div>
        `).join('');
      })
      .catch(() => {
        listEl.innerHTML = '';
        emptyEl.style.display = 'block';
      });
  }

  const lbPeriodToggle = document.getElementById('lbPeriodToggle');
  if (lbPeriodToggle) {
    lbPeriodToggle.addEventListener('click', (e) => {
      const btn = e.target.closest('button');
      if (!btn) return;
      lbCurrentPeriod = btn.dataset.period;
      [...lbPeriodToggle.children].forEach(b => b.classList.toggle('active', b === btn));
      renderLeaderboard();
    });
  }

  /* ---------------- Home / Chat logic ---------------- */
  levelToggle.addEventListener('click', (e) => {
    const btn = e.target.closest('button');
    if (!btn) return;
    currentLevel = btn.dataset.level;
    [...levelToggle.children].forEach(b => b.classList.toggle('active', b === btn));

    document.getElementById('subLevelLabel').textContent = subLevelLabelFor(currentLevel);
    populateGradeFormSelect(gradeFormSelect, currentLevel, null);
    currentGradeForm = gradeFormSelect.value;
    filterSubjectsForLevel();
  });

  gradeFormSelect.addEventListener('change', (e) => {
    currentGradeForm = e.target.value;
  });

  function openSubjectChat(subject) {
    currentSubject = subject;
    notebook.innerHTML = '';
    notebook.appendChild(emptyHint);
    notebook.appendChild(teachStarter);
    emptyHint.innerHTML = `Type a question below to get started — e.g. "${exampleQuestionFor(subject)}"`;

    try {
      localStorage.setItem('clavi_last_subject', JSON.stringify({
        subject, level: currentLevel, gradeForm: currentGradeForm, savedAt: Date.now()
      }));
    } catch (_) {}

    const saved = loadChatProgress(currentLevel, currentGradeForm, subject);
    teachState = loadTeachState(currentLevel, currentGradeForm, subject);
    if (saved && saved.length) {
      conversationHistory = saved;
      emptyHint.style.display = 'none';
      teachStarter.hidden = true;
      saved.forEach(m => addMessage(m.role === 'assistant' ? 'tutor' : 'user', m.content));
      if (saved[saved.length - 1].role === 'assistant') showExplainDifferentlyButton();
    } else {
      conversationHistory = [];
      emptyHint.style.display = teachState ? 'none' : 'block';
      teachStarter.hidden = !!teachState;
    }
    updateTeachBar();

    chatSubjectLabel.textContent = currentSubject;
    chatLevelLabel.textContent = currentLevel === 'cbc'
      ? `CBC — ${currentGradeForm}`
      : currentLevel === 'upper_primary'
        ? `Upper Primary — ${currentGradeForm}`
        : currentLevel === 'junior_school'
          ? `Junior School — ${currentGradeForm}`
          : currentLevel === 'senior_school'
            ? `Senior School — ${currentGradeForm}`
            : currentLevel === 'campus'
              ? `Campus — ${currentGradeForm}`
              : `Secondary — ${currentGradeForm} (KCSE)`;

    showScreen('chatScreen');
    questionInput.focus();
  }

  document.querySelectorAll('.subject-card').forEach(card => {
    card.addEventListener('click', () => openSubjectChat(card.dataset.subject));
  });

  backBtn.addEventListener('click', () => showScreen('homeScreen'));

  function escapeHtml(str) {
    return str
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;');
  }

  function cleanTutorText(text) {
    if (!text) return text;
    return escapeHtml(text)
      .replace(/^[ \t]*\*[ \t]+/gm, '• ')                       // "* item" bullet lines -> "• item"
      .replace(/^[ \t]*#{1,6}[ \t]*(.+)$/gm, '<strong>$1</strong>') // markdown headers (e.g. "# Conclusion") -> bold
      .replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')         // **bold** -> real bold
      .replace(/\*(.*?)\*/g, '$1')                              // *italic* -> plain text
      .replace(/\*/g, '');                                      // any stray leftover asterisks
  }

  function sanitizeDiagramSvg(raw) {
    let svg = (raw || '').trim();
    if (!/^<svg[\s>]/i.test(svg)) return null;
    // Strip anything that could execute or reach outside the diagram itself.
    svg = svg
      .replace(/<script[\s\S]*?<\/script\s*>/gi, '')
      .replace(/<foreignObject[\s\S]*?<\/foreignObject\s*>/gi, '')
      .replace(/<image\b[^>]*>/gi, '')
      .replace(/\son[a-z]+\s*=\s*(".*?"|'.*?'|[^\s>]+)/gi, '')
      .replace(/(href|xlink:href)\s*=\s*("javascript:.*?"|'javascript:.*?')/gi, '');
    if (/<script/i.test(svg)) return null;
    return svg;
  }

  /* Pulls an optional [DIAGRAM]...[/DIAGRAM] block out of exam/paper question text,
     returning the remaining text plus a ready-to-insert sanitized diagram-wrap HTML string. */
  function extractDiagramHtml(text) {
    if (!text) return { text: '', diagramHtml: '' };
    const match = /\[DIAGRAM\]([\s\S]*?)\[\/DIAGRAM\]/.exec(text);
    if (!match) return { text: text, diagramHtml: '' };
    const cleanText = (text.slice(0, match.index) + text.slice(match.index + match[0].length)).trim();
    const safeSvg = sanitizeDiagramSvg(match[1]);
    return { text: cleanText, diagramHtml: safeSvg ? `<div class="diagram-wrap">${safeSvg}</div>` : '' };
  }

  function renderTutorContent(container, text) {
    container.innerHTML = '';
    const parts = (text || '').split(/\[DIAGRAM\]([\s\S]*?)\[\/DIAGRAM\]/g);
    parts.forEach((part, i) => {
      if (i % 2 === 0) {
        if (!part.trim()) return;
        const span = document.createElement('span');
        span.innerHTML = cleanTutorText(part.trim());
        container.appendChild(span);
      } else {
        const safeSvg = sanitizeDiagramSvg(part);
        if (safeSvg) {
          const wrap = document.createElement('div');
          wrap.className = 'diagram-wrap';
          wrap.innerHTML = safeSvg;
          container.appendChild(wrap);
        }
      }
    });
    renderMath(container);
  }

  /* Typesets any \( \), \[ \] LaTeX left in rendered text — used for chat, tests, and papers alike. */
  /* Renders any \( \), \[ \] LaTeX in `container` using the plain-text fallback.
     We deliberately do NOT load KaTeX from a CDN at all — on a slow or unreliable
     mobile connection, extra blocking network requests on every page load caused
     noticeable lag, and the plain-text conversion (e.g. \frac{1}{2} -> (1)/(2),
     \sin 30^{\circ} -> sin 30°) reads perfectly clearly without any external
     library or network dependency. */
  function renderMath(container) {
    if (!container) return;
    applyMathFallback(container);
  }

  function plainizeMath(text) {
    let out = text;
    for (let pass = 0; pass < 3; pass++) {
      out = out
        .replace(/\^\{\\circ\}|\^\\circ/g, '°')
        .replace(/\\sqrt\[(\d+)\]\{([^{}]*)\}/g, '$1√($2)')
        .replace(/\\sqrt\{([^{}]*)\}/g, '√($1)')
        .replace(/\\frac\{([^{}]*)\}\{([^{}]*)\}/g, '($1)/($2)')
        .replace(/\\dfrac\{([^{}]*)\}\{([^{}]*)\}/g, '($1)/($2)')
        .replace(/\\binom\{([^{}]*)\}\{([^{}]*)\}/g, '($1 choose $2)')
        .replace(/\\overrightarrow\{([^{}]*)\}/g, '$1\u20D7')
        .replace(/\\overline\{([^{}]*)\}/g, '$1\u0305')
        .replace(/\\vec\{([^{}]*)\}/g, '$1\u20D7')
        .replace(/\^\{([^{}]*)\}/g, '^$1')
        .replace(/_\{([^{}]*)\}/g, '_$1')
        .replace(/\\text\{([^{}]*)\}/g, '$1')
        .replace(/\\mathrm\{([^{}]*)\}/g, '$1');
    }
    return out
      .replace(/\\\[|\\\]|\\\(|\\\)/g, '')
      .replace(/\$\$/g, '')
      .replace(/\\rightarrow\b/g, '→')
      .replace(/\\Rightarrow\b/g, '⇒')
      .replace(/\\leftrightarrow\b/g, '↔')
      .replace(/\\left\b|\\right\b/g, '')
      .replace(/\\circ/g, '°')
      .replace(/\\times/g, '×')
      .replace(/\\div/g, '÷')
      .replace(/\\cdot/g, '·')
      .replace(/\\pm/g, '±')
      .replace(/\\mp/g, '∓')
      .replace(/\\leq/g, '≤')
      .replace(/\\geq/g, '≥')
      .replace(/\\neq/g, '≠')
      .replace(/\\approx/g, '≈')
      .replace(/\\equiv/g, '≡')
      .replace(/\\sim\b/g, '~')
      .replace(/\\propto\b/g, '∝')
      .replace(/\\parallel\b/g, '∥')
      .replace(/\\perp\b/g, '⊥')
      .replace(/\\angle\b/g, '∠')
      .replace(/\\triangle\b/g, '△')
      .replace(/\\infty\b/g, '∞')
      .replace(/\\pi\b/g, 'π')
      .replace(/\\theta\b/g, 'θ')
      .replace(/\\alpha\b/g, 'α')
      .replace(/\\beta\b/g, 'β')
      .replace(/\\gamma\b/g, 'γ')
      .replace(/\\delta\b/g, 'δ')
      .replace(/\\mu\b/g, 'μ')
      .replace(/\\lambda\b/g, 'λ')
      .replace(/\\sigma\b/g, 'σ')
      .replace(/\\in\b/g, '∈')
      .replace(/\\notin\b/g, '∉')
      .replace(/\\subset\b/g, '⊂')
      .replace(/\\cup\b/g, '∪')
      .replace(/\\cap\b/g, '∩')
      .replace(/\\forall\b/g, '∀')
      .replace(/\\exists\b/g, '∃')
      .replace(/\\ldots\b|\\cdots\b|\\dots\b/g, '…')
      .replace(/\\quad\b|\\qquad\b|\\,|\\;|\\!|\\ /g, ' ')
      .replace(/\\(sin|cos|tan|log|ln|lim|sum|int|max|min|exp)\b/g, '$1')
      // Catch-all: any remaining "\word" -> just "word" (drop backslash, keep letters) —
      // covers any LaTeX command not specifically handled above.
      .replace(/\\([a-zA-Z]+)/g, '$1')
      // Catch-all: any remaining lone backslash (e.g. \%, \_, \$) -> drop the backslash
      .replace(/\\(.)/g, '$1')
      .replace(/\\/g, '')
      // Clean up any stray unmatched braces left behind by unusual patterns
      .replace(/\{([^{}]*)\}/g, '$1');
  }

  function applyMathFallback(container) {
    if (!/\\\S/.test(container.textContent || '')) return;
    const walker = document.createTreeWalker(container, NodeFilter.SHOW_TEXT);
    const nodes = [];
    let n;
    while ((n = walker.nextNode())) nodes.push(n);
    nodes.forEach(node => {
      if (/\\\S/.test(node.textContent)) {
        node.textContent = plainizeMath(node.textContent);
      }
    });
  }

  const SPEECH_SUPPORTED = typeof window !== 'undefined' && 'speechSynthesis' in window;
  let currentSpeakBtn = null;

  function stopSpeaking() {
    if (SPEECH_SUPPORTED) window.speechSynthesis.cancel();
    if (currentSpeakBtn) currentSpeakBtn.classList.remove('speaking');
    currentSpeakBtn = null;
  }

  function toggleSpeak(btn, text) {
    if (!SPEECH_SUPPORTED || !text) return;
    const wasThisOne = currentSpeakBtn === btn;
    stopSpeaking();
    if (wasThisOne) return;
    const utter = new SpeechSynthesisUtterance(text);
    utter.lang = tutorLanguage === 'sw' ? 'sw-KE' : 'en-US';
    utter.rate = 0.98;
    utter.pitch = 1;
    const clear = () => { btn.classList.remove('speaking'); if (currentSpeakBtn === btn) currentSpeakBtn = null; };
    utter.onend = clear;
    utter.onerror = clear;
    currentSpeakBtn = btn;
    btn.classList.add('speaking');
    window.speechSynthesis.speak(utter);
  }

  notebook.addEventListener('click', (e) => {
    const btn = e.target.closest('.speak-btn');
    if (!btn) return;
    const msgDiv = btn.closest('.msg');
    toggleSpeak(btn, (msgDiv && msgDiv.dataset.speakText) || '');
  });

  function addMessage(role, text, attachment) {
    if (emptyHint.style.display !== 'none') emptyHint.style.display = 'none';
    const div = document.createElement('div');
    div.className = 'msg ' + (role === 'user' ? 'user' : 'tutor');
    if (role === 'tutor') {
      const speakBtn = SPEECH_SUPPORTED ? '<button type="button" class="speak-btn" aria-label="Read aloud">🔊</button>' : '';
      div.innerHTML = '<div class="msg-header"><span class="label">Tutor</span>' + speakBtn + '</div><span class="msg-content"></span>';
      div.dataset.speakText = (text || '').replace(/\[DIAGRAM\][\s\S]*?\[\/DIAGRAM\]/g, ' ').replace(/\s+/g, ' ').trim();
      renderTutorContent(div.querySelector('.msg-content'), text);
    } else {
      if (attachment && attachment.data) {
        const img = document.createElement('img');
        img.src = attachment.data;
        img.alt = attachment.name || 'Attached image';
        img.style.cssText = 'display:block;max-width:180px;max-height:180px;border-radius:10px;margin-bottom:6px;object-fit:cover;';
        div.appendChild(img);
      }
      const span = document.createElement('span');
      span.textContent = text;
      div.appendChild(span);
    }
    notebook.appendChild(div);
    notebook.scrollTop = notebook.scrollHeight;
    return div;
  }

  /* ---------------- Tutor avatar (Lottie AI character) ---------------- */
  // Frame ranges pulled from the file's own named markers (idle/yes/no/alert/thinking/jump).
  const AVATAR_SEGMENTS = {
    idle: [0, 29],
    yes: [31, 105],
    no: [106, 180],
    alert: [181, 270],
    thinking: [271, 390],
    jump: [391, 479],
  };

  let tutorAvatarAnim = null;

  function getTutorAvatarAnim() {
    if (!tutorAvatarAnim && window.lottie) {
      tutorAvatarAnim = lottie.loadAnimation({
        container: document.getElementById('tutorAvatar'),
        renderer: 'svg',
        loop: false,
        autoplay: false,
        path: '/static/animations/ai_robo.json'
      });
    }
    return tutorAvatarAnim;
  }

  function playAvatarState(state, loop) {
    const anim = getTutorAvatarAnim();
    if (!anim) return;
    const segment = AVATAR_SEGMENTS[state];
    if (!segment) return;
    anim.setLoop(!!loop);
    anim.playSegments(segment, true);
  }

  async function askTutor(question, attachment) {
    conversationHistory.push({ role: 'user', content: question });

    const body = {
      subject: currentSubject,
      level: currentLevel,
      gradeForm: currentGradeForm,
      responseStyle: tutorResponseStyle,
      language: tutorLanguage,
      messages: conversationHistory
    };
    if (attachment) body.attachment = attachment;
    if (teachState) {
      body.teachTopic = currentTeachTopic();
      body.teachTopics = teachState.topics;
    }

    const response = await fetch("/api/tutor", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body)
    });

    if (!response.ok) {
      let errMsg = "Server error: " + response.status;
      try {
        const errData = await response.json();
        if (errData.error) errMsg = errData.error;
      } catch (_) {}
      throw new Error(errMsg);
    }

    const data = await response.json();
    const answer = data.answer || "Sorry, I couldn't come up with an answer just now — try asking again.";
    conversationHistory.push({ role: 'assistant', content: answer });
    return { answer, topicComplete: !!data.topicComplete };
  }

  async function handleSend() {
    const question = questionInput.value.trim();
    const attachment = pendingAttachment;
    if (!question && !attachment) return;

    const displayText = question || 'Take a look at this image.';
    addMessage('user', displayText, attachment);
    questionInput.value = '';
    clearAttachment();
    sendBtn.disabled = true;
    typingIndicator.style.visibility = 'visible';
    typingLabel.textContent = 'Tutor is thinking...';
    playAvatarState('thinking', true);

    try {
      const result = await askTutor(displayText, attachment);
      addMessage('tutor', result.answer);
      saveChatProgress();
      recordQuestion(currentSubject, (teachState && result.topicComplete) ? 3 : 1);
      showExplainDifferentlyButton();
      if (teachState && result.topicComplete) {
        markTopicTaught(currentSubject, currentLevel, currentGradeForm, currentTeachTopic());
        if (teachState.topicIndex < teachState.topics.length - 1) {
          teachState.topicIndex += 1;
          teachState.pendingTopicStart = true;
          saveTeachState();
          addMessage('tutor', `✅ Great work — you've finished "${teachState.topics[teachState.topicIndex - 1]}". Ready for the next topic, "${currentTeachTopic()}"? Tap "Start next topic" below whenever you're ready.`);
        } else {
          addMessage('tutor', `🎉 That's the last topic done — you've been taught the full ${currentSubject} course! You can revise anytime, or head to Topics & Tests to check your understanding.`);
          teachState = null;
          saveTeachState();
        }
      }
      updateTeachBar();
      typingLabel.textContent = 'Got it!';
      playAvatarState('yes', false);
      setTimeout(() => { typingIndicator.style.visibility = 'hidden'; }, 900);
    } catch (err) {
      addMessage('tutor', err.message || "Something went wrong reaching the tutor. Please try again.");
      console.error(err);
      typingLabel.textContent = 'Something went wrong';
      playAvatarState('alert', false);
      setTimeout(() => { typingIndicator.style.visibility = 'hidden'; }, 900);
    } finally {
      sendBtn.disabled = false;
      questionInput.focus();
    }
  }

  /* ---------------- Explain it differently ---------------- */
  function removeExplainDifferentlyButton() {
    const existing = document.getElementById('explainDifferentlyWrap');
    if (existing) existing.remove();
  }

  function showExplainDifferentlyButton() {
    removeExplainDifferentlyButton();
    const wrap = document.createElement('div');
    wrap.id = 'explainDifferentlyWrap';
    wrap.className = 'explain-differently-wrap';
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.id = 'explainDifferentlyBtn';
    btn.textContent = '🔄 Explain it differently';
    wrap.appendChild(btn);
    notebook.appendChild(wrap);
    notebook.scrollTop = notebook.scrollHeight;
    btn.addEventListener('click', handleExplainDifferently);
  }

  async function handleExplainDifferently() {
    removeExplainDifferentlyButton();
    addMessage('user', '🔄 Explain it differently');
    sendBtn.disabled = true;
    typingIndicator.style.visibility = 'visible';
    typingLabel.textContent = 'Finding another way to explain it...';
    playAvatarState('thinking', true);
    try {
      const result = await askTutor(
        'Please explain your previous answer again, but differently this time — use a different approach, a simpler everyday analogy, or a different example than you used last time.',
        null
      );
      addMessage('tutor', result.answer);
      saveChatProgress();
      showExplainDifferentlyButton();
      typingLabel.textContent = 'Got it!';
      playAvatarState('yes', false);
      setTimeout(() => { typingIndicator.style.visibility = 'hidden'; }, 900);
    } catch (err) {
      addMessage('tutor', err.message || "Something went wrong reaching the tutor. Please try again.");
      typingLabel.textContent = 'Something went wrong';
      playAvatarState('alert', false);
      setTimeout(() => { typingIndicator.style.visibility = 'hidden'; }, 900);
    } finally {
      sendBtn.disabled = false;
    }
  }

  sendBtn.addEventListener('click', handleSend);
  questionInput.addEventListener('keydown', (e) => { if (e.key === 'Enter') handleSend(); });

  /* ---------------- Voice input ---------------- */
  (function setupVoiceInput() {
    const micBtn = document.getElementById('micBtn');
    const SpeechRecognitionAPI = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SpeechRecognitionAPI || !micBtn) return; // not supported on this browser — leave button hidden

    micBtn.hidden = false;
    const recognition = new SpeechRecognitionAPI();
    recognition.lang = 'en-KE';
    recognition.interimResults = true;
    recognition.continuous = false;
    let listening = false;

    recognition.addEventListener('result', (event) => {
      let transcript = '';
      for (let i = 0; i < event.results.length; i++) {
        transcript += event.results[i][0].transcript;
      }
      questionInput.value = transcript;
    });

    recognition.addEventListener('end', () => {
      listening = false;
      micBtn.classList.remove('listening');
    });

    recognition.addEventListener('error', () => {
      listening = false;
      micBtn.classList.remove('listening');
    });

    micBtn.addEventListener('click', () => {
      if (listening) {
        recognition.stop();
        return;
      }
      questionInput.value = '';
      questionInput.focus();
      try {
        recognition.start();
        listening = true;
        micBtn.classList.add('listening');
      } catch (_) { /* already started — ignore */ }
    });
  })();


  /* ---------------- Composer "+" menu: attach file, camera, study goal ---------------- */
  const toolsBtn = document.getElementById('toolsBtn');
  const toolsMenu = document.getElementById('toolsMenu');
  const attachFileBtn = document.getElementById('attachFileBtn');
  const cameraBtn = document.getElementById('cameraBtn');
  const goalBtn = document.getElementById('goalBtn');
  const goalPopover = document.getElementById('goalPopover');
  const goalInput = document.getElementById('goalInput');
  const saveGoalBtn = document.getElementById('saveGoalBtn');
  const fileInput = document.getElementById('fileInput');
  const cameraInput = document.getElementById('cameraInput');
  const attachmentPreview = document.getElementById('attachmentPreview');
  const attachmentThumb = document.getElementById('attachmentThumb');
  const attachmentName = document.getElementById('attachmentName');
  const removeAttachmentBtn = document.getElementById('removeAttachmentBtn');
  const goalBanner = document.getElementById('goalBanner');

  let pendingAttachment = null; // { data: 'data:image/...;base64,...', type: 'image/png', name: '...' }

  function closeToolsMenu() {
    toolsMenu.hidden = true;
    toolsBtn.setAttribute('aria-expanded', 'false');
  }
  function closeGoalPopover() { goalPopover.hidden = true; }

  toolsBtn.addEventListener('click', (e) => {
    e.stopPropagation();
    const willOpen = toolsMenu.hidden;
    closeGoalPopover();
    toolsMenu.hidden = !willOpen;
    toolsBtn.setAttribute('aria-expanded', String(willOpen));
  });

  document.addEventListener('click', (e) => {
    if (!toolsMenu.hidden && !toolsMenu.contains(e.target) && e.target !== toolsBtn) closeToolsMenu();
    if (!goalPopover.hidden && !goalPopover.contains(e.target) && e.target !== goalBtn) closeGoalPopover();
  });
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') { closeToolsMenu(); closeGoalPopover(); }
  });

  attachFileBtn.addEventListener('click', () => { closeToolsMenu(); fileInput.click(); });
  cameraBtn.addEventListener('click', () => { closeToolsMenu(); cameraInput.click(); });

  function todayKey() { return new Date().toISOString().slice(0, 10); }

  function getTodayGoal() {
    try {
      const raw = JSON.parse(localStorage.getItem('clavi_daily_goal') || 'null');
      if (raw && raw.date === todayKey()) return raw.text;
    } catch (_) {}
    return null;
  }

  function renderGoalBanner() {
    const g = getTodayGoal();
    if (g) {
      goalBanner.textContent = "🎯 Today's focus: " + g;
      goalBanner.hidden = false;
    } else {
      goalBanner.hidden = true;
    }
  }

  goalBtn.addEventListener('click', (e) => {
    e.stopPropagation();
    closeToolsMenu();
    const existing = getTodayGoal();
    goalInput.value = existing || '';
    goalPopover.hidden = !goalPopover.hidden;
    if (!goalPopover.hidden) goalInput.focus();
  });

  saveGoalBtn.addEventListener('click', () => {
    const text = goalInput.value.trim();
    if (!text) { goalInput.focus(); return; }
    localStorage.setItem('clavi_daily_goal', JSON.stringify({ date: todayKey(), text }));
    renderGoalBanner();
    closeGoalPopover();
  });
  goalInput.addEventListener('keydown', (e) => { if (e.key === 'Enter') saveGoalBtn.click(); });

  renderGoalBanner();

  const ACCEPTED_IMAGE_TYPES = ['image/jpeg', 'image/png', 'image/webp'];
  const MAX_IMAGE_BYTES = 5 * 1024 * 1024;

  function clearAttachment() {
    pendingAttachment = null;
    attachmentPreview.classList.remove('visible');
    attachmentThumb.removeAttribute('src');
    attachmentThumb.style.display = '';
    attachmentName.textContent = '';
  }

  function showAttachmentPreview(name, thumbSrc) {
    if (thumbSrc) {
      attachmentThumb.src = thumbSrc;
      attachmentThumb.style.display = '';
    } else {
      attachmentThumb.removeAttribute('src');
      attachmentThumb.style.display = 'none';
    }
    attachmentName.textContent = name;
    attachmentPreview.classList.add('visible');
  }

  function handleImageFile(file) {
    if (!ACCEPTED_IMAGE_TYPES.includes(file.type)) {
      addMessage('tutor', 'Please attach a JPG, PNG, or WEBP image.');
      return;
    }
    if (file.size > MAX_IMAGE_BYTES) {
      addMessage('tutor', 'That image is too large. Please choose one under 5 MB.');
      return;
    }
    const reader = new FileReader();
    reader.onload = () => {
      pendingAttachment = { data: reader.result, type: file.type, name: file.name };
      showAttachmentPreview(file.name, reader.result);
    };
    reader.readAsDataURL(file);
  }

  function handleTextFile(file) {
    const reader = new FileReader();
    reader.onload = () => {
      const content = String(reader.result || '').slice(0, 4000);
      const prefix = questionInput.value ? questionInput.value + '\n\n' : '';
      questionInput.value = prefix + `[Attached: ${file.name}]\n` + content;
      questionInput.focus();
    };
    reader.readAsText(file);
  }

  fileInput.addEventListener('change', () => {
    const file = fileInput.files && fileInput.files[0];
    fileInput.value = '';
    if (!file) return;
    if (file.type.startsWith('image/')) handleImageFile(file);
    else handleTextFile(file);
  });

  cameraInput.addEventListener('change', () => {
    const file = cameraInput.files && cameraInput.files[0];
    cameraInput.value = '';
    if (file) handleImageFile(file);
  });

  removeAttachmentBtn.addEventListener('click', clearAttachment);

  /* ---------------- Book Finder ---------------- */
  const bookQuery = document.getElementById('bookQuery');
  const bookSearchBtn = document.getElementById('bookSearchBtn');
  const bookGrid = document.getElementById('bookGrid');
  const bookStatus = document.getElementById('bookStatus');

  async function searchBooks() {
    const q = bookQuery.value.trim();
    if (!q) return;

    bookGrid.innerHTML = '';
    bookStatus.style.display = 'block';
    bookStatus.textContent = 'Searching...';

    try {
      const res = await fetch(`https://openlibrary.org/search.json?q=${encodeURIComponent(q)}&limit=18`);
      if (!res.ok) throw new Error('Search failed');
      const data = await res.json();
      const docs = data.docs || [];

      if (docs.length === 0) {
        bookStatus.textContent = 'No books found — try a different search.';
        return;
      }

      bookStatus.style.display = 'none';
      bookGrid.innerHTML = docs.map(book => {
        const title = book.title || 'Untitled';
        const author = (book.author_name && book.author_name[0]) || 'Unknown author';
        const cover = book.cover_i
          ? `https://covers.openlibrary.org/b/id/${book.cover_i}-M.jpg`
          : 'https://via.placeholder.com/150x220?text=No+Cover';
        const link = `https://openlibrary.org${book.key}`;
        return `
          <a class="book-card" href="${link}" target="_blank" rel="noopener">
            <img class="book-cover" src="${cover}" alt="${title}" loading="lazy" />
            <div class="book-info">
              <div class="book-title">${title}</div>
              <div class="book-author">${author}</div>
            </div>
          </a>
        `;
      }).join('');
    } catch (err) {
      bookStatus.style.display = 'block';
      bookStatus.textContent = 'Something went wrong searching for books. Please try again.';
      console.error(err);
    }
  }

  bookSearchBtn.addEventListener('click', searchBooks);
  bookQuery.addEventListener('keydown', (e) => { if (e.key === 'Enter') searchBooks(); });

  /* ---------------- Topics & Tests ---------------- */
  const topicsNavBtn = document.getElementById('topicsNavBtn');
  const topicsBackBtn = document.getElementById('topicsBackBtn');
  const examBackBtn = document.getElementById('examBackBtn');
  const topicList = document.getElementById('topicList');
  const topicsLoading = document.getElementById('topicsLoading');
  const topicsSubjectLabel = document.getElementById('topicsSubjectLabel');
  const topicsLevelLabel = document.getElementById('topicsLevelLabel');
  const finalExamProgress = document.getElementById('finalExamProgress');
  const finalExamBtn = document.getElementById('finalExamBtn');
  const finalExamCard = document.getElementById('finalExamCard');
  const examLoading = document.getElementById('examLoading');
  const examTakingView = document.getElementById('examTakingView');
  const examResultsView = document.getElementById('examResultsView');

  let currentTopics = [];
  let currentTestData = null;
  let currentExamTopic = null; // null means final exam
  let currentExamIsFinal = false;

  function levelLabelText() {
    return currentLevel === 'cbc'
      ? `CBC — ${currentGradeForm}`
      : currentLevel === 'upper_primary'
        ? `Upper Primary — ${currentGradeForm}`
        : currentLevel === 'junior_school'
          ? `Junior School — ${currentGradeForm}`
          : currentLevel === 'senior_school'
            ? `Senior School — ${currentGradeForm}`
            : currentLevel === 'campus'
              ? `Campus — ${currentGradeForm}`
              : `Secondary — ${currentGradeForm} (KCSE)`;
  }

  function progressKey() {
    return `${currentSubject}|${currentLevel}|${currentGradeForm}`;
  }

  function getAllProgress() {
    const raw = localStorage.getItem('clavi_topic_progress');
    return raw ? JSON.parse(raw) : {};
  }

  function saveTopicResult(topicName, totalMarks, maxMarks) {
    const all = getAllProgress();
    const key = progressKey();
    if (!all[key]) all[key] = {};
    const passed = (totalMarks / maxMarks) >= 0.7;
    all[key][topicName] = { done: passed, score: `${totalMarks}/${maxMarks}` };
    localStorage.setItem('clavi_topic_progress', JSON.stringify(all));
    return passed;
  }

  function getTopicsCache() {
    const raw = localStorage.getItem('clavi_topics_cache');
    return raw ? JSON.parse(raw) : {};
  }

  function saveTopicsCache(topics) {
    const cache = getTopicsCache();
    cache[progressKey()] = topics;
    localStorage.setItem('clavi_topics_cache', JSON.stringify(cache));
  }

  topicsNavBtn.addEventListener('click', () => { teachPickerMode = false; openTopicsScreen(); });
  topicsBackBtn.addEventListener('click', () => { teachPickerMode = false; showScreen('chatScreen'); });
  examBackBtn.addEventListener('click', () => showScreen('topicsScreen'));

  async function openTopicsScreen(pickerMode) {
    teachPickerMode = !!pickerMode;
    topicsSubjectLabel.textContent = currentSubject;
    topicsLevelLabel.textContent = levelLabelText();
    finalExamCard.style.display = teachPickerMode ? 'none' : '';
    showScreen('topicsScreen');

    if (teachPickerMode) {
      renderTopicList();
      return;
    }

    const cache = getTopicsCache();
    const cached = cache[progressKey()];
    if (cached) {
      currentTopics = cached;
      renderTopicList();
      return;
    }

    topicList.innerHTML = '';
    topicsLoading.style.display = 'block';

    try {
      const res = await fetch('/api/topics', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ subject: currentSubject, level: currentLevel, gradeForm: currentGradeForm })
      });
      if (!res.ok) {
        let errMsg = 'Failed to load topics';
        try { const errData = await res.json(); if (errData.error) errMsg = errData.error; } catch (_) {}
        throw new Error(errMsg);
      }
      const data = await res.json();
      currentTopics = data.topics || [];
      saveTopicsCache(currentTopics);
      renderTopicList();
    } catch (err) {
      topicList.innerHTML = `<div class="empty-dash">${err.message || 'Could not load topics right now. Please try again.'}</div>`;
      console.error(err);
    } finally {
      topicsLoading.style.display = 'none';
    }
  }

  function renderTopicList() {
    if (teachPickerMode) {
      renderTeachPickerList();
      return;
    }
    const all = getAllProgress();
    const progress = all[progressKey()] || {};

    topicList.innerHTML = currentTopics.map(topic => {
      const entry = progress[topic];
      const done = entry && entry.done;
      return `
        <div class="topic-row">
          <div class="topic-row-left">
            <div class="topic-badge ${done ? 'done' : ''}">${done ? '✓' : ''}</div>
            <div>
              <div class="topic-name">${topic}${entry && entry.taught ? ' 📖' : ''}</div>
              ${entry ? `<div class="topic-score">Last score: ${entry.score}</div>` : ''}
            </div>
          </div>
          <button class="topic-test-btn" data-topic="${topic}">${entry ? 'Retake test' : 'Take test'}</button>
        </div>
      `;
    }).join('');

    topicList.querySelectorAll('.topic-test-btn').forEach(btn => {
      btn.addEventListener('click', () => openExamScreen(btn.dataset.topic));
    });

    const completedCount = currentTopics.filter(t => progress[t] && progress[t].done).length;
    finalExamProgress.textContent = `${completedCount}/${currentTopics.length} topics completed`;
    finalExamBtn.disabled = completedCount < currentTopics.length;
  }

  function renderTeachPickerList() {
    const all = getAllProgress();
    const progress = all[progressKey()] || {};
    const startBtn = `<button type="button" class="teach-pick-btn" id="teachPickBeginning">▶ Start from the beginning</button>`;
    const rows = currentTopics.map((topic, idx) => {
      const entry = progress[topic];
      const taught = entry && entry.taught;
      return `
        <div class="topic-row">
          <div class="topic-row-left">
            <div class="topic-badge ${taught ? 'done' : ''}">${taught ? '📖' : ''}</div>
            <div class="topic-name">${topic}</div>
          </div>
          <button class="topic-test-btn" data-idx="${idx}">Start here</button>
        </div>
      `;
    }).join('');
    topicList.innerHTML = startBtn + rows;

    document.getElementById('teachPickBeginning').addEventListener('click', () => {
      teachPickerMode = false;
      showScreen('chatScreen');
      startTeachingTopic(currentTopics, 0);
    });
    topicList.querySelectorAll('.topic-test-btn').forEach(btn => {
      btn.addEventListener('click', () => {
        teachPickerMode = false;
        showScreen('chatScreen');
        startTeachingTopic(currentTopics, parseInt(btn.dataset.idx, 10));
      });
    });
  }

  finalExamBtn.addEventListener('click', () => openExamScreen(null));

  async function openExamScreen(topic) {
    currentExamTopic = topic;
    currentExamIsFinal = !topic;
    showScreen('examScreen');
    examLoading.style.display = 'block';
    examTakingView.style.display = 'none';
    examResultsView.style.display = 'none';
    examTakingView.innerHTML = '';
    examResultsView.innerHTML = '';

    try {
      const res = await fetch('/api/generate-test', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          subject: currentSubject,
          level: currentLevel,
          gradeForm: currentGradeForm,
          topic: topic,
          allTopics: currentTopics
        })
      });
      if (!res.ok) {
        let errMsg = 'Failed to generate test';
        try { const errData = await res.json(); if (errData.error) errMsg = errData.error; } catch (_) {}
        throw new Error(errMsg);
      }
      currentTestData = await res.json();
      renderExamPaper();
      examLoading.style.display = 'none';
      examTakingView.style.display = 'block';
    } catch (err) {
      examLoading.innerHTML = err.message || 'Could not generate the test right now. Please go back and try again.';
      console.error(err);
    }
  }

  function renderExamPaper() {
    const test = currentTestData;
    const paperTitle = currentExamIsFinal ? 'Final Subject Exam — All Topics' : currentExamTopic;

    const questionsHtml = test.questions.map(q => {
      const marksLabel = `(${q.marks} mk${q.marks > 1 ? 's' : ''})`;
      const { text: qText, diagramHtml } = extractDiagramHtml(q.question);
      if (q.type === 'mcq') {
        const optionsHtml = Object.entries(q.options).map(([letter, text]) => `
          <label class="exam-option">
            <input type="radio" name="q_${q.id}" value="${letter}" />
            <span><strong>${letter}.</strong> ${text}</span>
          </label>
        `).join('');
        return `
          <div class="exam-question">
            <div class="exam-question-text">${q.id}. ${qText} <span class="exam-question-marks">${marksLabel}</span></div>
            ${diagramHtml}
            <div class="exam-options">${optionsHtml}</div>
          </div>
        `;
      } else {
        return `
          <div class="exam-question exam-short-answer">
            <div class="exam-question-text">${q.id}. ${qText} <span class="exam-question-marks">${marksLabel}</span></div>
            ${diagramHtml}
            <textarea id="short_${q.id}" placeholder="Write your answer..."></textarea>
          </div>
        `;
      }
    }).join('');

    examTakingView.innerHTML = `
      <div class="exam-paper">
        <div class="exam-paper-header">
          <div class="exam-subject">${currentSubject}</div>
          <div class="exam-meta">${paperTitle} — ${levelLabelText()} — Total: ${test.total_marks} marks</div>
        </div>
        <div class="exam-instructions">Answer ALL questions.</div>
        ${questionsHtml}
        <button class="exam-submit-btn" id="submitExamBtn">Submit Test</button>
      </div>
    `;

    document.getElementById('submitExamBtn').addEventListener('click', handleSubmitExam);
    renderMath(examTakingView);
  }

  function handleSubmitExam() {
    const test = currentTestData;
    let autoMarks = 0;

    const itemsHtml = test.questions.map(q => {
      const { text: qText, diagramHtml } = extractDiagramHtml(q.question);
      if (q.type === 'mcq') {
        const selected = document.querySelector(`input[name="q_${q.id}"]:checked`);
        const selectedValue = selected ? selected.value : null;
        const correct = selectedValue === q.correct_option;
        if (correct) autoMarks += q.marks;

        return `
          <div class="marking-item" data-marks="${correct ? q.marks : 0}" data-max="${q.marks}">
            <div class="marking-question">${q.id}. ${qText}</div>
            ${diagramHtml}
            <div class="marking-row">Your answer: <strong>${selectedValue || '(not answered)'}</strong></div>
            <div class="marking-row ${correct ? 'marking-correct' : 'marking-wrong'}">
              Correct answer: <strong>${q.correct_option}</strong> ${correct ? '✓' : '✗'}
            </div>
            <div class="marking-row">${q.explanation}</div>
            <div class="marking-row"><strong>${correct ? q.marks : 0} / ${q.marks} marks</strong></div>
          </div>
        `;
      } else {
        const studentAnswer = document.getElementById(`short_${q.id}`).value.trim() || '(not answered)';
        const pointsHtml = q.marking_points.map(p => `<li>${p}</li>`).join('');
        return `
          <div class="marking-item" data-marks="0" data-max="${q.marks}" data-self-mark-max="${q.marks}">
            <div class="marking-question">${q.id}. ${qText}</div>
            ${diagramHtml}
            <div class="marking-row"><strong>Your answer:</strong> ${studentAnswer}</div>
            <div class="marking-row"><strong>Model answer:</strong> ${q.model_answer}</div>
            <div class="marking-row">Marking points:</div>
            <ul class="marking-points-list">${pointsHtml}</ul>
            <div class="self-mark-row">
              <label for="selfmark_${q.id}">Marks you'd award yourself:</label>
              <input type="number" id="selfmark_${q.id}" class="self-mark-input" min="0" max="${q.marks}" value="0" data-max="${q.marks}" />
              <span>/ ${q.marks}</span>
            </div>
          </div>
        `;
      }
    }).join('');

    examResultsView.innerHTML = `
      <div class="result-summary">
        <div class="result-score" id="resultScoreDisplay">${autoMarks} / ${test.total_marks}</div>
        <div class="result-percent" id="resultPercentDisplay">${Math.round((autoMarks / test.total_marks) * 100)}%</div>
        <div class="result-verdict retry" id="resultVerdict">Enter your self-marks below, then finish</div>
      </div>
      ${itemsHtml}
      <div class="exam-actions">
        <button class="primary" id="finishExamBtn">Finish &amp; Save Result</button>
      </div>
    `;

    examTakingView.style.display = 'none';
    examResultsView.style.display = 'block';
    renderMath(examResultsView);

    const selfMarkInputs = examResultsView.querySelectorAll('.self-mark-input');
    function recalcTotal() {
      let total = autoMarks;
      selfMarkInputs.forEach(inp => {
        let val = parseInt(inp.value, 10) || 0;
        const max = parseInt(inp.dataset.max, 10);
        if (val > max) { val = max; inp.value = max; }
        if (val < 0) { val = 0; inp.value = 0; }
        total += val;
      });
      document.getElementById('resultScoreDisplay').textContent = `${total} / ${test.total_marks}`;
      document.getElementById('resultPercentDisplay').textContent = `${Math.round((total / test.total_marks) * 100)}%`;
      return total;
    }

    selfMarkInputs.forEach(inp => inp.addEventListener('input', recalcTotal));

    document.getElementById('finishExamBtn').onclick = () => {
      const finalTotal = recalcTotal();
      const percent = Math.round((finalTotal / test.total_marks) * 100);
      const verdictEl = document.getElementById('resultVerdict');
      const passed = percent >= 70;
      verdictEl.textContent = passed ? 'PASS ✓' : 'Below 70% — consider revising and retaking';
      verdictEl.className = 'result-verdict ' + (passed ? 'pass' : 'retry');

      if (!currentExamIsFinal) {
        saveTopicResult(currentExamTopic, finalTotal, test.total_marks);
      }
      recordQuestion(currentSubject, 3);

      document.getElementById('finishExamBtn').textContent = 'Saved ✓ — Back to Topics';
      document.getElementById('finishExamBtn').onclick = () => {
        renderTopicList();
        showScreen('topicsScreen');
      };
    };
  }

  /* ---------------- Exam Papers (formal, real-paper style) ---------------- */
  const KCSE_PAPER_COUNTS = {
    'Mathematics': 2, 'English': 3, 'Kiswahili': 3, 'Physics': 3, 'Chemistry': 3, 'Biology': 3,
    'Geography': 2, 'History': 2, 'Religious Education': 2, 'Agriculture': 2, 'Home Science': 2,
    'Computer Studies': 2, 'Business Studies': 1, 'Creative Arts': 1
  };
  // Most university units are a single final exam paper. A few broad units
  // are commonly split into two distinct areas, so give those two mock
  // papers instead of lumping everything into one.
  const CAMPUS_PAPER_COUNTS = {
    'Law': 2, 'Accounting': 2, 'Statistics': 2, 'Economics': 2, 'Finance': 2
  };
  function paperCountFor(subject, level) {
    if (level === 'campus') return CAMPUS_PAPER_COUNTS[subject] || 1;
    if (level !== 'secondary' && level !== 'senior_school') return 1;
    return KCSE_PAPER_COUNTS[subject] || 1;
  }

  let papersLevel = ACCOUNT_TRACK === 'campus' ? 'campus' : 'secondary';
  let papersGradeForm = ACCOUNT_TRACK === 'campus' ? 'Year 1' : 'Form 1';
  let papersSubject = null;
  let papersPaperNum = null;
  let papersPaperCount = null;
  let papersTestData = null;
  let papersStudentName = '';

  const papersSetupView = document.getElementById('papersSetupView');
  const paperListView = document.getElementById('paperListView');
  const paperNameGateView = document.getElementById('paperNameGateView');
  const paperTakingView = document.getElementById('paperTakingView');
  const paperResultsView = document.getElementById('paperResultsView');
  const myResultsView = document.getElementById('myResultsView');
  const papersLevelToggle = document.getElementById('papersLevelToggle');
  const papersGradeFormSelect = document.getElementById('papersGradeFormSelect');
  const papersSubjectGrid = document.getElementById('papersSubjectGrid');

  function papersShowOnly(view) {
    [papersSetupView, paperListView, paperNameGateView, paperTakingView, paperResultsView, myResultsView]
      .forEach(v => { if (v) v.style.display = (v === view ? 'block' : 'none'); });
  }

  function renderPapersSubjectGrid() {
    const subjects = (SUBJECTS_BY_LEVEL[papersLevel] || []);
    papersSubjectGrid.innerHTML = subjects.map(subj => {
      const count = paperCountFor(subj, papersLevel);
      return `
        <div class="paper-list-card" data-subject="${subj}">
          <div class="paper-list-title">${subj}</div>
          <div class="paper-list-sub">${count} paper${count > 1 ? 's' : ''}</div>
        </div>
      `;
    }).join('');
    papersSubjectGrid.querySelectorAll('[data-subject]').forEach(card => {
      card.addEventListener('click', () => openPaperList(card.dataset.subject));
    });
  }

  if (papersLevelToggle) {
    papersLevelToggle.addEventListener('click', (e) => {
      const btn = e.target.closest('button');
      if (!btn) return;
      papersLevel = btn.dataset.level;
      [...papersLevelToggle.children].forEach(b => b.classList.toggle('active', b === btn));
      const papersSubLevelLabel = document.getElementById('papersSubLevelLabel');
      if (papersSubLevelLabel) papersSubLevelLabel.textContent = subLevelLabelFor(papersLevel);
      populateGradeFormSelect(papersGradeFormSelect, papersLevel, null);
      papersGradeForm = papersGradeFormSelect.value;
      renderPapersSubjectGrid();
    });
  }

  if (papersGradeFormSelect) {
    papersGradeFormSelect.addEventListener('change', (e) => {
      papersGradeForm = e.target.value;
    });
  }

  function openPaperList(subject) {
    papersSubject = subject;
    papersPaperCount = paperCountFor(subject, papersLevel);
    document.getElementById('paperListTitle').textContent = subject;
    const grid = document.getElementById('paperListGrid');
    let html = '';
    for (let i = 1; i <= papersPaperCount; i++) {
      html += `
        <div class="paper-list-card" data-paper="${i}" style="display:flex; justify-content:space-between; align-items:center;">
          <div>
            <div class="paper-list-title">Paper ${i}</div>
            <div class="paper-list-sub">${papersLevelLabel()} — ${papersGradeForm}</div>
          </div>
          <span style="color:var(--gold); font-size:20px;">→</span>
        </div>
      `;
    }
    grid.innerHTML = html;
    grid.querySelectorAll('[data-paper]').forEach(card => {
      card.addEventListener('click', () => openNameGate(parseInt(card.dataset.paper, 10)));
    });
    papersShowOnly(paperListView);
  }

  function papersLevelLabel() {
    const map = { cbc: 'CBC', upper_primary: 'Upper Primary', junior_school: 'Junior School', secondary: 'Secondary (KCSE)', senior_school: 'Senior School', campus: 'Campus' };
    return map[papersLevel] || papersLevel;
  }

  function openNameGate(paperNum) {
    papersPaperNum = paperNum;
    document.getElementById('paperGateSubtitle').textContent =
      `${papersSubject} — Paper ${paperNum}${papersPaperCount > 1 ? ' of ' + papersPaperCount : ''} — ${papersGradeForm}`;
    const nameInput = document.getElementById('paperStudentNameInput');
    nameInput.value = localStorage.getItem('clavi_user_name') || '';
    papersShowOnly(paperNameGateView);
  }

  document.getElementById('paperListBackBtn').addEventListener('click', () => papersShowOnly(papersSetupView));
  document.getElementById('paperNameGateBackBtn').addEventListener('click', () => papersShowOnly(paperListView));
  document.getElementById('myResultsBackBtn').addEventListener('click', () => papersShowOnly(papersSetupView));

  document.getElementById('startPaperBtn').addEventListener('click', async () => {
    const nameInput = document.getElementById('paperStudentNameInput');
    papersStudentName = nameInput.value.trim() || 'Student';
    papersShowOnly(paperTakingView);
    paperTakingView.innerHTML = '<div class="loading-block">Building your paper...</div>';
    try {
      const res = await fetch('/api/generate-test', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          subject: papersSubject,
          level: papersLevel,
          gradeForm: papersGradeForm,
          paper: papersPaperNum,
          paperCount: papersPaperCount
        })
      });
      if (!res.ok) {
        const errData = await res.json().catch(() => ({}));
        throw new Error(errData.error || 'Could not generate this paper. Please try again.');
      }
      papersTestData = await res.json();
      renderFormalPaper();
    } catch (err) {
      paperTakingView.innerHTML = `<div class="loading-block">${err.message || 'Something went wrong. Please try again.'}</div>`;
    }
  });

  function renderFormalPaper() {
    const test = papersTestData;
    const sections = {};
    const sectionOrder = [];
    test.questions.forEach(q => {
      const sec = q.section || '';
      if (!(sec in sections)) { sections[sec] = []; sectionOrder.push(sec); }
      sections[sec].push(q);
    });

    const renderQuestion = (q) => {
      const marksLabel = `(${q.marks} mk${q.marks > 1 ? 's' : ''})`;
      const { text: qText, diagramHtml } = extractDiagramHtml(q.question);
      if (q.type === 'mcq') {
        const optionsHtml = Object.entries(q.options).map(([letter, text]) => `
          <label class="exam-option">
            <input type="radio" name="pq_${q.id}" value="${letter}" />
            <span><strong>${letter}.</strong> ${text}</span>
          </label>
        `).join('');
        return `
          <div class="exam-question">
            <div class="exam-question-text">${q.id}. ${qText} <span class="exam-question-marks">${marksLabel}</span></div>
            ${diagramHtml}
            <div class="exam-options">${optionsHtml}</div>
          </div>
        `;
      } else {
        return `
          <div class="exam-question exam-short-answer">
            <div class="exam-question-text">${q.id}. ${qText} <span class="exam-question-marks">${marksLabel}</span></div>
            ${diagramHtml}
            <textarea id="pshort_${q.id}" placeholder="Write your answer..."></textarea>
          </div>
        `;
      }
    };

    const questionsHtml = sectionOrder.map(sec => `
      ${sec ? `<div class="paper-section-title">${sec}</div>` : ''}
      ${sections[sec].map(renderQuestion).join('')}
    `).join('');

    const instructions = (test.instructions && test.instructions.length)
      ? test.instructions
      : ['Write your name in the space provided.', 'Answer ALL questions.'];
    const instructionsHtml = `<ul class="paper-instructions-list">${instructions.map(i => `<li>${i}</li>`).join('')}</ul>`;
    const timeAllowed = test.time_allowed ? `<div class="paper-meta-row"><strong>Time:</strong> ${test.time_allowed}</div>` : '';
    const today = new Date().toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: 'numeric' });

    paperTakingView.innerHTML = `
      <div class="exam-paper printable-paper">
        <div class="paper-header">
          <img src="/static/images/logo.png" alt="Clavis" class="paper-logo" />
          <div class="paper-header-name">CLAVIS</div>
          <div class="paper-header-sub">Practice Paper — styled on the ${papersLevelLabel()} exam format</div>
          <div class="paper-header-title">${papersSubject} — Paper ${papersPaperNum}${papersPaperCount > 1 ? ' of ' + papersPaperCount : ''}</div>
          <div class="paper-header-sub">${papersLevelLabel()} — ${papersGradeForm} &nbsp;•&nbsp; Total: ${test.total_marks} marks</div>
          <div class="paper-meta-grid">
            <div class="paper-meta-row"><strong>Name:</strong> ${papersStudentName}</div>
            <div class="paper-meta-row"><strong>Date:</strong> ${today}</div>
            ${timeAllowed}
          </div>
        </div>
        <div class="exam-instructions">
          <div class="paper-instructions-title">Instructions to candidates</div>
          ${instructionsHtml}
        </div>
        ${questionsHtml}
        <button type="button" id="downloadPaperBtn" style="width:100%; background:var(--paper); border:1px solid var(--line); color:var(--chalk); font-weight:600; padding:12px 20px; border-radius:8px; cursor:pointer; margin-bottom:10px;">🖨️ Download / Print PDF</button>
        <button class="exam-submit-btn" id="submitPaperBtn">Submit Paper</button>
      </div>
    `;
    document.getElementById('submitPaperBtn').addEventListener('click', handleSubmitFormalPaper);
    document.getElementById('downloadPaperBtn').addEventListener('click', () => window.print());
    renderMath(paperTakingView);
  }

  function saveFormalPaperResult(finalTotal, totalMarks) {
    const entry = {
      subject: papersSubject,
      level: papersLevel,
      gradeForm: papersGradeForm,
      paper: papersPaperNum,
      paperCount: papersPaperCount,
      score: finalTotal,
      total: totalMarks,
      percent: Math.round((finalTotal / totalMarks) * 100),
      studentName: papersStudentName,
      date: new Date().toISOString()
    };
    let log = [];
    try { log = JSON.parse(localStorage.getItem('clavi_paper_results') || '[]'); } catch (_) { log = []; }
    log.unshift(entry);
    log = log.slice(0, 50); // keep a reasonable local history
    localStorage.setItem('clavi_paper_results', JSON.stringify(log));

    fetch('/api/exam-results', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(entry)
    }).catch(() => { /* saved locally either way — not lost, just not synced yet */ });

    if (typeof renderOnboardingChecklist === 'function') renderOnboardingChecklist();
  }

  function handleSubmitFormalPaper() {
    const test = papersTestData;
    let autoMarks = 0;

    const itemsHtml = test.questions.map(q => {
      const { text: qText, diagramHtml } = extractDiagramHtml(q.question);
      if (q.type === 'mcq') {
        const selected = document.querySelector(`input[name="pq_${q.id}"]:checked`);
        const selectedValue = selected ? selected.value : null;
        const correct = selectedValue === q.correct_option;
        if (correct) autoMarks += q.marks;
        return `
          <div class="marking-item" data-marks="${correct ? q.marks : 0}" data-max="${q.marks}">
            <div class="marking-question">${q.id}. ${qText}</div>
            ${diagramHtml}
            <div class="marking-row">Your answer: <strong>${selectedValue || '(not answered)'}</strong></div>
            <div class="marking-row ${correct ? 'marking-correct' : 'marking-wrong'}">
              Correct answer: <strong>${q.correct_option}</strong> ${correct ? '✓' : '✗'}
            </div>
            <div class="marking-row">${q.explanation}</div>
            <div class="marking-row"><strong>${correct ? q.marks : 0} / ${q.marks} marks</strong></div>
          </div>
        `;
      } else {
        const studentAnswer = document.getElementById(`pshort_${q.id}`).value.trim() || '(not answered)';
        const pointsHtml = q.marking_points.map(p => `<li>${p}</li>`).join('');
        return `
          <div class="marking-item" data-marks="0" data-max="${q.marks}" data-self-mark-max="${q.marks}">
            <div class="marking-question">${q.id}. ${qText}</div>
            ${diagramHtml}
            <div class="marking-row"><strong>Your answer:</strong> ${studentAnswer}</div>
            <div class="marking-row"><strong>Model answer:</strong> ${q.model_answer}</div>
            <div class="marking-row">Marking points:</div>
            <ul class="marking-points-list">${pointsHtml}</ul>
            <div class="self-mark-row">
              <label for="pselfmark_${q.id}">Marks you'd award yourself:</label>
              <input type="number" id="pselfmark_${q.id}" class="self-mark-input" min="0" max="${q.marks}" value="0" data-max="${q.marks}" />
              <span>/ ${q.marks}</span>
            </div>
          </div>
        `;
      }
    }).join('');

    paperResultsView.innerHTML = `
      <div class="result-summary">
        <div class="result-score" id="paperResultScoreDisplay">${autoMarks} / ${test.total_marks}</div>
        <div class="result-percent" id="paperResultPercentDisplay">${Math.round((autoMarks / test.total_marks) * 100)}%</div>
        <div class="result-verdict retry" id="paperResultVerdict">Enter your self-marks below, then finish</div>
      </div>
      ${itemsHtml}
      <div class="exam-actions">
        <button class="primary" id="finishPaperBtn">Finish &amp; Save Result</button>
      </div>
    `;

    papersShowOnly(paperResultsView);
    renderMath(paperResultsView);

    const selfMarkInputs = paperResultsView.querySelectorAll('.self-mark-input');
    function recalcTotal() {
      let total = autoMarks;
      selfMarkInputs.forEach(inp => {
        let val = parseInt(inp.value, 10) || 0;
        const max = parseInt(inp.dataset.max, 10);
        if (val > max) { val = max; inp.value = max; }
        if (val < 0) { val = 0; inp.value = 0; }
        total += val;
      });
      document.getElementById('paperResultScoreDisplay').textContent = `${total} / ${test.total_marks}`;
      document.getElementById('paperResultPercentDisplay').textContent = `${Math.round((total / test.total_marks) * 100)}%`;
      return total;
    }
    selfMarkInputs.forEach(inp => inp.addEventListener('input', recalcTotal));

    document.getElementById('finishPaperBtn').onclick = () => {
      const finalTotal = recalcTotal();
      const percent = Math.round((finalTotal / test.total_marks) * 100);
      const verdictEl = document.getElementById('paperResultVerdict');
      const passed = percent >= 70;
      verdictEl.textContent = passed ? 'PASS ✓' : 'Below 70% — consider revising and retaking';
      verdictEl.className = 'result-verdict ' + (passed ? 'pass' : 'retry');

      saveFormalPaperResult(finalTotal, test.total_marks);
      recordQuestion(papersSubject, 5);

      document.getElementById('finishPaperBtn').textContent = 'Saved ✓ — Back to Papers';
      document.getElementById('finishPaperBtn').onclick = () => papersShowOnly(papersSetupView);

      const shareBtn = document.createElement('button');
      shareBtn.textContent = '📤 Share my score';
      shareBtn.type = 'button';
      shareBtn.style.cssText = 'margin-top:10px; background:var(--paper); border:1px solid var(--line); color:var(--chalk); font-weight:600; padding:12px 20px; border-radius:8px; cursor:pointer; width:100%;';
      shareBtn.addEventListener('click', () => shareExamResult(papersSubject, papersPaperNum, percent, passed));
      document.querySelector('#paperResultsView .exam-actions').appendChild(shareBtn);
    };
  }

  function shareExamResult(subject, paperNum, percent, passed) {
    const text = passed
      ? `I scored ${percent}% on ${subject} Paper ${paperNum} using Clavis — an AI study tutor for Kenyan students! 🎉`
      : `Just took a ${subject} Paper ${paperNum} practice exam on Clavis and scored ${percent}%. Studying to improve! 📚`;
    const url = window.location.origin;
    if (navigator.share) {
      navigator.share({ text: text + ' ' + url }).catch(() => { /* user cancelled — no action needed */ });
    } else {
      const waUrl = `https://wa.me/?text=${encodeURIComponent(text + ' ' + url)}`;
      window.open(waUrl, '_blank');
    }
  }

  document.getElementById('viewMyResultsBtn').addEventListener('click', () => {
    const list = document.getElementById('myResultsList');
    list.innerHTML = '<div class="empty-dash">Loading your results...</div>';
    papersShowOnly(myResultsView);

    fetch('/api/exam-results')
      .then(res => res.ok ? res.json() : Promise.reject())
      .then(renderMyResultsList)
      .catch(() => {
        // Offline or server hiccup — fall back to whatever is saved locally.
        let log = [];
        try { log = JSON.parse(localStorage.getItem('clavi_paper_results') || '[]'); } catch (_) { log = []; }
        renderMyResultsList(log);
      });
  });

  function renderMyResultsList(log) {
    const list = document.getElementById('myResultsList');
    if (!log || !log.length) {
      list.innerHTML = '<div class="empty-dash">No exam papers completed yet — take one above and your results will show up here.</div>';
      return;
    }
    list.innerHTML = log.map(r => `
      <div class="paper-list-card" style="display:flex; justify-content:space-between; align-items:center;">
        <div>
          <div class="paper-list-title">${r.subject} — Paper ${r.paper}</div>
          <div class="paper-list-sub">${r.gradeForm} — ${new Date(r.date).toLocaleDateString()}</div>
        </div>
        <div style="font-family:'Fraunces',serif; font-weight:700; font-size:18px; color:${r.percent >= 70 ? 'var(--chalk)' : '#B8433A'};">${r.percent}%</div>
      </div>
    `).join('');
  }

  // Initialize the papers screen's grade dropdown + subject grid once the page loads.
  const papersSubLevelLabelInit = document.getElementById('papersSubLevelLabel');
  if (papersSubLevelLabelInit) papersSubLevelLabelInit.textContent = subLevelLabelFor(papersLevel);
  populateGradeFormSelect(papersGradeFormSelect, papersLevel, null);
  papersGradeForm = papersGradeFormSelect.value;
  renderPapersSubjectGrid();

  /* ---------------- Upgrade pricing modal ---------------- */
  const upgradeBtn = document.getElementById('upgradeBtn');
  const pricingModalOverlay = document.getElementById('pricingModalOverlay');
  const pricingModalClose = document.getElementById('pricingModalClose');

  if (upgradeBtn && pricingModalOverlay) {
    upgradeBtn.addEventListener('click', () => pricingModalOverlay.classList.add('open'));
    pricingModalClose.addEventListener('click', () => pricingModalOverlay.classList.remove('open'));
    pricingModalOverlay.addEventListener('click', (e) => {
      if (e.target === pricingModalOverlay) pricingModalOverlay.classList.remove('open');
    });
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') pricingModalOverlay.classList.remove('open');
    });
  }

  /* ---------------- Greeting + returning-user welcome ---------------- */
  function getGreeting() {
    const h = new Date().getHours();
    if (h >= 5 && h < 12) return 'Good morning';
    if (h >= 12 && h < 16) return 'Good afternoon';
    if (h >= 16 && h < 21) return 'Good evening';
    if (h >= 21 || h < 1) return 'Burning the midnight oil';
    return 'Night owl';
  }

  function showWelcomeBackToast(name) {
    const toast = document.getElementById('welcomeBackToast');
    if (!toast) return;
    const greeting = getGreeting();
    toast.textContent = name ? `${greeting}, ${name}! 👋` : `${greeting}! 👋`;
    toast.style.display = 'block';
    toast.style.opacity = '1';
    setTimeout(() => {
      toast.style.transition = 'opacity 0.6s ease';
      toast.style.opacity = '0';
      setTimeout(() => { toast.style.display = 'none'; }, 700);
    }, 3200);
  }

  /* ---------------- First-time welcome overlay / returning greeting ---------------- */
  (function showWelcomeIfFirstVisit() {
    const seen = localStorage.getItem('clavi_visited');
    const savedName = localStorage.getItem('clavi_user_name');

    // The campus/high-school choice is normally made on the public landing
    // page, before signup. Fall back to a ?type= URL param (in case
    // localStorage was blocked) so the choice still carries through.
    const urlType = new URLSearchParams(window.location.search).get('type');
    let studentType = localStorage.getItem('clavi_student_type');
    if (!studentType && (urlType === 'campus' || urlType === 'highschool')) {
      studentType = urlType;
      localStorage.setItem('clavi_student_type', studentType);
    }

    if (!savedName) {
      if (studentType) {
        // Already told us on the landing page — skip straight to the name
        // step and apply their level, no need to ask again.
        document.getElementById('welcomeStepType').style.display = 'none';
        document.getElementById('welcomeStepName').style.display = 'block';
        if (studentType === 'campus') applyDefaultLevel('campus', 'Year 1');
      } else if (seen) {
        // Returning user from before the name feature existed — they're
        // already using the app, so skip the campus/high-school picker and
        // just ask for a name, without the full "Karibu, welcome!" copy.
        document.getElementById('welcomeStepType').style.display = 'none';
        document.getElementById('welcomeStepName').style.display = 'block';
        const heading = document.querySelector('#welcomeStepName h2');
        const para = document.querySelector('#welcomeStepName p');
        if (heading) heading.textContent = 'One quick thing 👋';
        if (para) para.textContent = "What should we call you? We'll remember it and greet you by name next time.";
      }
      document.getElementById('welcomeOverlay').style.display = 'flex';
    } else {
      localStorage.setItem('clavi_visited', 'true');
      showWelcomeBackToast(savedName);
    }
  })();

  function chooseWelcomeType(type) {
    localStorage.setItem('clavi_student_type', type);
    // Persist to the account server-side, then reload — the level toggles
    // are pruned based on the server-rendered track, so a reload is needed
    // for the "no more switching between tracks" behavior to take effect.
    // showWelcomeIfFirstVisit() picks the flow back up at the name step
    // automatically, since clavi_student_type is already saved.
    fetch('/api/profile', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ studentTrack: type })
    }).catch(() => {}).finally(() => {
      window.location.reload();
    });
  }

  const welcomeTypeHighschoolBtn = document.getElementById('welcomeTypeHighschool');
  const welcomeTypeCampusBtn = document.getElementById('welcomeTypeCampus');
  if (welcomeTypeHighschoolBtn) welcomeTypeHighschoolBtn.addEventListener('click', () => chooseWelcomeType('highschool'));
  if (welcomeTypeCampusBtn) welcomeTypeCampusBtn.addEventListener('click', () => chooseWelcomeType('campus'));

  document.getElementById('welcomeStartBtn').addEventListener('click', () => {
    const nameInput = document.getElementById('welcomeNameInput');
    const name = nameInput ? nameInput.value.trim() : '';
    if (name) localStorage.setItem('clavi_user_name', name);
    localStorage.setItem('clavi_visited', 'true');
    document.getElementById('welcomeOverlay').style.display = 'none';
  });

  /* ---------------- Resume progress per subject ---------------- */
  function chatStorageKey(level, gradeForm, subject) {
    return `clavi_chat_${level}_${gradeForm}_${subject}`;
  }

  function saveChatProgress() {
    if (!currentSubject) return;
    try {
      localStorage.setItem(
        chatStorageKey(currentLevel, currentGradeForm, currentSubject),
        JSON.stringify(conversationHistory)
      );
    } catch (_) { /* storage full or unavailable — skip silently */ }
  }

  function loadChatProgress(level, gradeForm, subject) {
    try {
      const raw = localStorage.getItem(chatStorageKey(level, gradeForm, subject));
      return raw ? JSON.parse(raw) : null;
    } catch (_) {
      return null;
    }
  }

  /* ---------------- Teach mode (step-by-step topic teaching) ---------------- */
  function teachStorageKey(level, gradeForm, subject) {
    return `clavi_teach_${level}_${gradeForm}_${subject}`;
  }

  function saveTeachState() {
    if (!currentSubject) return;
    try {
      const key = teachStorageKey(currentLevel, currentGradeForm, currentSubject);
      if (teachState) localStorage.setItem(key, JSON.stringify(teachState));
      else localStorage.removeItem(key);
    } catch (_) {}
  }

  function loadTeachState(level, gradeForm, subject) {
    try {
      const raw = localStorage.getItem(teachStorageKey(level, gradeForm, subject));
      return raw ? JSON.parse(raw) : null;
    } catch (_) {
      return null;
    }
  }

  function markTopicTaught(subject, level, gradeForm, topic) {
    try {
      const raw = localStorage.getItem('clavi_topic_progress');
      const all = raw ? JSON.parse(raw) : {};
      const key = `${subject}|${level}|${gradeForm}`;
      all[key] = all[key] || {};
      all[key][topic] = Object.assign({}, all[key][topic], { taught: true });
      localStorage.setItem('clavi_topic_progress', JSON.stringify(all));
    } catch (_) {}
  }

  function currentTeachTopic() {
    return teachState && teachState.topics ? teachState.topics[teachState.topicIndex] : null;
  }

  function updateTeachBar() {
    if (!teachState) { teachBar.hidden = true; return; }
    teachBar.hidden = false;
    const topic = currentTeachTopic();
    const total = teachState.topics.length;
    teachBarLabel.textContent = `📖 Topic ${teachState.topicIndex + 1}/${total}: ${topic}`;
    teachContinueBtn.textContent = teachState.pendingTopicStart ? 'Start next topic ▶' : 'Continue ▶';
  }

  async function startTeachingTopic(topics, topicIndex) {
    teachState = { topics, topicIndex, pendingTopicStart: false };
    saveTeachState();
    emptyHint.style.display = 'none';
    teachStarter.hidden = true;
    updateTeachBar();
    await sendTeachTurn(`Please start teaching me "${currentTeachTopic()}" from the very beginning, one small step at a time.`, `🎓 Teach me: ${currentTeachTopic()}`);
  }

  async function sendTeachTurn(hiddenPromptText, displayText) {
    addMessage('user', displayText);
    sendBtn.disabled = true;
    teachContinueBtn.disabled = true;
    typingIndicator.style.visibility = 'visible';
    typingLabel.textContent = 'Tutor is thinking...';
    playAvatarState('thinking', true);
    try {
      const result = await askTutor(hiddenPromptText);
      addMessage('tutor', result.answer);
      saveChatProgress();
      recordQuestion(currentSubject, result.topicComplete ? 3 : 1);
      typingLabel.textContent = 'Got it!';
      playAvatarState('yes', false);
      if (result.topicComplete) {
        markTopicTaught(currentSubject, currentLevel, currentGradeForm, currentTeachTopic());
        if (teachState.topicIndex < teachState.topics.length - 1) {
          teachState.topicIndex += 1;
          teachState.pendingTopicStart = true;
          saveTeachState();
          addMessage('tutor', `✅ Great work — you've finished "${teachState.topics[teachState.topicIndex - 1]}". Ready for the next topic, "${currentTeachTopic()}"? Tap "Start next topic" below whenever you're ready.`);
        } else {
          addMessage('tutor', `🎉 That's the last topic done — you've been taught the full ${currentSubject} course! You can revise anytime, or head to Topics & Tests to check your understanding.`);
          teachState = null;
          saveTeachState();
        }
      }
      updateTeachBar();
      setTimeout(() => { typingIndicator.style.visibility = 'hidden'; }, 900);
    } catch (err) {
      addMessage('tutor', err.message || "Something went wrong reaching the tutor. Please try again.");
      typingLabel.textContent = 'Something went wrong';
      playAvatarState('alert', false);
      setTimeout(() => { typingIndicator.style.visibility = 'hidden'; }, 900);
    } finally {
      sendBtn.disabled = false;
      teachContinueBtn.disabled = false;
    }
  }

  teachStarterBtn.addEventListener('click', async () => {
    teachStarterBtn.disabled = true;
    teachStarterBtn.textContent = 'Loading topics...';
    try {
      const res = await fetch('/api/topics', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ subject: currentSubject, level: currentLevel, gradeForm: currentGradeForm })
      });
      if (!res.ok) throw new Error('Could not load topics right now.');
      const data = await res.json();
      const topics = data.topics || [];
      if (!topics.length) throw new Error('No topics came back — please try again.');
      saveTopicsCache(topics);
      openTeachPicker(topics);
    } catch (err) {
      addMessage('tutor', err.message || 'Could not load topics right now. Please try again.');
    } finally {
      teachStarterBtn.disabled = false;
      teachStarterBtn.textContent = '🎓 Teach me everything';
    }
  });

  function openTeachPicker(topics) {
    teachPickerMode = true;
    currentTopics = topics;
    openTopicsScreen(true);
  }

  teachContinueBtn.addEventListener('click', () => {
    if (!teachState) return;
    if (teachState.pendingTopicStart) {
      teachState.pendingTopicStart = false;
      saveTeachState();
      sendTeachTurn(`Please start teaching me "${currentTeachTopic()}" from the very beginning, one small step at a time.`, `🎓 Teach me: ${currentTeachTopic()}`);
    } else {
      sendTeachTurn('Please continue with the next step.', 'Continue ▶');
    }
  });

  teachExitBtn.addEventListener('click', () => {
    teachState = null;
    saveTeachState();
    updateTeachBar();
  });

  /* ---------------- Install App banner ---------------- */
  let deferredInstallPrompt = null;
  const installAppBanner = document.getElementById('installAppBanner');

  function isAlreadyInstalled() {
    return window.matchMedia('(display-mode: standalone)').matches || window.navigator.standalone === true;
  }

  window.addEventListener('beforeinstallprompt', (e) => {
    e.preventDefault();
    deferredInstallPrompt = e;
    if (!isAlreadyInstalled() && localStorage.getItem('clavi_install_dismissed') !== 'true' && installAppBanner) {
      installAppBanner.style.display = 'flex';
    }
    const settingsGroup = document.getElementById('settingsInstallGroup');
    if (settingsGroup && !isAlreadyInstalled()) settingsGroup.style.display = 'block';
  });

  const settingsInstallBtn = document.getElementById('settingsInstallBtn');
  if (settingsInstallBtn) {
    settingsInstallBtn.addEventListener('click', async () => {
      if (!deferredInstallPrompt) return;
      deferredInstallPrompt.prompt();
      await deferredInstallPrompt.userChoice;
      deferredInstallPrompt = null;
      const settingsGroup = document.getElementById('settingsInstallGroup');
      if (settingsGroup) settingsGroup.style.display = 'none';
    });
  }

  const installAppBtn = document.getElementById('installAppBtn');
  if (installAppBtn) {
    installAppBtn.addEventListener('click', async () => {
      if (!deferredInstallPrompt) return;
      deferredInstallPrompt.prompt();
      await deferredInstallPrompt.userChoice;
      deferredInstallPrompt = null;
      if (installAppBanner) installAppBanner.style.display = 'none';
    });
  }

  const dismissInstallBtn = document.getElementById('dismissInstallBtn');
  if (dismissInstallBtn) {
    dismissInstallBtn.addEventListener('click', () => {
      localStorage.setItem('clavi_install_dismissed', 'true');
      if (installAppBanner) installAppBanner.style.display = 'none';
    });
  }

  window.addEventListener('appinstalled', () => {
    if (installAppBanner) installAppBanner.style.display = 'none';
    const settingsGroup = document.getElementById('settingsInstallGroup');
    if (settingsGroup) settingsGroup.style.display = 'none';
    deferredInstallPrompt = null;
  });

  if ('serviceWorker' in navigator) {
    window.addEventListener('load', () => {
      navigator.serviceWorker.register('/sw.js').catch(() => { /* installability is a bonus, not critical */ });
    });
  }
