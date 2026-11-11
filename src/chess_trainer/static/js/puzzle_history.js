/**
 * Blindfold puzzle dashboard.
 *
 * The initial layout, settings, and history are rendered server-side; this
 * module wires the small shared dashboard modules (charts, range controls,
 * board previews, dialogs, timestamps) and keeps the trainer-specific
 * settings form and start request.
 */
import { renderLineCharts } from '/static/js/dashboard/charts.js';
import { initRangeControls } from '/static/js/dashboard/range_controls.js';
import { initBoardPreviews } from '/static/js/dashboard/board_previews.js';
import { initDeletionDialog } from '/static/js/dashboard/deletion_dialog.js';
import { initDialogs } from '/static/js/dashboard/dialog.js';
import { localizeTimestamps } from '/static/js/dashboard/timestamps.js';

// Chart rows are embedded by the template (tojson); range switching is instant.
const progressData = document.getElementById('progress-data');
const progress = progressData ? JSON.parse(progressData.textContent) : [];
const chartContainer = document.getElementById('progress-charts');
const rangeControls = document.querySelector('.range-controls');

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

const PROGRESS_CHARTS = [
  { key: 'performance_rating', label: 'Performance', format: value => String(Math.round(value)), pad: 50 },
  { key: 'average_rating', label: 'Average puzzle rating', format: value => String(Math.round(value)), pad: 50 },
  { key: 'average_solve_time_ms', label: 'Average solve time', format: formatDuration, pad: 10000 },
];

if (chartContainer && rangeControls) {
  const controls = initRangeControls(rangeControls, days => {
    renderLineCharts(chartContainer, PROGRESS_CHARTS, progress, days);
  });
  renderLineCharts(chartContainer, PROGRESS_CHARTS, progress, controls.value);
}

initBoardPreviews();
localizeTimestamps();

const confirmDeletion = initDeletionDialog(document.getElementById('deletion-dialog'));
const settingsDialog = document.getElementById('puzzle-settings-dialog');
const settingsForm = document.getElementById('puzzle-settings-form');
const startButton = document.getElementById('start-puzzle-submit');
const startError = document.getElementById('puzzle-start-error');
const timedSessionInput = document.getElementById('timed-session');
const durationField = document.querySelector('.duration-setting');
const difficultyInputs = [...settingsForm.querySelectorAll('input[name="difficulties"]')];
const depthMinInput = document.getElementById('depth-min');
const depthMaxInput = document.getElementById('depth-max');
const depthRange = document.getElementById('depth-range');
const depthMinValue = document.getElementById('depth-min-value');
const depthMaxValue = document.getElementById('depth-max-value');
const presetChips = document.getElementById('preset-chips');
const addPresetButton = document.getElementById('add-preset-button');
const presetSaveRow = document.getElementById('preset-save-row');
const presetNameInput = document.getElementById('preset-name-input');
const presetSaveConfirm = document.getElementById('preset-save-confirm');
const presetError = document.getElementById('preset-error');

const MAX_DEPTH = 15;
const MIX_STORAGE_KEY = 'chessTrainer.puzzleMix';

function loginUrl() {
  const next = window.location.pathname + window.location.search;
  return `/auth/login?next=${encodeURIComponent(next)}`;
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

function updateDepthRange() {
  const min = Number(depthMinInput.value);
  const max = Number(depthMaxInput.value);
  depthMinValue.textContent = min;
  depthMaxValue.textContent = max;
  depthRange.style.setProperty('--range-min', `${(min - 1) / (MAX_DEPTH - 1) * 100}%`);
  depthRange.style.setProperty('--range-max', `${(max - 1) / (MAX_DEPTH - 1) * 100}%`);
  depthRange.classList.toggle('is-single', min === max);
  depthMinInput.setAttribute('aria-valuetext', `${min}, minimum depth`);
  depthMaxInput.setAttribute('aria-valuetext', `${max}, maximum depth`);
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
  updateDepthRange();
}

function saveMix() {
  try {
    localStorage.setItem(MIX_STORAGE_KEY, JSON.stringify(readMixFromForm()));
  } catch {
    // Ignore storage failures (private mode, disabled storage).
  }
}

// Per-user presets, managed in the start dialog where they are applied.
function showPresetError(message) {
  presetError.textContent = message;
  presetError.hidden = !message;
}

function renderPresets(presets) {
  presetChips.replaceChildren();
  for (const preset of presets) {
    const wrap = document.createElement('span');
    wrap.className = 'preset-chip-wrap';
    const chip = document.createElement('button');
    chip.type = 'button';
    chip.className = 'preset-chip';
    chip.textContent = preset.name;
    chip.addEventListener('click', () => applyMix(preset.profile));
    const remove = document.createElement('button');
    remove.type = 'button';
    remove.className = 'preset-chip-delete';
    remove.setAttribute('aria-label', `Delete preset ${preset.name}`);
    remove.textContent = '×';
    remove.addEventListener('click', () => confirmDeletion(preset.name, () => deletePreset(preset.id)));
    wrap.append(chip, remove);
    presetChips.append(wrap);
  }
}

async function loadPresets() {
  try {
    const response = await fetch('/api/puzzles/presets');
    if (!response.ok) return; // 401 mid-session is handled by checkSignIn.
    const payload = await response.json();
    renderPresets(payload.presets || []);
  } catch {
    // Transient failure: leave the preset row as-is.
  }
}

async function deletePreset(presetId) {
  const response = await fetch(`/api/puzzles/presets/${encodeURIComponent(presetId)}`, { method: 'DELETE' });
  if (!response.ok) throw new Error('Could not delete preset');
  await loadPresets();
}

function togglePresetSaveRow(show) {
  presetSaveRow.hidden = !show;
  addPresetButton.hidden = show;
  if (show) presetNameInput.focus();
}

async function savePreset() {
  const name = presetNameInput.value.trim();
  if (!name) {
    showPresetError('Enter a preset name.');
    return;
  }
  try {
    const response = await fetch('/api/puzzles/presets', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name, ...readMixFromForm() }),
    });
    const result = await response.json();
    if (!response.ok) {
      showPresetError(result.error || 'Could not save the preset.');
      return;
    }
    presetNameInput.value = '';
    showPresetError('');
    togglePresetSaveRow(false);
    await loadPresets();
  } catch {
    showPresetError('Could not save the preset. Please try again.');
  }
}

