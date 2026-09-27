import { Chessground } from '/static/dist/bundle.js';

const statusEl = document.getElementById('history-status');
const contentEl = document.getElementById('history-content');
const daysEl = document.getElementById('history-days');
const statElements = Object.fromEntries(
  [...document.querySelectorAll('[data-stat]')].map(el => [el.dataset.stat, el])
);
const recentTrendEl = document.getElementById('recent-trend');
const settingsDialog = document.getElementById('puzzle-settings-dialog');
const settingsForm = document.getElementById('puzzle-settings-form');
const startButton = document.getElementById('start-puzzle-submit');
const startError = document.getElementById('puzzle-start-error');
const timedSessionInput = document.getElementById('timed-session');
const durationField = document.querySelector('.duration-setting');
const difficultyInputs = [...settingsForm.querySelectorAll('input[name="difficulties"]')];
const depthMinInput = document.getElementById('depth-min');
const depthMaxInput = document.getElementById('depth-max');
const depthMinValue = document.getElementById('depth-min-value');
const depthMaxValue = document.getElementById('depth-max-value');
const mixPreview = document.getElementById('mix-preview');
const presetButtons = [...document.querySelectorAll('[data-preset]')];

const DIFFICULTY_ORDER = ['easiest', 'easier', 'normal', 'harder', 'hardest'];
const DIFFICULTY_LABELS = {
  easiest: 'Easiest', easier: 'Easier', normal: 'Normal', harder: 'Harder', hardest: 'Hardest',
};
const MAX_DEPTH = 15;
const MIX_STORAGE_KEY = 'chessTrainer.puzzleMix';
// Presets deliberately stay within easy/normal difficulties; harder/hardest
// remain selectable by hand but are not offered as quick starts.
const MIX_PRESETS = {
  zen: { difficulties: ['easiest'], depth_min: 5, depth_max: 6 },
  steady: { difficulties: ['easiest', 'easier'], depth_min: 9, depth_max: 11 },
  build: { difficulties: ['easier'], depth_min: 9, depth_max: 11 },
  deep: { difficulties: ['normal'], depth_min: 7, depth_max: 9 },
};

function localDay(isoTimestamp) {
  const date = new Date(isoTimestamp);
  return new Intl.DateTimeFormat(undefined, {
    weekday: 'long', year: 'numeric', month: 'long', day: 'numeric',
  }).format(date);
}

function localTime(isoTimestamp) {
  return new Intl.DateTimeFormat(undefined, {
    hour: 'numeric', minute: '2-digit',
  }).format(new Date(isoTimestamp));
}

function formatDuration(ms) {
  if (!ms) return '—';
  const totalSeconds = Math.round(ms / 1000);
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  if (minutes >= 60) {
    const hours = Math.floor(minutes / 60);
    return `${hours}h ${minutes % 60}m`;
  }
  return `${minutes}:${String(seconds).padStart(2, '0')}`;
}

function readMixFromForm() {
  let depthMin = Number(depthMinInput.value);
  let depthMax = Number(depthMaxInput.value);
  if (depthMin > depthMax) [depthMin, depthMax] = [depthMax, depthMin];
  return {
    difficulties: difficultyInputs.filter(input => input.checked).map(input => input.value),
    depth_min: depthMin,
    depth_max: depthMax,
  };
}

function formatMix(mix) {
  const difficulties = mix.difficulties || [];
  const difficultyLabel = difficulties.length === DIFFICULTY_ORDER.length
    ? 'all difficulties'
    : difficulties.map(mode => DIFFICULTY_LABELS[mode] || mode).join(', ');
  const depthLabel = mix.depth_min === mix.depth_max
    ? `depth ${mix.depth_min}`
    : `depth ${mix.depth_min}–${mix.depth_max}`;
  if (difficulties.length === 1 && mix.depth_min === mix.depth_max) {
    return `Fixed: ${difficultyLabel} · ${depthLabel}`;
  }
  return `Random draw from ${difficultyLabel} · ${depthLabel}`;
}

function updateMixPreview() {
  if (mixPreview) mixPreview.textContent = formatMix(readMixFromForm());
}

function applyMix(mix) {
  if (!mix || !Array.isArray(mix.difficulties)) return;
  const selected = new Set(mix.difficulties);
  difficultyInputs.forEach(input => { input.checked = selected.has(input.value); });
  if (!difficultyInputs.some(input => input.checked)) {
    const fallback = difficultyInputs.find(input => input.value === 'normal') || difficultyInputs[0];
    if (fallback) fallback.checked = true;
  }
  if (Number.isFinite(mix.depth_min)) {
    depthMinInput.value = String(Math.min(MAX_DEPTH, Math.max(1, mix.depth_min)));
  }
  if (Number.isFinite(mix.depth_max)) {
    depthMaxInput.value = String(Math.min(MAX_DEPTH, Math.max(1, mix.depth_max)));
  }
  depthMinValue.textContent = depthMinInput.value;
  depthMaxValue.textContent = depthMaxInput.value;
  updateMixPreview();
}

