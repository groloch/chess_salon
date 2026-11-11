/** Bind a confirmation template and return an opener accepting a deletion callback. */
export function initDeletionDialog(dialog) {
  const name = dialog.querySelector('[data-deletion-name]');
  const confirm = dialog.querySelector('[data-deletion-confirm]');
  const error = dialog.querySelector('[data-deletion-error]');
  let onDelete = null;
  let deleting = false;

  dialog.addEventListener('close', () => { onDelete = null; });
  confirm.addEventListener('click', async () => {
    if (!onDelete || deleting) return;
    const callback = onDelete;
    deleting = true;
    confirm.disabled = true;
    error.hidden = true;
    try {
      await callback();
      if (dialog.open) dialog.close();
    } catch {
      if (dialog.open) {
        error.textContent = 'Could not delete the preset. Please try again.';
        error.hidden = false;
      }
    } finally {
      deleting = false;
      confirm.disabled = false;
    }
  });

  return (presetName, callback) => {
    if (deleting) return;
    name.textContent = presetName;
    error.hidden = true;
    onDelete = callback;
    dialog.showModal();
  };
}
