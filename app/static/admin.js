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
  const openCountEl = document.querySelector('#open_count');
  const closedCountEl = document.querySelector('#closed_count');
  if (openCountEl) openCountEl.textContent = openCount;
  if (closedCountEl) closedCountEl.textContent = document.querySelectorAll('.chip').length - openCount;

  if (typeof filterGroupCards === 'function') {
    filterGroupCards();
  }
}

const loginForm = document.querySelector('#admin_login');
if (loginForm) {
  const password = loginForm.querySelector('[name="password"]');
  const passwordToggle = loginForm.querySelector('.password-toggle');

  passwordToggle.addEventListener('click', () => {
    const isVisible = password.type === 'password';
    password.type = isVisible ? 'text' : 'password';
    passwordToggle.setAttribute('aria-pressed', String(isVisible));
    passwordToggle.setAttribute('aria-label', isVisible ? 'Скрыть пароль' : 'Показать пароль');
    passwordToggle.textContent = isVisible ? 'Скрыть пароль' : 'Показать пароль';
  });
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
      setStatus(status, 'error', 'Не удалось сохранить настройки. Проверьте соединение и попробуйте ещё раз.');
    } finally {
      setBusy(form, false, 'Сохранить настройки');
    }
  });
});

const groupSearch = document.querySelector('#group_search');
const groupFilterButtons = document.querySelectorAll('[data-group-filter]');
let currentGroupFilter = 'all';

function filterGroupCards() {
  const query = (groupSearch ? groupSearch.value.trim().toLowerCase() : '');
  document.querySelectorAll('.group-card').forEach((card) => {
    const name = (card.dataset.groupName || card.querySelector('h3')?.textContent || '').toLowerCase();
    const isOpen = card.querySelector('.chip--open') !== null;
    const matchesQuery = !query || name.includes(query);
    const matchesFilter =
      currentGroupFilter === 'all' ||
      (currentGroupFilter === 'open' && isOpen) ||
      (currentGroupFilter === 'closed' && !isOpen);
    card.hidden = !(matchesQuery && matchesFilter);
  });
}

if (groupSearch) {
  groupSearch.addEventListener('input', filterGroupCards);
}

if (groupFilterButtons.length) {
  groupFilterButtons.forEach((btn) => {
    btn.addEventListener('click', () => {
      groupFilterButtons.forEach((b) => {
        b.setAttribute('aria-pressed', 'false');
      });
      btn.setAttribute('aria-pressed', 'true');
      currentGroupFilter = btn.dataset.groupFilter;
      filterGroupCards();
    });
  });
}
