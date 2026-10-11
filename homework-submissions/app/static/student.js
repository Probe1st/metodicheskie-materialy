const stepperSteps = Array.from(document.querySelectorAll('.student-step'));
function showActiveStep(activeIndex) {
  stepperSteps.forEach((step, index) => {
    step.classList.toggle('student-step--active', index === activeIndex);
    if (index === activeIndex) step.setAttribute('aria-current', 'step');
    else step.removeAttribute('aria-current');
  });
}

showActiveStep(0);

const unlockForm = document.querySelector('#unlock');
const submitForm = document.querySelector('#submit');
const unlockStatus = document.querySelector('#unlock_status');
const submitStatus = document.querySelector('#submit_status');
const discipline = document.querySelector('#discipline');
const work = document.querySelector('#work');
const topicHint = document.querySelector('#topic_hint');
const submitButton = submitForm.querySelector('button[type="submit"]');
const selectedGroup = document.querySelector('#selected_group');
const groupDisplay = document.querySelector('#group_display');
const accessSummary = document.querySelector('#access_summary');
const changeGroup = document.querySelector('#change_group');
const file = document.querySelector('#file');
const fileName = document.querySelector('#file_name');
const topicPlaceholder = work.options[0].cloneNode(true);
const topicOptions = Array.from(work.options)
  .filter((option) => option.dataset.subjectId)
  .map((option) => option.cloneNode(true));

const dropzone = document.querySelector('#file_dropzone');
const clearFile = document.querySelector('#clear_file');

function resetFilePreview() {
  file.value = '';
  fileName.textContent = 'Файл не выбран';
  clearFile.hidden = true;
  dropzone.classList.remove('is-dragging');
}

function formatFileSize(bytes) {
  if (bytes < 1024 * 1024) {
    return `${Math.max(1, Math.round(bytes / 1024))} КБ`;
  }
  return `${(bytes / (1024 * 1024)).toFixed(1)} МБ`;
}

function showSelectedFile(selectedFile) {
  const maxBytes = window.submissionLimit * 1024 * 1024 * 1024;
  if (selectedFile.size > maxBytes) {
    resetFilePreview();
    setStatus(submitStatus, 'error', `Файл превышает допустимый размер ${window.submissionLimit} ГБ. Выберите файл меньшего размера.`);
    return false;
  }
  fileName.textContent = `${selectedFile.name} · ${formatFileSize(selectedFile.size)}`;
  clearFile.hidden = false;
  setStatus(submitStatus, '', '');
  return true;
}

function acceptDroppedFile(selectedFile) {
  const transfer = new DataTransfer();
  transfer.items.add(selectedFile);
  file.files = transfer.files;
  showSelectedFile(selectedFile);
}
function setStatus(element, state, message) {
  element.dataset.state = state;
  element.textContent = message;
}

function setBusy(form, busy, label) {
  const button = form.querySelector('button[type="submit"]');
  button.disabled = busy;
  button.textContent = label;
}

let groupAssignments = [];

function resetAccess() {
  groupAssignments = [];
  selectedGroup.value = '';
  groupDisplay.textContent = '';
  accessSummary.hidden = true;
  submitForm.hidden = true;
  unlockForm.hidden = false;
  unlockForm.querySelector('[name="password"]').value = '';
  unlockForm.querySelector('[name="password"]').focus();
  showActiveStep(0);
  setBusy(unlockForm, false, 'Подтвердить доступ');
}
function updateTopics() {
  const selectedSubjectId = discipline.selectedOptions[0]?.dataset.subjectId;
  let availableTopics = [];

  const assignedForSubject = groupAssignments.length > 0
    ? groupAssignments.filter((a) => a.subject_id === selectedSubjectId)
    : [];

  if (assignedForSubject.length > 0) {
    availableTopics = assignedForSubject.map((a) => {
      const opt = document.createElement('option');
      opt.value = a.topic_name;
      opt.dataset.subjectId = a.subject_id;
      opt.dataset.assignmentId = a.id;
      opt.textContent = a.label;
      return opt;
    });
  } else {
    availableTopics = topicOptions
      .filter((option) => option.dataset.subjectId === selectedSubjectId)
      .map((option) => option.cloneNode(true));
  }

  work.replaceChildren(topicPlaceholder.cloneNode(true), ...availableTopics);

  const hasTopics = availableTopics.length > 0;
  work.disabled = !hasTopics;
  topicHint.hidden = hasTopics;
  topicHint.textContent = hasTopics
    ? ''
    : 'Для выбранного предмета нет доступных тем.';
  submitButton.disabled = !hasTopics;
}

discipline.addEventListener('change', () => updateTopics());
updateTopics();
submitButton.disabled = work.disabled;


file.addEventListener('change', () => {
  if (file.files[0]) showSelectedFile(file.files[0]);
  else resetFilePreview();
});

clearFile.addEventListener('click', () => {
  resetFilePreview();
  file.focus();
});

['dragenter', 'dragover'].forEach((eventName) => {
  dropzone.addEventListener(eventName, (event) => {
    event.preventDefault();
    event.stopPropagation();
    dropzone.classList.add('is-dragging');
  });
});

['dragleave', 'dragend'].forEach((eventName) => {
  dropzone.addEventListener(eventName, (event) => {
    event.preventDefault();
    if (!dropzone.contains(event.relatedTarget)) dropzone.classList.remove('is-dragging');
  });
});

dropzone.addEventListener('drop', (event) => {
  event.preventDefault();
  event.stopPropagation();
  dropzone.classList.remove('is-dragging');
  const droppedFile = event.dataTransfer?.files[0];
  if (droppedFile) acceptDroppedFile(droppedFile);
});

changeGroup.addEventListener('click', resetAccess);

unlockForm.addEventListener('submit', async (event) => {
  event.preventDefault();
  const formData = new FormData(unlockForm);
  setBusy(unlockForm, true, 'Проверяем доступ…');
  setStatus(unlockStatus, 'working', 'Проверяем доступ…');

  try {
    const response = await fetch('/unlock', {
      method: 'POST',
      body: formData,
      headers: { 'Accept': 'application/json' },
    });
    if (!response.ok) throw new Error('denied');

    if (response.status === 200) {
      const data = await response.json();
      groupAssignments = data.assignments || [];
    }

    selectedGroup.value = formData.get('group');
    groupDisplay.textContent = selectedGroup.value;
    unlockForm.hidden = true;
    accessSummary.hidden = false;
    submitForm.hidden = false;
    showActiveStep(1);
    setStatus(unlockStatus, '', '');
    updateTopics();
    discipline.focus();
  } catch {
    setStatus(unlockStatus, 'error', 'Доступ к приёму работ закрыт или пароль неверен.');
  } finally {
    setBusy(unlockForm, false, 'Подтвердить доступ');
  }
});

submitForm.addEventListener('submit', async (event) => {
  event.preventDefault();
  const selectedFile = file.files[0];
  if (selectedFile && !showSelectedFile(selectedFile)) return;
  if (work.disabled) return;

  setBusy(submitForm, true, 'Отправляем…');
  setStatus(submitStatus, 'working', 'Загружаем файл…');

  try {
    const response = await fetch('/submit', { method: 'POST', body: new FormData(submitForm) });
    if (!response.ok) throw new Error('failed');

    const { filename } = await response.json();
    setStatus(submitStatus, 'success', `Работа принята: ${filename}`);
  } catch {
    setStatus(submitStatus, 'error', 'Ошибка отправки: проверьте статус приёма и поля формы.');
  } finally {
    setBusy(submitForm, false, 'Отправить работу');
    submitButton.disabled = work.disabled;
  }
});
