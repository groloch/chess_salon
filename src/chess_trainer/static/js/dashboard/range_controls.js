/**
 * Instant range switching for chart panels (7/30/90 days, all time).
 * Manages aria-pressed and reports the selected range to the caller.
 */
export function initRangeControls(container, onChange) {
  const buttons = [...container.querySelectorAll('[data-days]')];
  let current = buttons.find(button => button.getAttribute('aria-pressed') === 'true')?.dataset.days || '30';
  container.addEventListener('click', (event) => {
    const button = event.target.closest('[data-days]');
    if (!button || button.dataset.days === current) return;
    current = button.dataset.days;
    for (const item of buttons) item.setAttribute('aria-pressed', String(item === button));
    onChange(current);
  });
  return { get value() { return current; } };
}
