function showStatus(element, state, message) {
  element.dataset.state = state;
  element.textContent = message;
}

function setFormBusy(form, busy, buttonLabel) {
  const button = form.querySelector('button[type="submit"]');
  button.disabled = busy;
  if (buttonLabel) button.textContent = buttonLabel;
}

function errorMessage(response) {
  if (response.status === 400) return 'Такое название уже существует или оно некорректно. Проверьте название и попробуйте снова.';
  if (response.status === 404) return 'Элемент больше не найден. Обновите страницу и повторите действие.';
  return 'Не удалось сохранить изменения. Попробуйте ещё раз.';
}

function makeRow(kind, item) {
  const row = document.createElement('article');
  row.className = 'panel catalog-row';
  row.dataset.catalogItem = '';
  row.dataset.kind = kind;
  row.dataset.id = item.id;
  row.dataset.name = item.name;

  const heading = document.createElement('div');
  heading.className = 'catalog-row__heading';
  const title = document.createElement('h3');
  title.dataset.itemName = '';
  title.textContent = item.name;
  heading.append(title);

  const form = document.createElement('form');
  form.className = 'catalog-rename form-stack';
  form.dataset.catalogRename = '';
  form.method = 'post';
  form.action = `/admin/${encodeURIComponent(kind)}/${encodeURIComponent(item.id)}/rename`;
  const label = document.createElement('label');
  label.className = 'field';
  const inputId = `catalog_name_${item.id}`;
  label.htmlFor = inputId;
  label.append(document.createTextNode('Новое название'));
  const input = document.createElement('input');
  input.id = inputId;
  input.name = 'name';
  input.value = item.name;
  input.required = true;
  input.maxLength = 200;
  input.autocomplete = 'off';
  label.append(input);
  form.append(label);

  const actions = document.createElement('div');
  actions.className = 'catalog-actions';
  const renameButton = document.createElement('button');
  renameButton.className = 'button button--outline';
  renameButton.type = 'submit';
  renameButton.textContent = 'Переименовать';
  const deleteButton = document.createElement('button');
  deleteButton.className = 'button button--danger';
  deleteButton.type = 'button';
  deleteButton.dataset.delete = '';
  deleteButton.textContent = 'Удалить';
  actions.append(renameButton, deleteButton);
  form.append(actions);

  const status = document.createElement('p');
  status.className = 'catalog-status status';
  status.setAttribute('aria-live', 'polite');
  row.append(heading, form, status);
  return row;
}

function insertRow(list, row) {
  list.querySelector('[data-catalog-empty]')?.remove();
  list.append(row);
}

function setRowName(row, name) {
  row.dataset.name = name;
  row.querySelector('[data-item-name]').textContent = name;
  row.querySelector('input[name="name"]').value = name;
}

const createForm = document.querySelector('[data-catalog-create]');
const catalogList = document.querySelector('[data-catalog-list]');

if (createForm && catalogList) {
  const kind = createForm.dataset.kind;
  const status = createForm.querySelector('[data-create-status]');

  createForm.addEventListener('submit', async (event) => {
    event.preventDefault();
    setFormBusy(createForm, true, 'Добавляем…');
    showStatus(status, 'working', 'Добавление…');
    try {
      const response = await fetch(createForm.action, { method: 'POST', body: new FormData(createForm) });
      if (!response.ok) {
        showStatus(status, 'error', errorMessage(response));
        return;
      }
      const item = await response.json();
      insertRow(catalogList, makeRow(kind, item));
      createForm.reset();
      showStatus(status, 'success', `Добавлено: ${item.name}.`);
    } catch {
      showStatus(status, 'error', 'Не удалось связаться с сервером. Попробуйте ещё раз.');
    } finally {
      setFormBusy(createForm, false, 'Добавить');
    }
  });
}

if (catalogList) {
  catalogList.addEventListener('submit', async (event) => {
    const form = event.target.closest('[data-catalog-rename]');
    if (!form) return;
    event.preventDefault();
    const row = form.closest('[data-catalog-item]');
    const status = row.querySelector('.catalog-status');
    setFormBusy(form, true, 'Сохраняем…');
    showStatus(status, 'working', 'Переименование…');
    try {
      const response = await fetch(form.action, { method: 'POST', body: new FormData(form) });
      if (!response.ok) {
        showStatus(status, 'error', errorMessage(response));
        return;
      }
      const item = await response.json();
      setRowName(row, item.name);
      showStatus(status, 'success', `Название изменено на «${item.name}».`);
    } catch {
      showStatus(status, 'error', 'Не удалось связаться с сервером. Попробуйте ещё раз.');
    } finally {
      setFormBusy(form, false, 'Переименовать');
    }
  });

  catalogList.addEventListener('click', async (event) => {
    const button = event.target.closest('[data-delete]');
    if (!button) return;
    const row = button.closest('[data-catalog-item]');
    const name = row.dataset.name;
    if (!window.confirm(`Удалить «${name}»? Это действие нельзя отменить.`)) return;

    const status = row.querySelector('.catalog-status');
    const form = row.querySelector('[data-catalog-rename]');
    const controls = [...form.querySelectorAll('button, input')];
    controls.forEach((control) => { control.disabled = true; });
    showStatus(status, 'working', 'Удаление…');
    try {
      const response = await fetch(`/admin/${encodeURIComponent(row.dataset.kind)}/${encodeURIComponent(row.dataset.id)}/delete`, { method: 'POST' });
      if (!response.ok) {
        showStatus(status, 'error', errorMessage(response));
        return;
      }
      row.remove();
      if (!catalogList.querySelector('[data-catalog-item]')) {
        const empty = document.createElement('p');
        empty.className = 'empty-state';
        empty.dataset.catalogEmpty = '';
        empty.textContent = 'Пока нет элементов. Добавьте первый в форме выше.';
        catalogList.append(empty);
      }
    } catch {
      showStatus(status, 'error', 'Не удалось связаться с сервером. Попробуйте ещё раз.');
    } finally {
      controls.forEach((control) => { control.disabled = false; });
    }
  });
}
