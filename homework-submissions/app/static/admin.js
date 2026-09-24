function setStatus(element, state, message) {
  element.dataset.state = state;
  element.textContent = message;
}

function setBusy(form, busy, label) {
  const button = form.querySelector('button[type="submit"]');
  button.disabled = busy;
  button.textContent = label;
}

function updateGroupState(form) {
  const card = form.closest('.group-card');
  const chip = card.querySelector('.chip');
  const isOpen = form.elements.is_open.checked;

  chip.className = `chip ${isOpen ? 'chip--open' : 'chip--closed'}`;
  chip.textContent = isOpen ? 'Приём открыт' : 'Приём закрыт';

  const openCount = document.querySelectorAll('.chip--open').length;
  document.querySelector('#open_count').textContent = openCount;
  document.querySelector('#closed_count').textContent = document.querySelectorAll('.chip').length - openCount;
}

const loginForm = document.querySelector('#admin_login');
if (loginForm) {
  const status = document.querySelector('#admin_login_status');

  loginForm.addEventListener('submit', async (event) => {
    event.preventDefault();
    setBusy(loginForm, true, 'Входим…');
    setStatus(status, 'working', 'Проверяем пароль…');

    try {
      const response = await fetch('/admin/login', { method: 'POST', body: new FormData(loginForm) });
      if (!response.ok) throw new Error('denied');
      window.location.reload();
    } catch {
      setStatus(status, 'error', 'Неверный мастер-пароль.');
      setBusy(loginForm, false, 'Войти в панель');
    }
  });
}

document.querySelectorAll('.group-form').forEach((form) => {
  const status = form.closest('.group-card').querySelector('.group-status');

  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    setBusy(form, true, 'Сохраняем…');
    setStatus(status, 'working', 'Сохранение…');

    try {
      const response = await fetch('/admin/access', { method: 'POST', body: new FormData(form) });
      if (!response.ok) throw new Error('failed');
      setStatus(status, 'success', 'Настройки группы сохранены.');
      updateGroupState(form);
    } catch {
      setStatus(status, 'error', 'Ошибка сохранения настроек.');
    } finally {
      setBusy(form, false, 'Сохранить');
    }
  });
});
