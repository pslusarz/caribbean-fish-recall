"""
The trainer's single-page UI, ported from the lustereczko prototype.

Two changes from the prototype, both simplifications now that we're not
constrained by the lustereczko display_ui_to_user bridge:
  1. callTool(name, args) -> fetchApi(method, path, body): plain fetch()
     calls against the FastAPI routes mounted at /api instead of
     window.app.callServerTool({name: 'run_custom_tool', ...}).
  2. Photos load via a direct <img src="/photos/{file}"> URL instead of a
     round-trip through fish_get_image + base64 data URI -- that hack only
     existed to fit inside the lustereczko bridge's per-call payload limit.
Everything else (lesson flow, photo gallery, level badge + promotion dots,
stats bar chart, rank-over-time SVG) is unchanged.
"""

INDEX_BODY = """
<style>
@keyframes fr-spin { to { transform: rotate(360deg); } }
.fr-spinner {
  display: inline-block; width: 16px; height: 16px;
  border: 2px solid rgba(255,255,255,0.3); border-top-color: #fff;
  border-radius: 50%; animation: fr-spin 0.7s linear infinite; vertical-align: middle;
}
.fr-spinner-lg { width: 28px; height: 28px; border-width: 3px; }
</style>

<div style="max-width:640px; margin:24px auto; height:920px; display:flex; flex-direction:column; font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif; background:#0b3d55; color:#eaf6fb; border-radius:10px; overflow:hidden; box-shadow:0 8px 30px rgba(0,0,0,0.35); position:relative;">

  <div style="display:flex; gap:4px; padding:10px 10px 0 10px; background:#072b3d;">
    <button class="tabbtn" data-tab="lesson" style="flex:1; padding:10px; border:none; border-radius:8px 8px 0 0; background:#0b3d55; color:#eaf6fb; font-weight:600; cursor:pointer;">Lesson</button>
    <button class="tabbtn" data-tab="stats" style="flex:1; padding:10px; border:none; border-radius:8px 8px 0 0; background:#0b3d55; color:#eaf6fb; font-weight:600; cursor:pointer;">Stats</button>
    <button class="tabbtn" data-tab="browse" style="flex:1; padding:10px; border:none; border-radius:8px 8px 0 0; background:#0b3d55; color:#eaf6fb; font-weight:600; cursor:pointer;">Browse</button>
  </div>

  <div id="panel-lesson" class="panel" style="flex:1; overflow-y:auto; padding:14px; display:flex; flex-direction:column; gap:10px;">
    <div id="lesson-start-screen" style="display:flex; flex-direction:column; gap:12px; align-items:center; justify-content:center; height:100%; text-align:center;">
      <div style="font-size:16px; opacity:0.9;">Ready for a lesson?</div>
      <div style="font-size:13px; opacity:0.7; max-width:320px;">~15 questions, a mix of new fish and review, tuned to keep you around 70% correct.</div>
      <button id="btn-start-lesson" style="padding:14px 24px; border:none; border-radius:8px; background:#2f9e6e; color:white; font-weight:700; cursor:pointer; font-size:15px;">Start Lesson</button>
    </div>

    <div id="lesson-active-screen" style="display:none; flex-direction:column; gap:10px; flex:1;">
      <div id="lesson-progress" style="font-size:12px; opacity:0.8;"></div>
      <div id="lesson-badge" style="display:none; align-items:center; gap:7px; font-size:12px; font-weight:700; padding:4px 9px; border-radius:6px; align-self:flex-start; color:#eaf6fb;">
        <span id="lesson-badge-text"></span>
        <span id="lesson-badge-dots" style="display:flex; align-items:center; gap:3px;"></span>
      </div>

      <div id="lesson-img-wrap" style="position:relative; width:100%; height:420px; background:#04202e; border-radius:8px; display:flex; align-items:center; justify-content:center; overflow:hidden;">
        <img id="lesson-img" style="max-width:100%; max-height:100%; object-fit:contain; transition:opacity 0.15s;" />
        <div id="lesson-img-spinner" style="display:none; position:absolute; inset:0; background:rgba(4,32,46,0.55); align-items:center; justify-content:center; border-radius:8px;"><div class="fr-spinner fr-spinner-lg"></div></div>
        <div id="lesson-img-credit" style="position:absolute; bottom:6px; right:10px; font-size:10px; color:rgba(255,255,255,0.75); background:rgba(0,0,0,0.45); padding:2px 7px; border-radius:5px;"></div>
        <div id="lesson-img-dots" style="position:absolute; bottom:8px; left:50%; transform:translateX(-50%); display:flex; gap:6px;"></div>
        <button id="lesson-img-prev" style="display:none; position:absolute; left:6px; top:50%; transform:translateY(-50%); width:32px; height:32px; border:none; border-radius:50%; background:rgba(0,0,0,0.4); color:#fff; font-size:16px; cursor:pointer; align-items:center; justify-content:center;">&#8249;</button>
        <button id="lesson-img-next" style="display:none; position:absolute; right:6px; top:50%; transform:translateY(-50%); width:32px; height:32px; border:none; border-radius:50%; background:rgba(0,0,0,0.4); color:#fff; font-size:16px; cursor:pointer; align-items:center; justify-content:center;">&#8250;</button>
      </div>

      <div id="lesson-intro-info" style="display:none; flex-direction:column; gap:4px;">
        <div id="intro-name" style="font-size:19px; font-weight:700;"></div>
        <div id="intro-sci" style="font-style:italic; opacity:0.85; font-size:13px;"></div>
        <div id="intro-size" style="font-size:12px; opacity:0.75;"></div>
        <div id="intro-features" style="font-size:13px; margin-top:4px;"></div>
        <div id="intro-mnemonic" style="margin-top:6px; font-size:13px; background:#123f56; border-left:3px solid #f4b942; padding:8px; border-radius:4px;"></div>
        <button id="btn-intro-continue" style="margin-top:10px; padding:12px; border:none; border-radius:8px; background:#2f9e6e; color:white; font-weight:700; cursor:pointer;">Got it &#8594;</button>
      </div>
      <div id="lesson-hint" style="display:none; font-size:13px; background:#123f56; border-left:3px solid #f4b942; padding:8px; border-radius:4px;"></div>
      <div id="lesson-question" style="display:flex; flex-direction:column; gap:8px;"></div>
      <div id="lesson-submit-spinner" style="display:none; align-items:center; gap:8px; font-size:13px; opacity:0.85;"><span class="fr-spinner"></span> Checking...</div>
      <div id="lesson-feedback" style="display:none; padding:10px; border-radius:8px; font-size:14px; line-height:1.4;"></div>
      <button id="btn-lesson-next" style="display:none; padding:12px; border:none; border-radius:8px; background:#2f9e6e; color:white; font-weight:700; cursor:pointer; font-size:15px;">Continue &#8594;</button>
    </div>

    <div id="lesson-summary-screen" style="display:none; flex-direction:column; gap:10px; align-items:center; justify-content:center; height:100%; text-align:center;">
      <div style="font-size:20px; font-weight:700;">Lesson complete!</div>
      <div id="summary-body" style="font-size:14px; line-height:1.6;"></div>
      <button id="btn-summary-restart" style="padding:14px 24px; border:none; border-radius:8px; background:#2f9e6e; color:white; font-weight:700; cursor:pointer; font-size:15px;">Back to menu</button>
    </div>
  </div>

  <div id="panel-stats" class="panel" style="flex:1; overflow-y:auto; padding:14px; display:none; flex-direction:column; gap:10px;">
    <div id="stats-loading" style="display:none; align-items:center; justify-content:center; padding:40px 0;"><div class="fr-spinner fr-spinner-lg"></div></div>
    <div id="stats-body" style="font-size:14px; line-height:1.6;"></div>
    <div id="stats-levelbars"></div>
    <div id="stats-chart"></div>
    <div style="margin-top:14px; padding-top:10px; border-top:1px solid rgba(255,255,255,0.12); font-size:12px; opacity:0.75; line-height:1.5;">
      <b>Your goal:</b> move every fish from Level 0 up to full mastery (Level 4). A fish advances a level each time you answer a recall question about it correctly in a lesson &mdash; keep practicing to level them all up.
    </div>
    <div style="margin-top:14px; padding-top:10px; border-top:1px solid rgba(255,255,255,0.12);">
      <div style="font-size:13px; font-weight:700; margin-bottom:6px;">Transfer progress to another device</div>
      <button id="btn-transfer-link" style="padding:10px 16px; border:none; border-radius:8px; background:#123f56; color:#eaf6fb; font-weight:600; cursor:pointer; font-size:13px;">Get transfer link</button>
      <div id="transfer-link-result" style="display:none; margin-top:10px; padding:10px; background:#123f56; border-radius:8px; font-size:12px;">
        <div style="opacity:0.8; margin-bottom:6px;">Open this link on your other device (valid for 15 minutes):</div>
        <div style="display:flex; gap:6px; align-items:center;">
          <input id="transfer-link-url" type="text" readonly style="flex:1; padding:8px; border-radius:6px; border:none; font-size:12px; background:#04202e; color:#eaf6fb;" />
          <button id="btn-copy-transfer-link" style="padding:8px 12px; border:none; border-radius:6px; background:#2f9e6e; color:white; font-weight:700; cursor:pointer; font-size:12px; white-space:nowrap;">Copy</button>
        </div>
      </div>
    </div>
  </div>

  <div id="panel-browse" class="panel" style="flex:1; overflow-y:auto; padding:14px; display:none; flex-direction:column; gap:10px;">
    <div id="browse-detail" style="display:none; background:#0e4a68; border-radius:10px; padding:12px;">
      <div id="browse-img-wrap" style="position:relative; width:100%; height:400px; background:#04202e; border-radius:8px; display:flex; align-items:center; justify-content:center; overflow:hidden;">
        <img id="browse-img" style="max-width:100%; max-height:100%; object-fit:contain; transition:opacity 0.15s;" />
        <div id="browse-img-spinner" style="display:none; position:absolute; inset:0; background:rgba(4,32,46,0.55); align-items:center; justify-content:center; border-radius:8px;"><div class="fr-spinner fr-spinner-lg"></div></div>
        <div id="browse-img-credit" style="position:absolute; bottom:6px; right:10px; font-size:10px; color:rgba(255,255,255,0.75); background:rgba(0,0,0,0.45); padding:2px 7px; border-radius:5px;"></div>
        <div id="browse-img-dots" style="position:absolute; bottom:8px; left:50%; transform:translateX(-50%); display:flex; gap:6px;"></div>
        <button id="browse-img-prev" style="display:none; position:absolute; left:6px; top:50%; transform:translateY(-50%); width:32px; height:32px; border:none; border-radius:50%; background:rgba(0,0,0,0.4); color:#fff; font-size:16px; cursor:pointer; align-items:center; justify-content:center;">&#8249;</button>
        <button id="browse-img-next" style="display:none; position:absolute; right:6px; top:50%; transform:translateY(-50%); width:32px; height:32px; border:none; border-radius:50%; background:rgba(0,0,0,0.4); color:#fff; font-size:16px; cursor:pointer; align-items:center; justify-content:center;">&#8250;</button>
      </div>
      <div id="browse-name" style="font-size:19px; font-weight:700; margin-top:8px;"></div>
      <div id="browse-sci" style="font-style:italic; opacity:0.85; font-size:13px;"></div>
      <div id="browse-size" style="font-size:12px; opacity:0.75; margin-top:2px;"></div>
      <div id="browse-features" style="margin-top:8px; font-size:13px; line-height:1.4;"></div>
      <div id="browse-mnemonic" style="margin-top:8px; font-size:13px; background:#123f56; border-left:3px solid #f4b942; padding:8px; border-radius:4px;"></div>
    </div>
    <div id="browse-loading" style="display:none; align-items:center; justify-content:center; padding:40px 0; width:100%;"><div class="fr-spinner fr-spinner-lg"></div></div>
    <div id="browse-list" style="display:flex; flex-wrap:wrap; gap:6px;"></div>
  </div>

  <div id="fr-error-toast" style="display:none; position:absolute; bottom:60px; left:50%; transform:translateX(-50%); background:#6b2b2b; color:#fff; padding:8px 14px; border-radius:8px; font-size:13px; box-shadow:0 4px 14px rgba(0,0,0,0.4); z-index:50; max-width:80%; text-align:center;"></div>

  <div id="fr-welcome-banner" style="display:none; position:absolute; top:60px; left:50%; transform:translateX(-50%); background:#1e5c3f; color:#fff; padding:10px 16px; border-radius:8px; font-size:13px; box-shadow:0 4px 14px rgba(0,0,0,0.4); z-index:50; max-width:85%; text-align:center; cursor:pointer;">Welcome! Your progress has been transferred to this device.</div>

  <div id="fr-claim-overlay" style="display:none; position:fixed; inset:0; background:rgba(4,32,46,0.95); z-index:100; align-items:center; justify-content:center; padding:20px;">
    <div id="fr-claim-content" style="max-width:380px; text-align:center; background:#0b3d55; padding:24px; border-radius:12px; box-shadow:0 8px 30px rgba(0,0,0,0.5); color:#eaf6fb;"></div>
  </div>

  <div style="padding:8px 14px; background:#072b3d; font-size:11px; text-align:center; opacity:0.75; border-top:1px solid rgba(255,255,255,0.08);">
    Fish photos &amp; species data: <a href="https://www.reef.org/species/galleries/caribbean" target="_blank" rel="noopener" style="color:#eaf6fb;">REEF.org</a>
    &nbsp;&middot;&nbsp;
    <a href="https://github.com/pslusarz/caribbean-fish-recall" target="_blank" rel="noopener" style="color:#eaf6fb;">GitHub</a>
    &nbsp;&middot;&nbsp;
    <a href="https://www.linkedin.com/in/paul-slusarz-365933124/" target="_blank" rel="noopener" style="color:#eaf6fb;">LinkedIn</a>
  </div>

</div>

<script>
(function() {
  var currentItem = null;
  var currentLessonId = null;
  var allFish = [];
  var selectedFishId = null;
  var LEVEL_COLORS = { 0: '#4a5568', 1: '#c05621', 2: '#b7791f', 3: '#2b6cb0', 4: '#2f9e6e' };

  // A stalled connection should never leave a button disabled or a spinner
  // spinning forever -- every fetch gets aborted after FETCH_TIMEOUT_MS, and
  // every busy/spinner helper below sets its own safety-net timeout on top
  // of that as a second line of defense.
  var FETCH_TIMEOUT_MS = 20000;
  var IMG_LOAD_TIMEOUT_MS = 15000;

  function fetchApi(method, path, body) {
    var opts = { method: method, headers: {} };
    if (body !== undefined) {
      opts.headers['Content-Type'] = 'application/json';
      opts.body = JSON.stringify(body);
    }
    var controller = new AbortController();
    var timer = setTimeout(function() { controller.abort(); }, FETCH_TIMEOUT_MS);
    opts.signal = controller.signal;
    return fetch('/api' + path, opts)
      .then(function(r) {
        // fetch() only rejects on network-level failures, never on 4xx/5xx --
        // without this check, a server error would flow into the *success*
        // path with an error-shaped body ({"detail": "..."}), rendering as
        // broken/undefined content instead of tripping the error toast.
        return r.json().then(function(data) {
          if (!r.ok) { throw new Error(data.detail || 'Request failed'); }
          return data;
        });
      })
      .finally(function() { clearTimeout(timer); });
  }

  // ---------- ERROR TOAST ----------
  var errorToastTimer = null;
  function showErrorToast(msg) {
    var el = document.getElementById('fr-error-toast');
    el.textContent = msg || 'Connection issue \\u2014 please try again.';
    el.style.display = 'block';
    clearTimeout(errorToastTimer);
    errorToastTimer = setTimeout(function() { el.style.display = 'none'; }, 4000);
  }

  // ---------- LOADING-INDICATOR HELPER (for tab-level fetches) ----------
  // Shows indicatorEl for the lifetime of promise, with its own safety-net
  // timeout so it can never outlive a hung request even if the underlying
  // fetch's own abort somehow didn't fire. Swallows the rejection after
  // surfacing an error toast, so callers don't need their own .catch().
  function withLoadingIndicator(indicatorEl, promise, errorMsg) {
    indicatorEl.style.display = 'flex';
    var safety = setTimeout(function() { indicatorEl.style.display = 'none'; }, FETCH_TIMEOUT_MS + 3000);
    return promise.then(function(v) {
      clearTimeout(safety);
      indicatorEl.style.display = 'none';
      return v;
    }, function(err) {
      clearTimeout(safety);
      indicatorEl.style.display = 'none';
      showErrorToast(errorMsg);
    });
  }

  // ---------- BUTTON BUSY/SPINNER HELPER ----------
  function setButtonBusy(btn, busy) {
    if (busy) {
      if (btn._frOrigHtml === undefined) btn._frOrigHtml = btn.innerHTML;
      btn.innerHTML = '<span class="fr-spinner"></span>';
      btn.disabled = true;
      btn.style.opacity = '0.7';
      btn.style.cursor = 'default';
    } else {
      if (btn._frOrigHtml !== undefined) btn.innerHTML = btn._frOrigHtml;
      btn.disabled = false;
      btn.style.opacity = '';
      btn.style.cursor = 'pointer';
    }
  }

  // Disables btn + shows an in-button spinner for the lifetime of
  // asyncFn()'s promise. Re-enables no matter what: success, failure, or
  // (via the safety timer) a request that never settles at all. Ignores
  // re-clicks while already busy instead of firing a second request.
  function runBusy(btn, asyncFn) {
    if (btn.disabled) return Promise.resolve();
    setButtonBusy(btn, true);
    var safety = setTimeout(function() { setButtonBusy(btn, false); }, FETCH_TIMEOUT_MS + 3000);
    function finish() { clearTimeout(safety); setButtonBusy(btn, false); }
    var p;
    try {
      p = asyncFn();
    } catch (e) {
      finish();
      showErrorToast();
      return Promise.resolve();
    }
    return p.then(function(v) { finish(); return v; }, function(err) {
      finish();
      showErrorToast();
    });
  }

  // ---------- REUSABLE PHOTO GALLERY ----------
  function makeGallery(prefix) {
    var state = { photos: [], idx: 0 };
    var wrap = document.getElementById(prefix + '-img-wrap');
    var img = document.getElementById(prefix + '-img');
    var credit = document.getElementById(prefix + '-img-credit');
    var dots = document.getElementById(prefix + '-img-dots');
    var prevBtn = document.getElementById(prefix + '-img-prev');
    var nextBtn = document.getElementById(prefix + '-img-next');
    var spinner = document.getElementById(prefix + '-img-spinner');
    var spinnerSafety = null;

    // The old photo stays visible (dimmed under the spinner) while the new
    // one loads instead of vanishing, so there's no layout flash -- but the
    // spinner makes it obvious *something's* happening instead of it just
    // looking like the click did nothing. Safety timeout guarantees this
    // can't spin forever even if a load/error event somehow never fires.
    function showImgSpinner() {
      spinner.style.display = 'flex';
      clearTimeout(spinnerSafety);
      spinnerSafety = setTimeout(hideImgSpinner, IMG_LOAD_TIMEOUT_MS);
    }
    function hideImgSpinner() {
      spinner.style.display = 'none';
      clearTimeout(spinnerSafety);
    }
    img.addEventListener('load', hideImgSpinner);
    img.addEventListener('error', hideImgSpinner);

    function renderDots() {
      dots.innerHTML = '';
      if (state.photos.length <= 1) return;
      state.photos.forEach(function(_, i) {
        var d = document.createElement('div');
        d.style.cssText = 'width:8px; height:8px; border-radius:50%; cursor:pointer; background:' +
          (i === state.idx ? '#fff' : 'rgba(255,255,255,0.4)') + ';';
        d.addEventListener('click', function(e) { e.stopPropagation(); showIndex(i); });
        dots.appendChild(d);
      });
    }

    function showIndex(i) {
      if (!state.photos.length) return;
      state.idx = ((i % state.photos.length) + state.photos.length) % state.photos.length;
      var p = state.photos[state.idx];
      showImgSpinner();
      img.src = '/photos/' + p.file;
      credit.textContent = p.credit ? ('Photo: ' + p.credit) : '';
      renderDots();
    }

    function setPhotos(photos) {
      state.photos = photos || [];
      state.idx = 0;
      hideImgSpinner();
      img.src = '';
      credit.textContent = '';
      dots.innerHTML = '';
      if (state.photos.length) {
        showIndex(0);
        var multi = state.photos.length > 1;
        prevBtn.style.display = multi ? 'flex' : 'none';
        nextBtn.style.display = multi ? 'flex' : 'none';
      }
    }

    prevBtn.addEventListener('click', function(e) { e.stopPropagation(); showIndex(state.idx - 1); });
    nextBtn.addEventListener('click', function(e) { e.stopPropagation(); showIndex(state.idx + 1); });
    wrap.addEventListener('click', function() { if (state.photos.length > 1) showIndex(state.idx + 1); });

    return { setPhotos: setPhotos };
  }

  var lessonGallery = makeGallery('lesson');
  var browseGallery = makeGallery('browse');

  function showTab(tab) {
    ['lesson', 'stats', 'browse'].forEach(function(t) {
      document.getElementById('panel-' + t).style.display = (t === tab) ? 'flex' : 'none';
    });
    document.querySelectorAll('.tabbtn').forEach(function(b) {
      b.style.background = (b.getAttribute('data-tab') === tab) ? '#0e4a68' : '#0b3d55';
    });
    if (tab === 'stats') loadStats();
    if (tab === 'browse' && allFish.length === 0) loadBrowseList();
  }

  document.querySelectorAll('.tabbtn').forEach(function(b) {
    b.addEventListener('click', function() { showTab(b.getAttribute('data-tab')); });
  });

  // ---------- LESSON TAB ----------
  function showLessonScreen(which) {
    document.getElementById('lesson-start-screen').style.display = (which === 'start') ? 'flex' : 'none';
    document.getElementById('lesson-active-screen').style.display = (which === 'active') ? 'flex' : 'none';
    document.getElementById('lesson-summary-screen').style.display = (which === 'summary') ? 'flex' : 'none';
  }

  document.getElementById('btn-start-lesson').addEventListener('click', function() {
    runBusy(this, function() {
      return fetchApi('POST', '/lesson/start').then(function(res) {
        currentLessonId = res.lesson_id;
        showLessonScreen('active');
        return loadNextItem();
      });
    });
  });

  document.getElementById('btn-summary-restart').addEventListener('click', function() {
    showLessonScreen('start');
  });

  function renderLevelBadge(item) {
    var badge = document.getElementById('lesson-badge');
    var textEl = document.getElementById('lesson-badge-text');
    var dotsEl = document.getElementById('lesson-badge-dots');
    var level = item.level_at_plan;

    badge.style.display = 'flex';
    badge.style.background = LEVEL_COLORS[level];

    var label = 'Level ' + level;
    if (item.is_retry) {
      label += ' \\u00b7 Encore';
    } else if (item.is_reinforce) {
      label += ' \\u00b7 First quiz';
    } else if (level === 0) {
      label += ' \\u00b7 New fish';
    }
    textEl.textContent = label;

    dotsEl.innerHTML = '';
    if (!item.is_retry && level > 0) {
      if (item.mastered) {
        var star = document.createElement('span');
        star.textContent = '\\u2605';
        star.style.cssText = 'font-size:12px; color:#fff;';
        dotsEl.appendChild(star);
      } else {
        var streak = item.streak_success || 0;
        var threshold = item.promote_threshold || 2;
        for (var i = 0; i < threshold; i++) {
          var d = document.createElement('span');
          var filled = i < streak;
          d.style.cssText = 'width:7px; height:7px; border-radius:50%; display:inline-block; background:' +
            (filled ? 'rgba(255,255,255,0.95)' : 'rgba(255,255,255,0.32)') + ';';
          dotsEl.appendChild(d);
        }
      }
    }
  }

  function loadNextItem() {
    document.getElementById('lesson-feedback').style.display = 'none';
    document.getElementById('lesson-hint').style.display = 'none';
    document.getElementById('lesson-intro-info').style.display = 'none';
    document.getElementById('btn-lesson-next').style.display = 'none';
    document.getElementById('lesson-badge').style.display = 'none';
    document.getElementById('lesson-question').innerHTML = '';

    return fetchApi('GET', '/lesson/next_item?lesson_id=' + currentLessonId).then(function(item) {
      if (item.done) {
        var s = item.summary || {};
        document.getElementById('summary-body').innerHTML =
          '<div>' + (s.correct || 0) + ' correct / ' + (s.wrong || 0) + ' missed this lesson</div>' +
          '<div style="margin-top:6px;">Rank: <b>' + s.score + '%</b></div>' +
          '<div>Mastered: ' + s.mastered_count + ' / ' + s.total + '</div>' +
          '<div style="opacity:0.7; font-size:12px; margin-top:6px;">Lessons completed: ' + s.lessons_completed + '</div>';
        showLessonScreen('summary');
        return;
      }
      currentItem = item;
      document.getElementById('lesson-progress').textContent = item.remaining_in_lesson + ' left in this lesson';

      renderLevelBadge(item);

      lessonGallery.setPhotos(item.photos);

      if (item.hint) {
        var hb = document.getElementById('lesson-hint');
        hb.style.display = 'block';
        hb.textContent = '\\ud83d\\udca1 hint: ' + item.hint;
      }

      if (item.question_type === 'intro') {
        document.getElementById('lesson-intro-info').style.display = 'flex';
        document.getElementById('intro-name').textContent = item.name;
        document.getElementById('intro-sci').textContent = item.scientific_name || '';
        document.getElementById('intro-size').textContent = item.size ? ('Size: ' + item.size) : '';
        document.getElementById('intro-features').textContent = (item.features || '').split(' | ').join(' \\u2022 ');
        document.getElementById('intro-mnemonic').textContent = item.mnemonic ? ('\\ud83d\\udca1 ' + item.mnemonic) : '';
      } else {
        renderQuestion(item);
      }
    });
  }

  document.getElementById('btn-intro-continue').addEventListener('click', function() {
    runBusy(this, function() { return submitAnswer(null); });
  });

  function renderQuestion(item) {
    var qdiv = document.getElementById('lesson-question');
    qdiv.innerHTML = '';
    if (item.question_type === 'mc_easy' || item.question_type === 'mc_hard') {
      item.choices.forEach(function(c) {
        var btn = document.createElement('button');
        btn.textContent = c.name;
        btn.style.cssText = 'padding:12px; border:none; border-radius:8px; background:#123f56; color:#eaf6fb; font-size:14px; cursor:pointer; text-align:left;';
        btn.addEventListener('click', function() { if (!btn.disabled) submitAnswer(c.id); });
        qdiv.appendChild(btn);
      });
    } else {
      var label = document.createElement('div');
      label.style.cssText = 'font-size:13px; opacity:0.85;';
      label.textContent = (item.question_type === 'spell_partial')
        ? 'Fill in the name (scaffold: ' + item.scaffold + ')'
        : 'Type the full common name from memory:';
      qdiv.appendChild(label);
      var input = document.createElement('input');
      input.type = 'text';
      input.placeholder = 'e.g. Blue Chromis';
      input.style.cssText = 'padding:12px; border-radius:8px; border:none; font-size:15px;';
      var btn = document.createElement('button');
      btn.textContent = 'Submit';
      btn.style.cssText = 'padding:12px; border:none; border-radius:8px; background:#2f9e6e; color:white; font-weight:700; cursor:pointer;';
      input.addEventListener('keydown', function(e) { if (e.key === 'Enter' && !btn.disabled) submitAnswer(input.value); });
      qdiv.appendChild(input);
      btn.addEventListener('click', function() { if (!btn.disabled) submitAnswer(input.value); });
      qdiv.appendChild(btn);
      setTimeout(function() { input.focus(); }, 50);
    }
  }

  // Locks every control in the question (all MC choices, not just the one
  // clicked, plus the spelling input/button) so a second click or an Enter
  // keypress can't fire a second submit while the first is still in flight.
  var submitSafety = null;
  function setSubmitBusy(busy) {
    var els = document.querySelectorAll('#lesson-question button, #lesson-question input, #btn-intro-continue');
    els.forEach(function(el) { el.disabled = busy; el.style.opacity = busy ? '0.5' : ''; });
    document.getElementById('lesson-submit-spinner').style.display = busy ? 'flex' : 'none';
  }

  function submitAnswer(answer) {
    if (!currentItem) return Promise.resolve();
    var item = currentItem;
    currentItem = null; // guard first, synchronously -- before the fetch even starts
    setSubmitBusy(true);
    clearTimeout(submitSafety);
    submitSafety = setTimeout(function() { setSubmitBusy(false); }, FETCH_TIMEOUT_MS + 3000);
    return fetchApi('POST', '/lesson/submit', { item_id: item.item_id, answer: answer }).then(function(res) {
      clearTimeout(submitSafety);
      setSubmitBusy(false);
      if (res.is_intro) {
        return loadNextItem();
      }
      var fb = document.getElementById('lesson-feedback');
      fb.style.display = 'block';
      fb.style.background = res.correct ? '#1e5c3f' : '#6b2b2b';
      var html = '';
      html += '<b>' + (res.correct ? '\\u2713 Correct!' : '\\u2717 Not quite.') + '</b>';
      if (res.promoted) {
        html += ' <span style="opacity:0.85;">\\u2191 level up!</span>';
      } else if (res.demoted) {
        html += ' <span style="opacity:0.85;">\\u2193 demoted</span>';
      } else if (res.mastered_now) {
        html += ' <span style="opacity:0.85;">\\u2605 mastered!</span>';
      }
      html += '<br>';
      html += '<b>' + res.correct_name + '</b> <i>(' + res.scientific_name + ')</i><br>';
      if (!res.correct && res.matched_other) {
        html += 'Looks like you confused it with <b>' + res.matched_other.name + '</b>.<br>';
      }
      if (!res.correct && res.distance !== null && res.distance !== undefined) {
        html += 'Your spelling was ' + res.distance + ' letter change(s) off.<br>';
      }
      html += '<span style="opacity:0.85; font-size:13px;">' + (res.features || '').split(' | ').join(' \\u2022 ') + '</span>';
      if (res.mnemonic) {
        html += '<div style="margin-top:6px;">\\ud83d\\udca1 ' + res.mnemonic + '</div>';
      }
      html += '<div style="margin-top:6px; font-size:12px; opacity:0.7;">' +
        (res.is_retry ? 'encore round \\u2014 no stat changes' : 'now level ' + res.new_level + '/4') + '</div>';
      fb.innerHTML = html;
      document.getElementById('lesson-question').innerHTML = '';
      document.getElementById('lesson-intro-info').style.display = 'none';
      document.getElementById('lesson-hint').style.display = 'none';
      document.getElementById('btn-lesson-next').style.display = 'block';
    }, function(err) {
      clearTimeout(submitSafety);
      setSubmitBusy(false);
      showErrorToast();
    });
  }

  document.getElementById('btn-lesson-next').addEventListener('click', function() {
    runBusy(this, loadNextItem);
  });

  // ---------- STATS TAB ----------
  function drawChart(history) {
    var el = document.getElementById('stats-chart');
    if (!history || history.length < 2) { el.innerHTML = ''; return; }
    var w = 500, h = 140, pad = 24;
    var maxScore = Math.max(100, Math.max.apply(null, history.map(function(p) { return p.score; })));
    var minTs = history[0].ts, maxTs = history[history.length - 1].ts;
    var tsRange = Math.max(1, maxTs - minTs);
    function x(ts) { return pad + (w - 2 * pad) * (ts - minTs) / tsRange; }
    function y(score) { return h - pad - (h - 2 * pad) * (score / maxScore); }
    var pts = history.map(function(p) { return x(p.ts) + ',' + y(p.score); }).join(' ');
    var svg = '<svg viewBox="0 0 ' + w + ' ' + h + '" style="width:100%; height:140px;">' +
      '<line x1="' + pad + '" y1="' + (h - pad) + '" x2="' + (w - pad) + '" y2="' + (h - pad) + '" stroke="#2b6cb0" stroke-width="1" />' +
      '<polyline points="' + pts + '" fill="none" stroke="#2f9e6e" stroke-width="2.5" />' +
      history.map(function(p) {
        return '<circle cx="' + x(p.ts) + '" cy="' + y(p.score) + '" r="3" fill="#2f9e6e" />';
      }).join('') +
      '</svg>';
    el.innerHTML = '<div style="font-size:12px; opacity:0.7; margin-top:12px; margin-bottom:4px;">Rank over time</div>' + svg;
  }

  function drawLevelBars(by_level) {
    var el = document.getElementById('stats-levelbars');
    var levels = [0, 1, 2, 3, 4];
    var counts = levels.map(function(l) { return by_level[String(l)] || 0; });
    var maxCount = Math.max(1, Math.max.apply(null, counts));
    var maxBarPx = 100;
    var bars = levels.map(function(l, i) {
      var count = counts[i];
      var barH = count === 0 ? 4 : Math.max(6, Math.round((count / maxCount) * maxBarPx));
      return '<div style="display:flex; flex-direction:column; align-items:center; gap:4px; width:44px;">' +
        '<div style="font-size:11px; opacity:0.85;">' + count + '</div>' +
        '<div style="width:32px; height:' + barH + 'px; background:' + LEVEL_COLORS[l] + '; border-radius:4px 4px 2px 2px;"></div>' +
        '<div style="font-size:11px; opacity:0.7;">L' + l + '</div>' +
        '</div>';
    }).join('');
    el.innerHTML = '<div style="font-size:12px; opacity:0.7; margin-top:10px; margin-bottom:4px;">Fish by level</div>' +
      '<div style="display:flex; align-items:flex-end; gap:10px; height:' + (maxBarPx + 32) + 'px;">' + bars + '</div>';
  }

  function loadStats() {
    withLoadingIndicator(document.getElementById('stats-loading'), fetchApi('GET', '/stats'), 'Could not load stats.').then(function(s) {
      if (!s) return;
      var html = '';
      html += '<div style="font-size:22px; font-weight:700;">' + s.score + '% rank</div>';
      html += '<div>Mastered: <b>' + s.mastered + ' / ' + s.total + '</b></div>';
      html += '<div>Lessons completed: ' + s.lessons_completed + '</div>';
      var graded = s.total_correct + s.total_wrong;
      var acc = graded ? Math.round(100 * s.total_correct / graded) : 0;
      html += '<div>Overall accuracy: ' + acc + '% (' + s.total_correct + '/' + graded + ' graded questions, excludes intros)</div>';
      if (s.hardest.length) {
        html += '<div style="margin-top:10px;"><b>Trickiest so far:</b><ul>';
        s.hardest.forEach(function(h) {
          html += '<li>' + h.name + ' \\u2014 missed ' + h.wrong_count + 'x</li>';
        });
        html += '</ul></div>';
      }
      if (s.top_confusions.length) {
        html += '<div style="margin-top:10px;"><b>Common mix-ups:</b><ul>';
        s.top_confusions.forEach(function(c) {
          html += '<li>' + c.a + ' \\u2194 ' + c.b + '</li>';
        });
        html += '</ul></div>';
      }
      document.getElementById('stats-body').innerHTML = html;
      drawLevelBars(s.by_level);
      drawChart(s.history);
    });
  }

  // ---------- BROWSE TAB ----------
  function loadBrowseList() {
    withLoadingIndicator(document.getElementById('browse-loading'), fetchApi('GET', '/browse'), 'Could not load fish list.').then(function(res) {
      if (!res) return;
      allFish = res.fish;
      renderBrowseList();
    });
  }

  function renderBrowseList() {
    var list = document.getElementById('browse-list');
    list.innerHTML = '';
    allFish.forEach(function(f) {
      var selected = f.id === selectedFishId;
      var chip = document.createElement('div');
      chip.textContent = f.name + (f.mastered ? ' \\u2605' : '');
      chip.style.cssText = 'padding:6px 10px; border-radius:16px; font-size:12px; cursor:pointer; background:' +
        LEVEL_COLORS[f.level] + '; color:#eaf6fb; transition:transform 0.12s, box-shadow 0.12s;' +
        (selected
          ? ' transform:scale(1.12); box-shadow:0 0 0 2px #fff, 0 3px 10px rgba(0,0,0,0.5); font-weight:700;'
          : ' box-shadow:none;');
      chip.addEventListener('click', function() { showBrowseDetail(f); });
      list.appendChild(chip);
    });
  }

  function showBrowseDetail(f) {
    selectedFishId = f.id;
    renderBrowseList();
    var detail = document.getElementById('browse-detail');
    detail.style.display = 'block';
    document.getElementById('browse-name').textContent = f.name;
    document.getElementById('browse-sci').textContent = f.scientific_name || '';
    document.getElementById('browse-size').textContent = f.size ? ('Size: ' + f.size) : '';
    document.getElementById('browse-features').textContent = (f.features || '').split(' | ').join(' \\u2022 ');
    document.getElementById('browse-mnemonic').textContent = f.mnemonic ? ('\\ud83d\\udca1 ' + f.mnemonic) : '';
    browseGallery.setPhotos(f.photos);
  }

  // ---------- CROSS-DEVICE TRANSFER LINKS ----------
  function showClaimOverlay(html) {
    document.getElementById('fr-claim-content').innerHTML = html;
    document.getElementById('fr-claim-overlay').style.display = 'flex';
  }

  function goToApp() { window.location.href = '/'; }

  // fetchApi *rejects* (throws) on network failure, timeout, or a non-OK
  // HTTP status -- both branches below must be handled via .then()'s second
  // argument, not a falsy-check on the resolved value, or an expired-token
  // response (the main error case here) would silently leave the overlay
  // stuck on its loading spinner forever instead of showing anything.
  function confirmTransfer(token) {
    showClaimOverlay('<div class="fr-spinner fr-spinner-lg"></div><div style="margin-top:12px; font-size:14px;">Setting up your account...</div>');
    fetchApi('POST', '/account/transfer_confirm', { token: token }).then(function(res) {
      window.location.href = '/?welcome=1';
    }, function(err) {
      showClaimOverlay(
        '<div style="font-size:15px; font-weight:700; margin-bottom:10px;">Something went wrong</div>' +
        '<div style="font-size:13px; opacity:0.85; margin-bottom:16px;">Please try the link again.</div>' +
        '<button id="btn-claim-goapp2" style="padding:10px 18px; border:none; border-radius:8px; background:#2f9e6e; color:white; font-weight:700; cursor:pointer;">Go to app</button>'
      );
      document.getElementById('btn-claim-goapp2').addEventListener('click', goToApp);
    });
  }

  function runClaimFlow(token) {
    showClaimOverlay('<div class="fr-spinner fr-spinner-lg"></div><div style="margin-top:12px; font-size:14px;">Checking your link...</div>');
    fetchApi('GET', '/account/transfer_preview?t=' + encodeURIComponent(token)).then(function(preview) {
      if (preview.same_account) {
        goToApp();
        return;
      }
      if (preview.current_score === null) {
        confirmTransfer(token);
        return;
      }
      showClaimOverlay(
        '<div style="font-size:15px; font-weight:700; margin-bottom:10px;">\\u26a0\\ufe0f You already have progress on this device</div>' +
        '<div style="font-size:13px; line-height:1.6; margin-bottom:16px; text-align:left;">' +
          'Your rank on <b>this device</b>: <b>' + preview.current_score + '%</b><br>' +
          'Your incoming rank: <b>' + preview.incoming_score + '%</b><br><br>' +
          'Continuing will replace this device\\u2019s progress with the incoming progress, with no way to undo it.' +
        '</div>' +
        '<div style="display:flex; gap:10px; justify-content:center;">' +
          '<button id="btn-claim-cancel" style="padding:10px 18px; border:none; border-radius:8px; background:#6b2b2b; color:white; font-weight:700; cursor:pointer;">Cancel</button>' +
          '<button id="btn-claim-continue" style="padding:10px 18px; border:none; border-radius:8px; background:#2f9e6e; color:white; font-weight:700; cursor:pointer;">Continue</button>' +
        '</div>'
      );
      document.getElementById('btn-claim-cancel').addEventListener('click', goToApp);
      document.getElementById('btn-claim-continue').addEventListener('click', function() { confirmTransfer(token); });
    }, function(err) {
      showClaimOverlay(
        '<div style="font-size:15px; font-weight:700; margin-bottom:10px;">Link expired or invalid</div>' +
        '<div style="font-size:13px; opacity:0.85; margin-bottom:16px;">Generate a new transfer link from your other device\\u2019s Stats tab.</div>' +
        '<button id="btn-claim-goapp" style="padding:10px 18px; border:none; border-radius:8px; background:#2f9e6e; color:white; font-weight:700; cursor:pointer;">Go to app</button>'
      );
      document.getElementById('btn-claim-goapp').addEventListener('click', goToApp);
    });
  }

  (function checkForClaimPath() {
    var m = window.location.pathname.match(/^\\/claim\\/(.+)$/);
    if (m) runClaimFlow(decodeURIComponent(m[1]));
  })();

  (function checkForWelcomeBanner() {
    if (window.location.search.indexOf('welcome=1') === -1) return;
    var banner = document.getElementById('fr-welcome-banner');
    banner.style.display = 'block';
    var dismiss = function() { banner.style.display = 'none'; };
    banner.addEventListener('click', dismiss);
    setTimeout(dismiss, 6000);
    var url = new URL(window.location.href);
    url.searchParams.delete('welcome');
    window.history.replaceState({}, '', url.pathname + url.search);
  })();

  document.getElementById('btn-transfer-link').addEventListener('click', function() {
    // A rejection here is handled by runBusy itself (generic error toast +
    // button re-enable) -- no custom error UI needed for "couldn't generate
    // a link", unlike the claim flow above.
    runBusy(this, function() {
      return fetchApi('POST', '/account/transfer_link').then(function(res) {
        document.getElementById('transfer-link-url').value = window.location.origin + res.path;
        document.getElementById('transfer-link-result').style.display = 'block';
      });
    });
  });

  document.getElementById('btn-copy-transfer-link').addEventListener('click', function() {
    var input = document.getElementById('transfer-link-url');
    input.select();
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(input.value);
    } else {
      document.execCommand('copy');
    }
    var btn = this;
    var orig = btn.textContent;
    btn.textContent = 'Copied!';
    setTimeout(function() { btn.textContent = orig; }, 1500);
  });

  showTab('lesson');
})();
</script>
"""