function saveMix() {
  try {
    localStorage.setItem(MIX_STORAGE_KEY, JSON.stringify(readMixFromForm()));
  } catch {
    // Ignore storage failures (private mode, disabled storage).
  }
}

function displayStats(stats) {
  const hasData = (stats.completed ?? 0) > 0;
  for (const [key, element] of Object.entries(statElements)) {
    const value = stats[key] ?? 0;
    const isRate = key.endsWith('_rate');
    if (!hasData && (isRate || key.endsWith('_ms') || key === 'performance_rating')) {
      element.textContent = '—';
    } else if (isRate) {
      element.textContent = `${value}%`;
    } else if (key.endsWith('_ms')) {
      element.textContent = formatDuration(value);
    } else {
      element.textContent = `${value}`;
    }
  }
  if (recentTrendEl) {
    const trend = stats.recent_trend;
    if (!hasData || trend === null || trend === undefined || trend === 0) {
      recentTrendEl.textContent = '';
      recentTrendEl.className = 'stat-trend';
    } else {
      const up = trend > 0;
      recentTrendEl.textContent = `${up ? '▲' : '▼'} ${Math.abs(trend)}`;
      recentTrendEl.className = `stat-trend ${up ? 'up' : 'down'}`;
    }
  }
}

function makePuzzleCard(entry) {
  const link = document.createElement('a');
  link.className = 'puzzle-card';
  link.href = `https://lichess.org/training/${encodeURIComponent(entry.puzzle_id)}`;
  link.target = '_blank';
  link.rel = 'noopener noreferrer';
  link.setAttribute('aria-label', `${entry.success ? 'Solved' : 'Failed'} puzzle rated ${entry.rating}, ${localTime(entry.completed_at)}. Open on Lichess.`);

  const boardEl = document.createElement('div');
  boardEl.className = 'mini-board';
  const ground = Chessground(boardEl, {
    fen: entry.fen,
    orientation: entry.orientation,
    coordinates: false,
    viewOnly: true,
    animation: { enabled: false },
  });

  const info = document.createElement('div');
  info.className = 'puzzle-card-info';
  const top = document.createElement('div');
  top.className = 'puzzle-card-top';
  const result = document.createElement('span');
  const outcome = entry.outcome || (entry.success ? 'success' : 'failure');
  result.className = `puzzle-result ${outcome}`;
  result.textContent = outcome === 'success' ? '✓ Solved' : outcome === 'expired' ? '◷ Time' : '✕ Missed';
  const rating = document.createElement('span');
  rating.className = 'puzzle-rating';
  rating.textContent = `♟ ${entry.rating}`;
  top.append(result, rating);
  const time = document.createElement('time');
  time.className = 'puzzle-time';
  time.dateTime = entry.completed_at;
  time.textContent = entry.duration_ms
    ? `${localTime(entry.completed_at)} · ${formatDuration(entry.duration_ms)}`
    : localTime(entry.completed_at);
  info.append(top, time);
  link.append(boardEl, info);
  return link;
}

function makeSessionFrame(session, entries) {
  const frame = document.createElement('section');
  frame.className = 'timed-history-frame';
  const header = document.createElement('header');
  header.className = 'timed-history-header';
  const details = document.createElement('div');
  details.className = 'timed-history-details';
  const title = document.createElement('strong');
  title.textContent = `${Math.round(session.duration_seconds / 60)} minute session`;
  const settings = document.createElement('span');
  settings.textContent = formatMix(session.profile || {
    difficulties: [session.mode],
    depth_min: session.blindfold_depth,
    depth_max: session.blindfold_depth,
  });
  const when = document.createElement('time');
  when.dateTime = session.started_at;
  when.textContent = `${localTime(session.started_at)}${session.ended_at ? ` – ${localTime(session.ended_at)}` : ''}`;
  details.append(title, settings, when);
  const stats = document.createElement('div');
  stats.className = 'timed-history-stats';
  const visibleExpiredCount = Math.max(
    session.expired_count,
    entries.filter(entry => entry.outcome === 'expired').length,
  );
  stats.textContent = `${session.completed_count} completed · ${session.successes} solved · ${session.failures} missed · ${visibleExpiredCount} timed out`;
  header.append(details, stats);
  const grid = document.createElement('div');
  grid.className = 'puzzle-grid';
  for (const entry of entries) grid.appendChild(makePuzzleCard(entry));
  if (!entries.length) {
    const placeholder = document.createElement('p');
    placeholder.className = 'session-no-puzzles';
    placeholder.textContent = 'Session ended before any puzzle was completed.';
    grid.appendChild(placeholder);
  }
  frame.append(header, grid);
  return frame;
}

