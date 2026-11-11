/**
 * Shared dialog behavior: open triggers ([data-dialog-open]), close buttons
 * ([data-dialog-close]), and backdrop clicks. Trainer-specific setup hooks in
 * via onOpen.
 */
export function initDialogs({ onOpen } = {}) {
  const dialogs = new Map(
    [...document.querySelectorAll('dialog')].map(dialog => [dialog.id, dialog])
  );
  document.querySelectorAll('[data-dialog-open]').forEach(button => {
    button.addEventListener('click', () => {
      const dialog = dialogs.get(button.dataset.dialogOpen);
      if (!dialog) return;
      if (onOpen) onOpen(dialog);
      dialog.showModal();
    });
  });
  for (const dialog of dialogs.values()) {
    dialog.querySelectorAll('[data-dialog-close]').forEach(button => {
      button.addEventListener('click', () => dialog.close());
    });
    dialog.addEventListener('click', (event) => {
      if (event.target === dialog) dialog.close();
    });
  }
}
