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

function getCsrfToken() {
  const meta = document.querySelector('meta[name="csrf-token"]');
  return meta ? meta.getAttribute('content') : '';
}

document.addEventListener('DOMContentLoaded', () => {
  document.querySelectorAll('.assignment-card').forEach((card) => {
    const cardId = card.dataset.assignmentId;
    const deleteBtn = card.querySelector('button[data-action="archive"]') || card.querySelector('button[data-action="delete"]');

    if (deleteBtn) {
      deleteBtn.addEventListener('click', async () => {
        const focusTrigger = document.activeElement;
        const topicTitle = card.querySelector('h3')?.textContent || '';
        deleteBtn.disabled = true;

        const headers = { 'Accept': 'application/json' };
        const csrfToken = getCsrfToken();
        if (csrfToken) headers['X-CSRFToken'] = csrfToken;

        try {
          let confirmed = false;
          if (typeof AppDialog !== 'undefined' && AppDialog.confirm) {
            confirmed = await AppDialog.confirm({
              title: 'Архивация занятия',
              message: `Архивировать занятие «${topicTitle}»?`,
              hint: 'Это действие также архивирует задание к паре.',
              confirmLabel: 'Архивировать',
              danger: true,
            });
          } else {
            confirmed = window.confirm(`Архивировать занятие «${topicTitle}»?`);
          }
          if (!confirmed) return;

          const res = await fetch(`/admin/assignments/${cardId}/archive`, {
            method: 'POST',
            headers,
          });
          if (!res.ok) {
            const body = await res.json().catch(() => ({}));
            if (body.error === 'csrf_error') {
              throw new Error(body.message || 'Сессия устарела. Обновите страницу и повторите действие.');
            }
            throw new Error('Archive failed');
          }
          card.remove();
          const list = document.querySelector('[data-assignment-list]');
          if (list && list.querySelectorAll('.assignment-card').length === 0) {
            const journal = document.querySelector('.assignment-journal');
            if (journal) {
              journal.innerHTML = '<div class="empty-state"><p>Для выбранной группы пока не назначено ни одной пары.</p><span class="field__hint">Нажмите «Создать занятие» слева, чтобы запланировать пару.</span></div>';
            }
          }
        } catch (err) {
          if (typeof AppDialog !== 'undefined' && AppDialog.alert) {
            await AppDialog.alert({
              title: 'Ошибка архивации',
              message: err?.message || 'Не удалось архивировать занятие.',
            });
          } else {
            alert(err?.message || 'Не удалось архивировать занятие.');
          }
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
