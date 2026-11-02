function showStatus(element, state, message) {
  element.dataset.state = state;
  element.textContent = message;
}

function setFormBusy(form, busy, buttonLabel) {
  const button = form.querySelector('button[type="submit"]');
  button.disabled = busy;
  if (buttonLabel) button.textContent = buttonLabel;
}

async function errorMessage(response) {
  let payload = {};
  try {
    payload = await response.json();
  } catch {
    // Non-JSON errors use the standard status message below.
  }
  if (Array.isArray(payload.names) && payload.names.length) {
    return `Нельзя выполнить действие: конфликтуют темы «${payload.names.join('», «')}».`;
  }
  if (response.status === 400) return 'Такое название уже существует или оно некорректно. Проверьте название и попробуйте снова.';
  if (response.status === 404) return 'Элемент больше не найден. Обновите страницу и повторите действие.';
  return 'Не удалось сохранить изменения. Попробуйте ещё раз.';
}

function subjectOptions() {
  const select = document.querySelector('#catalog_new_subject');
  if (!select) return [];
  return [...select.options]
    .filter((option) => Boolean(option.value))
    .map((option) => ({ id: option.value, name: option.textContent.trim() }));
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
  if (kind === 'topics') {
    row.dataset.subjectId = item.subject_id;
    const selection = document.createElement('label');
    selection.className = 'topic-selection';
    const checkbox = document.createElement('input');
    checkbox.type = 'checkbox';
    checkbox.name = 'topic_ids';
    checkbox.value = item.id;
    checkbox.setAttribute('form', 'topic_bulk');
    checkbox.setAttribute('aria-label', `Выбрать тему ${item.name}`);
    selection.append(checkbox);
    heading.append(selection);
  }

  const titleWrap = document.createElement('div');
  titleWrap.style.flex = '1 1 auto';
  const title = document.createElement('h3');
  title.dataset.itemName = '';
  title.textContent = item.name;
  titleWrap.append(title);

  if (kind === 'topics') {
    const subjectName = subjectOptions().find((subject) => subject.id === item.subject_id)?.name || '';
    const subject = document.createElement('p');
    subject.className = 'field__hint';
    subject.dataset.topicSubject = '';
    subject.textContent = subjectName;
    titleWrap.append(subject);
  }
  heading.append(titleWrap);

  const status = document.createElement('p');
  status.className = 'catalog-status status';
  status.dataset.createStatus = '';
  status.setAttribute('aria-live', 'polite');

  const headingActions = document.createElement('div');
  headingActions.className = 'catalog-actions';
  headingActions.style.marginLeft = 'auto';
  const toggleEditButton = document.createElement('button');
  toggleEditButton.className = 'button button--outline';
  toggleEditButton.type = 'button';
  toggleEditButton.textContent = 'Редактировать';
  const deleteButton = document.createElement('button');
  deleteButton.className = 'button button--danger';
  deleteButton.type = 'button';
  deleteButton.textContent = 'Удалить';

  if (kind === 'subjects' && item.id === catalogList.dataset.unassignedId) {
    toggleEditButton.disabled = true;
    toggleEditButton.title = 'Системный предмет нельзя изменить';

    deleteButton.disabled = true;
    deleteButton.title = 'Системный предмет нельзя удалить';

    const specialNotice = document.createElement('p');
    specialNotice.className = 'field__hint';
    specialNotice.style.color = 'var(--color-warning)';
    specialNotice.textContent = 'Системный предмет для тем, ожидающих распределения. Его нельзя переименовать или удалить.';
    titleWrap.append(specialNotice);

    headingActions.append(toggleEditButton, deleteButton);
    heading.append(headingActions);
    row.append(heading, status);
    return row;
  }

  toggleEditButton.dataset.toggleEdit = '';
  deleteButton.dataset.delete = '';
  headingActions.append(toggleEditButton, deleteButton);
  heading.append(headingActions);
  const form = document.createElement('form');
  form.className = 'catalog-rename form-stack';
  form.dataset.catalogRename = '';
  form.method = 'post';
  form.action = `/admin/${encodeURIComponent(kind)}/${encodeURIComponent(item.id)}/${kind === 'topics' ? 'update' : 'rename'}`;
  form.hidden = true;

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

  if (kind === 'topics') {
    const subjectLabel = document.createElement('label');
    subjectLabel.className = 'field';
    const subjectId = `catalog_subject_${item.id}`;
    subjectLabel.htmlFor = subjectId;
    subjectLabel.append(document.createTextNode('Предмет'));
    const select = document.createElement('select');
    select.id = subjectId;
    select.name = 'subject_id';
    select.required = true;
    subjectOptions().forEach(({ id, name }) => {
      const option = document.createElement('option');
      option.value = id;
      option.textContent = name;
      option.selected = id === item.subject_id;
      select.append(option);
    });
    subjectLabel.append(select);
    form.append(subjectLabel);
  }

  const actions = document.createElement('div');
  actions.className = 'catalog-actions';
  const saveButton = document.createElement('button');
  saveButton.className = 'button button--primary';
  saveButton.type = 'submit';
  saveButton.textContent = 'Сохранить';
  const cancelEditButton = document.createElement('button');
  cancelEditButton.className = 'button button--outline';
  cancelEditButton.type = 'button';
  cancelEditButton.dataset.toggleEdit = '';
  cancelEditButton.textContent = 'Отмена';
  actions.append(saveButton, cancelEditButton);
  form.append(actions);

  row.append(heading, form, status);
  return row;
}

