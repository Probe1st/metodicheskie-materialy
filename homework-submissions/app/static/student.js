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

function setStatus(element, state, message) {
  element.dataset.state = state;
  element.textContent = message;
}

function setBusy(form, busy, label) {
  const button = form.querySelector('button[type="submit"]');
  button.disabled = busy;
  button.textContent = label;
}


function resetAccess() {
  selectedGroup.value = '';
  groupDisplay.textContent = '';
  accessSummary.hidden = true;
  submitForm.hidden = true;
  unlockForm.hidden = false;
  unlockForm.querySelector('[name="password"]').value = '';
  unlockForm.querySelector('[name="password"]').focus();
}
function updateTopics() {
  const selectedSubjectId = discipline.selectedOptions[0]?.dataset.subjectId;
  const availableTopics = topicOptions
    .filter((option) => option.dataset.subjectId === selectedSubjectId)
    .map((option) => option.cloneNode(true));
  work.replaceChildren(topicPlaceholder.cloneNode(true), ...availableTopics);

  const hasTopics = availableTopics.length > 0;
  work.disabled = !hasTopics;
  topicHint.hidden = hasTopics;
  topicHint.textContent = hasTopics ? '' : 'Для выбранного предмета нет доступных тем.';
  submitButton.disabled = !hasTopics;
}

discipline.addEventListener('change', () => updateTopics());
updateTopics();


file.addEventListener('change', () => {
  fileName.textContent = file.files[0] ? file.files[0].name : `Файл до ${window.submissionLimit} ГБ`;
});
changeGroup.addEventListener('click', resetAccess);

unlockForm.addEventListener('submit', async (event) => {
  event.preventDefault();
  const formData = new FormData(unlockForm);
  setBusy(unlockForm, true, 'Проверяем доступ…');
  setStatus(unlockStatus, 'working', 'Проверяем доступ…');

  try {
    const response = await fetch('/unlock', { method: 'POST', body: formData });
    if (!response.ok) throw new Error('denied');

    selectedGroup.value = formData.get('group');
    groupDisplay.textContent = selectedGroup.value;
    unlockForm.hidden = true;
    accessSummary.hidden = false;
    submitForm.hidden = false;
    setStatus(unlockStatus, '', '');
    discipline.focus();
  } catch {
    setStatus(unlockStatus, 'error', 'Доступ к приёму работ закрыт или пароль неверен.');
  } finally {
    setBusy(unlockForm, false, 'Открыть форму');
  }
});

submitForm.addEventListener('submit', async (event) => {
  event.preventDefault();
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
