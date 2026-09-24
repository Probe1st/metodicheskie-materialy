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
  const select = document.querySelector('[data-catalog-create] select[name="subject_id"]');
  if (!select) return [];
  return [...select.options].map((option) => ({ id: option.value, name: option.textContent }));
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
  const title = document.createElement('h3');
  title.dataset.itemName = '';
  title.textContent = item.name;
  if (kind === 'topics') {
    const titleWrap = document.createElement('div');
    const subjectName = subjectOptions().find((subject) => subject.id === item.subject_id)?.name || '';
    const subject = document.createElement('p');
    subject.className = 'field__hint';
    subject.dataset.topicSubject = '';
    subject.textContent = subjectName;
    titleWrap.append(title, subject);
    heading.append(titleWrap);
  } else {
    heading.append(title);
  }

  const form = document.createElement('form');
  form.className = 'catalog-rename form-stack';
  form.dataset.catalogRename = '';
  form.method = 'post';
  form.action = `/admin/${encodeURIComponent(kind)}/${encodeURIComponent(item.id)}/${kind === 'topics' ? 'update' : 'rename'}`;
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
  const renameButton = document.createElement('button');
  renameButton.className = 'button button--outline';
  renameButton.type = 'submit';
  renameButton.textContent = kind === 'topics' ? 'Сохранить' : 'Переименовать';
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

function topicControls() {
  const toolbar = document.querySelector('.topic-toolbar');
  const list = document.querySelector('[data-catalog-list][data-kind="topics"]');
  const filter = document.querySelector('[data-topic-filter]');
  const search = document.querySelector('[data-topic-search]');
  const empty = document.querySelector('[data-topic-filter-empty]');
  const unassignedFilter = document.querySelector('[data-topic-unassigned-filter]');
  const unassignedCount = document.querySelector('[data-topic-unassigned-count]');
  if (!toolbar || !list || !filter || !search || !empty || !unassignedFilter || !unassignedCount) return null;

  const refresh = ({ clearHiddenSelection = false } = {}) => {
    const rows = [...list.querySelectorAll('[data-catalog-item]')];
    const query = search.value.trim().toLocaleLowerCase();
    let visible = 0;
    const counts = new Map();
    rows.forEach((row) => {
      counts.set(row.dataset.subjectId, (counts.get(row.dataset.subjectId) || 0) + 1);
      const matches = (!filter.value || row.dataset.subjectId === filter.value)
        && row.dataset.name.toLocaleLowerCase().includes(query);
      if (clearHiddenSelection && !matches) {
        row.querySelector('input[name="topic_ids"]').checked = false;
      }
      row.hidden = !matches;
      if (matches) visible += 1;
    });
    [...filter.options].forEach((option) => {
      if (!option.value) option.textContent = `Все предметы (${rows.length})`;
      else {
        const label = option.dataset.label || option.textContent.replace(/\s+\(\d+\)$/, '');
        option.dataset.label = label;
        option.textContent = `${label} (${counts.get(option.value) || 0})`;
      }
    });
    const unassignedId = toolbar.dataset.unassignedId;
    unassignedCount.textContent = String(counts.get(unassignedId) || 0);
    unassignedFilter.setAttribute('aria-pressed', String(filter.value === unassignedId));
    empty.hidden = rows.length === 0 || visible !== 0;
  };
  filter.addEventListener('change', () => refresh({ clearHiddenSelection: true }));
  search.addEventListener('input', () => refresh({ clearHiddenSelection: true }));
  unassignedFilter.addEventListener('click', () => {
    filter.value = toolbar.dataset.unassignedId;
    refresh({ clearHiddenSelection: true });
  });
  return { toolbar, list, refresh };
}

const topicUI = topicControls();
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
        showStatus(status, 'error', await errorMessage(response));
        return;
      }
      const item = await response.json();
      insertRow(catalogList, makeRow(kind, item));
      createForm.reset();
      topicUI?.refresh();
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
      showStatus(status, 'success', isTopic ? 'Тема обновлена.' : `Название изменено на «${item.name}».`);
    } catch {
      showStatus(status, 'error', 'Не удалось связаться с сервером. Попробуйте ещё раз.');
    } finally {
      setFormBusy(form, false, isTopic ? 'Сохранить' : 'Переименовать');
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
    const controls = [...form.querySelectorAll('button, input, select')];
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
        empty.textContent = 'Пока нет элементов. Добавьте первый в форме выше.';
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