function insertRow(list, row) {
  list.querySelector('[data-catalog-empty]')?.remove();
  list.append(row);
}

function topicControls() {
  const toolbar = document.querySelector('.topic-toolbar');
  const list = document.querySelector('[data-catalog-list][data-kind="topics"]');
  const filter = document.querySelector('[data-topic-filter]');
  const search = document.querySelector('[data-topic-search]');
  const unassignedButton = document.querySelector('[data-topic-unassigned-filter]');
  const unassignedCount = document.querySelector('[data-topic-unassigned-count]');
  const bulkSelection = document.querySelector('[data-topic-selection-count]');
  const emptyFilter = document.querySelector('[data-topic-filter-empty]');

  if (!toolbar || !list) return null;

  const unassignedId = toolbar.dataset.unassignedId;

  function refresh() {
    const rows = [...list.querySelectorAll('[data-catalog-item]')];
    const subjectId = filter ? filter.value : '';
    const term = search ? search.value.trim().toLowerCase() : '';

    if (unassignedButton) {
      unassignedButton.setAttribute('aria-pressed', String(Boolean(unassignedId && subjectId === unassignedId)));
    }

    let visibleCount = 0;
    let unassignedTotal = 0;

    rows.forEach((row) => {
      const rowSubject = row.dataset.subjectId;
      const rowName = (row.dataset.name || '').toLowerCase();
      if (rowSubject === unassignedId) unassignedTotal += 1;

      const matchesSubject = !subjectId || rowSubject === subjectId;
      const matchesSearch = !term || rowName.includes(term);
      const isVisible = matchesSubject && matchesSearch;

      row.hidden = !isVisible;
      if (isVisible) visibleCount += 1;
    });

    if (unassignedCount) unassignedCount.textContent = String(unassignedTotal);
    if (emptyFilter) emptyFilter.hidden = visibleCount > 0 || rows.length === 0;

    const checkedVisible = list.querySelectorAll('[data-catalog-item]:not([hidden]) input[name="topic_ids"]:checked').length;
    if (bulkSelection) {
      bulkSelection.textContent = `Выбрано ${checkedVisible} из ${visibleCount} видимых тем.`;
    }
  }

  filter?.addEventListener('change', refresh);
  search?.addEventListener('input', refresh);
  unassignedButton?.addEventListener('click', () => {
    if (!filter) return;
    if (filter.value === unassignedId) {
      filter.value = '';
    } else {
      filter.value = unassignedId;
    }
    refresh();
  });
  list.addEventListener('change', (event) => {
    if (event.target.matches('input[name="topic_ids"]')) refresh();
  });

  return { refresh, list };
}

const topicUI = topicControls();
const createForm = document.querySelector('[data-catalog-create]');
const catalogList = document.querySelector('[data-catalog-list]');