function showSignInRequired(element) {
  element.textContent = '';
  const link = document.createElement('a');
  link.href = loginUrl();
  link.textContent = 'Sign in with Lichess to start training.';
  element.appendChild(link);
  element.hidden = false;
  startButton.disabled = false;
  startButton.textContent = 'Start training';
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
    if (!response.ok) {
      if (result.error === 'sign_in_required') {
        showSignInRequired(startError);
      } else {
        throw new Error(result.error || 'Could not start a puzzle.');
      }
      return;
    }
    saveMix();
    window.location.assign(result.redirect_url);
  } catch (error) {
    startError.textContent = error.message || 'Could not start a puzzle. Please try again.';
    startError.hidden = false;
    startButton.disabled = false;
    startButton.textContent = 'Start training';
  }
}

// Mid-session sign-out (token revoked while the page is open): keep an inline
// sign-in link in the status area instead of reloading — see
// _guidelines/_not_authentificated.md.
async function checkSignIn() {
  const statusEl = document.getElementById('history-status');
  if (!statusEl) return;
  try {
    const response = await fetch('/api/puzzles/history');
    if (response.status === 401) {
      statusEl.replaceChildren();
      const link = document.createElement('a');
      link.href = loginUrl();
      link.textContent = 'Sign in with Lichess to see your puzzle history and start training.';
      statusEl.appendChild(link);
      statusEl.hidden = false;
    }
  } catch {
    // Transient failure: keep the server-rendered content.
  }
}

initDialogs({
  onOpen(dialog) {
    if (dialog !== settingsDialog) return;
    startError.hidden = true;
    startError.textContent = '';
  },
});

timedSessionInput.addEventListener('change', () => {
  durationField.hidden = !timedSessionInput.checked;
});
settingsForm.addEventListener('submit', startPuzzle);

depthMinInput.addEventListener('input', () => {
  if (Number(depthMinInput.value) > Number(depthMaxInput.value)) {
    depthMinInput.value = depthMaxInput.value;
  }
  depthMinValue.textContent = depthMinInput.value;
  depthMaxValue.textContent = depthMaxInput.value;
  updateDepthRange();
});
depthMaxInput.addEventListener('input', () => {
  if (Number(depthMaxInput.value) < Number(depthMinInput.value)) {
    depthMaxInput.value = depthMinInput.value;
  }
  depthMinValue.textContent = depthMinInput.value;
  depthMaxValue.textContent = depthMaxInput.value;
  updateDepthRange();
});
depthRange.addEventListener('pointerdown', (event) => {
  if (event.target.matches('input') || event.button !== 0) return;
  const bounds = depthRange.getBoundingClientRect();
  const fraction = Math.max(0, Math.min(1, (event.clientX - bounds.left - 10) / (bounds.width - 20)));
  const value = Math.round(1 + fraction * (MAX_DEPTH - 1));
  const min = Number(depthMinInput.value);
  const max = Number(depthMaxInput.value);
  const input = Math.abs(value - min) < Math.abs(value - max) || value < min
    ? depthMinInput : depthMaxInput;
  input.value = value;
  input.dispatchEvent(new Event('input', { bubbles: true }));
  input.focus();
});
addPresetButton.addEventListener('click', () => togglePresetSaveRow(presetSaveRow.hidden));
presetSaveConfirm.addEventListener('click', savePreset);
presetNameInput.addEventListener('keydown', (event) => {
  if (event.key === 'Enter') {
    event.preventDefault();
    savePreset();
  } else if (event.key === 'Escape') {
    togglePresetSaveRow(false);
  }
});

// Restore the last-used mix so returning players keep their settings.
try {
  const savedMix = JSON.parse(localStorage.getItem(MIX_STORAGE_KEY) || 'null');
  if (savedMix) applyMix(savedMix);
} catch {
  // Ignore malformed or unavailable storage.
}
updateDepthRange();

loadPresets();
checkSignIn();
