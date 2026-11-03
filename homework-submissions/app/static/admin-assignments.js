function restoreFocus(focusTrigger) {
  if (!focusTrigger || !(focusTrigger instanceof HTMLElement) || !focusTrigger.isConnected) {
    return;
  }
  if (focusTrigger.matches(':disabled')) {
    return;
  }
  if (document.querySelector('#app_dialog[open]')) {
    return;
  }
  const active = document.activeElement;
  const inClosedDialog = active && active.closest && active.closest('#app_dialog:not([open])');
  if (!active || active === document.body || inClosedDialog) {
    focusTrigger.focus();
  }
}

document.addEventListener('DOMContentLoaded', () => {
  document.querySelectorAll('.assignment-card').forEach((card) => {
    const deleteBtn = card.querySelector('[data-action="delete"]');
    const cardId = card.dataset.assignmentId;

    if (deleteBtn) {
      deleteBtn.addEventListener('click', async () => {
        const focusTrigger = document.activeElement;
        const topicTitle = card.querySelector('h3')?.textContent || '';
        deleteBtn.disabled = true;
        try {
          const confirmed = await AppDialog.confirm({
            title: 'Удаление занятия',
            message: `Удалить занятие «${topicTitle}»?`,
            hint: 'Это действие также удалит задание к паре.',
            confirmLabel: 'Удалить',
            danger: true,
          });
          if (!confirmed) return;

          const res = await fetch(`/admin/assignments/${cardId}/delete`, {
            method: 'POST',
          });
          if (!res.ok) throw new Error('Delete failed');
          card.remove();
          const list = document.querySelector('[data-assignment-list]');
          if (list && list.querySelectorAll('.assignment-card').length === 0) {
            const journal = document.querySelector('.assignment-journal');
            if (journal) {
              journal.innerHTML = '<div class="empty-state"><p>Для выбранной группы пока не назначено ни одной пары.</p><span class="field__hint">Нажмите «Создать занятие» слева, чтобы запланировать пару.</span></div>';
            }
          }
        } catch {
          await AppDialog.alert({
            title: 'Ошибка удаления',
            message: 'Не удалось удалить занятие.',
          });
        } finally {
          if (card.isConnected) {
            deleteBtn.disabled = false;
            restoreFocus(focusTrigger);
          }
        }
      });
    }
  });
});
