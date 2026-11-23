(function () {
  var reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  // Live star count, shown once it is worth showing.
  var star = document.getElementById('stars');
  if (star && window.fetch) {
    fetch('https://api.github.com/repos/ArathiS1/ragops-observatory')
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) { if (d && d.stargazers_count >= 10) { star.textContent = d.stargazers_count; star.hidden = false; } })
      .catch(function () {});
  }

  function copyText(text, btn) {
    var done = function () { btn.textContent = 'Copied'; setTimeout(function () { btn.textContent = 'Copy'; }, 1600); };
    if (navigator.clipboard) navigator.clipboard.writeText(text).then(done, function () {});
  }
  document.querySelectorAll('[data-copy]').forEach(function (b) {
    b.addEventListener('click', function () { copyText(document.getElementById(b.dataset.copy).textContent, b); });
  });

  // Tab lists: arrow keys move between tabs.
  function tablist(buttons, onPick) {
    buttons.forEach(function (t, i) {
      t.tabIndex = t.getAttribute('aria-selected') === 'true' ? 0 : -1;
      t.addEventListener('click', function () { pick(t); });
      t.addEventListener('keydown', function (e) {
        var n = null;
        if (e.key === 'ArrowDown' || e.key === 'ArrowRight') n = buttons[(i + 1) % buttons.length];
        if (e.key === 'ArrowUp' || e.key === 'ArrowLeft') n = buttons[(i - 1 + buttons.length) % buttons.length];
        if (n) { e.preventDefault(); pick(n); n.focus(); }
      });
    });
    function pick(t) {
      buttons.forEach(function (b) { var on = b === t; b.setAttribute('aria-selected', on); b.tabIndex = on ? 0 : -1; });
      onPick(t);
    }
  }

  // Dashboard explorer.
  var img = document.getElementById('screen-img'), cap = document.getElementById('screen-cap');
  var tabs = Array.prototype.slice.call(document.querySelectorAll('.tabs [role=tab]'));
  tabs.forEach(function (t) { var p = new Image(); p.src = 'docs/images/' + t.dataset.img; });
  tablist(tabs, function (t) {
    img.classList.add('fade');
    setTimeout(function () {
      img.src = 'docs/images/' + t.dataset.img;
      img.alt = 'RAGOps dashboard: ' + t.querySelector('.t').textContent;
      cap.textContent = t.dataset.cap;
      img.classList.remove('fade');
    }, reduce ? 0 : 150);
  });

  // Terminal tabs.
  var termTabs = Array.prototype.slice.call(document.querySelectorAll('.term-tabs [role=tab]'));
  tablist(termTabs, function (t) {
    termTabs.forEach(function (b) { document.getElementById(b.dataset.term).hidden = b !== t; });
  });
  var termCopy = document.querySelector('[data-copy-term]');
  if (termCopy) termCopy.addEventListener('click', function () {
    var shown = document.querySelector('.term pre:not([hidden])');
    var text = shown.textContent.split('\n').map(function (l) { return l.replace(/\s+#.*$/, ''); })
      .filter(function (l) { return l.trim() && l.trim()[0] !== '#'; }).join('\n');
    copyText(text, termCopy);
  });

  // The hero instrument: v1 fails at rerank, then v3 passes. Loops while visible.
  var track = document.getElementById('track');
  if (!track) return;
  var steps = Array.prototype.slice.call(track.querySelectorAll('li'));
  var diag = document.getElementById('diag'), stamp = document.getElementById('stamp');
  var ver = document.getElementById('scope-ver'), note = document.getElementById('rerank-note');
  var FAIL = '<b class="stage">Failing stage: rerank.</b> <b>billing.md</b>, the document with the answer, was retrieved at rank 2, ' +
             'but the reranker moved it to rank 7 and it was cut before the prompt. <b>Fix:</b> keep the top 5 after reranking.';
  var PASS = '<b class="fine">Every stage passed.</b> billing.md reaches the prompt and the answer is grounded in it. ' +
             'Release guardian: pass rate 90% to 100%, nothing newly failing.';
  var timers = [];
  function later(fn, ms) { timers.push(setTimeout(fn, ms)); }
  function clear() { timers.forEach(clearTimeout); timers = []; }
  function finish(fixed) {
    steps.forEach(function (s) {
      s.classList.add('on');
      s.classList.toggle('fail', !fixed && s.hasAttribute('data-fails'));
    });
    track.classList.toggle('has-fail', !fixed);
    track.style.setProperty('--prog', fixed ? '80%' : '40%');
    diag.innerHTML = fixed ? PASS : FAIL;
    stamp.textContent = fixed ? 'Ready' : 'Not ready';
    stamp.className = 'stamp show ' + (fixed ? 'good' : 'bad');
  }
  function run(fixed) {
    clear();
    steps.forEach(function (s) { s.classList.remove('on', 'fail'); });
    track.classList.remove('has-fail');
    track.style.setProperty('--prog', '0%');
    stamp.className = 'stamp';
    ver.textContent = fixed ? 'help-centre assistant, v3' : 'help-centre assistant, v1';
    note.textContent = fixed ? 'top 5, 33 ms' : 'top 3, 31 ms';
    diag.textContent = 'Following the request through each stage…';
    var stop = fixed ? steps.length : 3;
    for (var i = 0; i < stop; i++) (function (i) {
      later(function () {
        steps[i].classList.add('on');
        if (!fixed && steps[i].hasAttribute('data-fails')) { steps[i].classList.add('fail'); track.classList.add('has-fail'); }
        track.style.setProperty('--prog', (i * 20) + '%');
      }, 500 + i * 550);
    })(i);
    later(function () { finish(fixed); }, 500 + stop * 550 + 250);
    later(function () { run(!fixed); }, fixed ? 9500 : 11000);
  }
  if (reduce) { finish(false); return; }
  run(false);
})();