function renderHistory(entries, sessions) {
  const groupedDays = new Map();
  const ensureDay = (date) => {
    const key = new Date(date).toLocaleDateString();
    if (!groupedDays.has(key)) groupedDays.set(key, { date, entries: [], sessions: new Map() });
    return groupedDays.get(key);
  };

  for (const session of sessions || []) {
    ensureDay(session.started_at).sessions.set(session.id, { session, entries: [] });
  }
  for (const entry of entries) {
    if (entry.session_id) {
      const session = (sessions || []).find(item => item.id === entry.session_id);
      if (session) {
        ensureDay(session.started_at).sessions.get(session.id).entries.push(entry);
        continue;
      }
    }
    ensureDay(entry.completed_at).entries.push(entry);
  }

  daysEl.replaceChildren();
  const days = [...groupedDays.values()].sort((a, b) => new Date(b.date) - new Date(a.date));
  for (const day of days) {
    const section = document.createElement('section');
    section.className = 'history-day';
    const heading = document.createElement('h3');
    const date = document.createElement('span');
    date.textContent = localDay(day.date);
    const dayCount = day.entries.length + [...day.sessions.values()].reduce(
      (total, group) => total + group.entries.length, 0,
    );
    const count = document.createElement('span');
    count.className = 'day-count';
    count.textContent = `${dayCount} ${dayCount === 1 ? 'puzzle' : 'puzzles'}`;
    heading.append(date, count);
    section.appendChild(heading);

    if (day.entries.length) {
      const grid = document.createElement('div');
      grid.className = 'puzzle-grid';
      for (const entry of day.entries) grid.appendChild(makePuzzleCard(entry));
      section.appendChild(grid);
    }
    for (const { session, entries: sessionEntries } of day.sessions.values()) {
      section.appendChild(makeSessionFrame(session, sessionEntries));
    }
    daysEl.appendChild(section);
  }
}

function openSettings() {
  startError.hidden = true;
  startError.textContent = '';
  settingsDialog.showModal();
}

async function startPuzzle(event) {
  event.preventDefault();
  startError.hidden = true;
  startError.textContent = '';

  const mix = readMixFromForm();
  if (!mix.difficulties.length) {
    startError.textContent = 'Choose at least one difficulty.';
    startError.hidden = false;
    return;
  }

  startButton.disabled = true;
  startButton.textContent = 'Fetching puzzle…';

  const formData = new FormData(settingsForm);
  const payload = {
    difficulties: mix.difficulties,
    depth_min: mix.depth_min,
    depth_max: mix.depth_max,
    timed: timedSessionInput.checked,
    duration_minutes: Number(formData.get('duration_minutes')),
  };

  try {
    const response = await fetch('/api/puzzles/start', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || 'Could not start a puzzle.');
    saveMix();
    window.location.assign(result.redirect_url);
  } catch (error) {
    startError.textContent = error.message || 'Could not start a puzzle. Please try again.';
    startError.hidden = false;
    startButton.disabled = false;
    startButton.textContent = 'Start training';
  }
}

async function loadHistory() {
  try {
    const response = await fetch('/api/puzzles/history');
    if (!response.ok) throw new Error(`Request failed (${response.status})`);
    const payload = await response.json();
    displayStats(payload.stats);

    if (!payload.history.length) {
      statusEl.textContent = payload.sessions?.length
        ? 'No completed puzzles yet. Timed sessions will appear here when they finish.'
        : 'No completed puzzles yet. Finish a blindfold puzzle and it will show up here.';
    }
    if (!payload.history.length && !payload.sessions?.length) return;

    renderHistory(payload.history, payload.sessions);
    statusEl.hidden = true;
    contentEl.hidden = false;
  } catch (error) {
    console.error('Could not load puzzle history:', error);
    statusEl.textContent = 'Puzzle history could not be loaded. Please refresh to try again.';
    statusEl.classList.add('error');
  }
}

timedSessionInput.addEventListener('change', () => {
  durationField.hidden = !timedSessionInput.checked;
});
document.getElementById('open-puzzle-settings').addEventListener('click', openSettings);
document.getElementById('close-puzzle-settings').addEventListener('click', () => settingsDialog.close());
document.getElementById('cancel-puzzle-settings').addEventListener('click', () => settingsDialog.close());
settingsDialog.addEventListener('click', (event) => {
  if (event.target === settingsDialog) settingsDialog.close();
});
settingsForm.addEventListener('submit', startPuzzle);

difficultyInputs.forEach(input => input.addEventListener('change', updateMixPreview));
depthMinInput.addEventListener('input', () => {
  if (Number(depthMinInput.value) > Number(depthMaxInput.value)) {
    depthMaxInput.value = depthMinInput.value;
  }
  depthMinValue.textContent = depthMinInput.value;
  depthMaxValue.textContent = depthMaxInput.value;
  updateMixPreview();
});
depthMaxInput.addEventListener('input', () => {
  if (Number(depthMaxInput.value) < Number(depthMinInput.value)) {
    depthMinInput.value = depthMaxInput.value;
  }
  depthMinValue.textContent = depthMinInput.value;
  depthMaxValue.textContent = depthMaxInput.value;
  updateMixPreview();
});
presetButtons.forEach(button => button.addEventListener('click', () => {
  const preset = MIX_PRESETS[button.dataset.preset];
  if (preset) applyMix(preset);
}));

// Restore the last-used mix so returning players keep their settings.
try {
  const savedMix = JSON.parse(localStorage.getItem(MIX_STORAGE_KEY) || 'null');
  if (savedMix) applyMix(savedMix);
} catch {
  // Ignore malformed or unavailable storage.
}
updateMixPreview();

loadHistory();