if (createForm && catalogList) {
  const status = createForm.querySelector('[data-create-status]');
  const kind = createForm.dataset.kind;

  createForm.addEventListener('submit', async (event) => {
    event.preventDefault();
    setFormBusy(createForm, true, 'Добавляем…');
    showStatus(status, 'working', 'Создание…');

    try {
      const response = await fetch(createForm.action, { method: 'POST', body: new FormData(createForm) });
      if (!response.ok) {
        showStatus(status, 'error', await errorMessage(response));
        return;
      }
      const item = await response.json();
      insertRow(catalogList, makeRow(kind, item));
      createForm.reset();
      showStatus(status, 'success', 'Успешно добавлено.');
      topicUI?.refresh();
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
    const isTopic = row.dataset.kind === 'topics';
    setFormBusy(form, true, 'Сохраняем…');
    showStatus(status, 'working', isTopic ? 'Сохранение…' : 'Переименование…');
    try {
      const response = await fetch(form.action, { method: 'POST', body: new FormData(form) });
      if (!response.ok) {
        showStatus(status, 'error', await errorMessage(response));
        return;
      }
      const item = await response.json();
      row.dataset.name = item.name;
      row.querySelector('[data-item-name]').textContent = item.name;
      row.querySelector('input[name="name"]').value = item.name;
      if (isTopic) {
        row.dataset.subjectId = item.subject_id;
        row.querySelector('[data-topic-subject]').textContent = subjectOptions().find((subject) => subject.id === item.subject_id)?.name || '';
        row.querySelector('input[name="topic_ids"]').setAttribute('aria-label', `Выбрать тему ${item.name}`);
        topicUI?.refresh();
      }
      form.hidden = true;
      const mainToggleBtn = row.querySelector('.catalog-row__heading [data-toggle-edit]');
      if (mainToggleBtn) mainToggleBtn.textContent = 'Редактировать';
      showStatus(status, 'success', isTopic ? 'Тема обновлена.' : `Название изменено на «${item.name}».`);
    } catch {
      showStatus(status, 'error', 'Не удалось связаться с сервером. Попробуйте ещё раз.');
    } finally {
      setFormBusy(form, false, 'Сохранить');
    }
  });

  catalogList.addEventListener('click', async (event) => {
    const toggleBtn = event.target.closest('[data-toggle-edit]');
    if (toggleBtn) {
      const row = toggleBtn.closest('[data-catalog-item]');
      const form = row.querySelector('[data-catalog-rename]');
      if (form) {
        form.hidden = !form.hidden;
        const mainToggleBtn = row.querySelector('.catalog-row__heading [data-toggle-edit]');
        if (mainToggleBtn) {
          mainToggleBtn.textContent = form.hidden ? 'Редактировать' : 'Закрыть';
        }
      }
      return;
    }

    const button = event.target.closest('[data-delete]');
    if (!button) return;
    const row = button.closest('[data-catalog-item]');
    const name = row.dataset.name;
    if (!window.confirm(`Удалить «${name}»? Это действие нельзя отменить.`)) return;

    const status = row.querySelector('.catalog-status');
    const form = row.querySelector('[data-catalog-rename]');
    const controls = [...(form ? form.querySelectorAll('button, input, select') : []), ...row.querySelectorAll('button')];
    controls.forEach((control) => { control.disabled = true; });
    showStatus(status, 'working', 'Удаление…');
    try {
      const response = await fetch(`/admin/${encodeURIComponent(row.dataset.kind)}/${encodeURIComponent(row.dataset.id)}/delete`, { method: 'POST' });
      if (!response.ok) {
        showStatus(status, 'error', await errorMessage(response));
        return;
      }
      row.remove();
      if (!catalogList.querySelector('[data-catalog-item]')) {
        const empty = document.createElement('p');
        empty.className = 'empty-state';
        empty.dataset.catalogEmpty = '';
        empty.textContent = 'Пока нет элементов. Добавьте первый в форме создания.';
        catalogList.append(empty);
      }
      topicUI?.refresh();
    } catch {
      showStatus(status, 'error', 'Не удалось связаться с сервером. Попробуйте ещё раз.');
    } finally {
      controls.forEach((control) => { control.disabled = false; });
    }
  });
}

if (topicUI) {
  const bulkForm = document.querySelector('[data-topic-bulk]');
  const status = bulkForm.querySelector('[data-topic-status]');
  bulkForm.addEventListener('submit', async (event) => {
    event.preventDefault();
    const selected = [...topicUI.list.querySelectorAll('[data-catalog-item]:not([hidden]) input[name="topic_ids"]:checked')];
    if (!selected.length) {
      showStatus(status, 'error', 'Выберите хотя бы одну тему.');
      return;
    }
    setFormBusy(bulkForm, true, 'Перенос…');
    showStatus(status, 'working', 'Перенос тем…');
    try {
      const formData = new FormData(bulkForm);
      formData.delete('topic_ids');
      selected.forEach((checkbox) => formData.append('topic_ids', checkbox.value));
      const response = await fetch(bulkForm.action, { method: 'POST', body: formData });
      if (!response.ok) {
        showStatus(status, 'error', await errorMessage(response));
        return;
      }
      const payload = await response.json();
      const moved = new Map(payload.topics.map((item) => [item.id, item]));
      selected.forEach((checkbox) => {
        const item = moved.get(checkbox.value);
        if (!item) return;
        const row = checkbox.closest('[data-catalog-item]');
        row.dataset.subjectId = item.subject_id;
        row.querySelector('[data-topic-subject]').textContent = subjectOptions().find((subject) => subject.id === item.subject_id)?.name || '';
        checkbox.checked = false;
      });
      topicUI.refresh();
      showStatus(status, 'success', `Перенесено тем: ${moved.size}.`);
    } catch {
      showStatus(status, 'error', 'Не удалось связаться с сервером. Попробуйте ещё раз.');
    } finally {
      setFormBusy(bulkForm, false, 'Перенести выбранные');
    }
  });
  topicUI.refresh();
}
