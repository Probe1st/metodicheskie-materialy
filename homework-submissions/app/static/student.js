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
const studentName = document.querySelector('#student_name');

const submissionOverlay = document.querySelector('#submission_overlay');
const overlayLoading = document.querySelector('#overlay_loading');
const overlaySuccess = document.querySelector('#overlay_success');
const overlayError = document.querySelector('#overlay_error');
const overlayFilename = document.querySelector('#overlay_filename');
const overlayErrorMessage = document.querySelector('#overlay_error_message');
const overlayResetButton = document.querySelector('#overlay_reset_button');
const overlayRetryButton = document.querySelector('#overlay_retry_button');
let isSubmitting = false;
const titleMap = {
  loading: 'overlay_title',
  success: 'overlay_success_title',
  error: 'overlay_error_title',
};

function showOverlay(state) {
  if (!submissionOverlay) return;
  submissionOverlay.hidden = false;
  document.body.style.overflow = 'hidden';

  if (titleMap[state]) {
    submissionOverlay.setAttribute('aria-labelledby', titleMap[state]);
  }
  if (state === 'error') {
    submissionOverlay.setAttribute('aria-describedby', 'overlay_error_message');
  } else {
    submissionOverlay.removeAttribute('aria-describedby');
  }

  if (overlayLoading) overlayLoading.hidden = state !== 'loading';
  if (overlaySuccess) overlaySuccess.hidden = state !== 'success';
  if (overlayError) overlayError.hidden = state !== 'error';

  if (state === 'loading') {
    submissionOverlay.focus();
  } else if (state === 'success') {
    if (overlayResetButton) overlayResetButton.focus();
  } else if (state === 'error') {
    if (overlayRetryButton) overlayRetryButton.focus();
  }
}

function hideOverlay() {
  if (!submissionOverlay) return;
  submissionOverlay.hidden = true;
  document.body.style.overflow = '';
  submissionOverlay.removeAttribute('aria-describedby');
  if (overlayLoading) overlayLoading.hidden = true;
  if (overlaySuccess) overlaySuccess.hidden = true;
  if (overlayError) overlayError.hidden = true;
}
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
  if (isSubmitting) return;

  const selectedFile = file.files[0];
  if (!selectedFile) {
    setStatus(submitStatus, 'error', 'Прикрепите файл с выполненной работой.');
    file.focus();
    return;
  }
  if (!showSelectedFile(selectedFile)) return;
  if (work.disabled || !work.value) {
    setStatus(submitStatus, 'error', 'Выберите тему работы.');
    work.focus();
    return;
  }
  if (studentName && !studentName.value.trim()) {
    setStatus(submitStatus, 'error', 'Укажите ФИО студента.');
    studentName.focus();
    return;
  }

  isSubmitting = true;
  showOverlay('loading');
  setBusy(submitForm, true, 'Отправляем…');
  setStatus(submitStatus, 'working', 'Загружаем файл…');

  try {
    const signal = typeof AbortSignal.timeout === 'function'
      ? AbortSignal.timeout(600000)
      : (() => {
          const controller = new AbortController();
          setTimeout(() => controller.abort(), 600000);
          return controller.signal;
        })();

    const response = await fetch('/submit', {
      method: 'POST',
      body: new FormData(submitForm),
      signal,
    });

    if (!response.ok) {
      if (response.status === 413) {
        throw new Error('Размер файла превышает допустимый лимит сервера.');
      } else if (response.status === 403) {
        throw new Error('Приём работ для данной группы закрыт или дедлайн истёк.');
      } else if (response.status === 400) {
        const errData = await response.json().catch(() => ({}));
        throw new Error(errData.error || 'Неверные параметры отправки или неподдерживаемый файл.');
      } else if (response.status === 500) {
        throw new Error('Внутренняя ошибка сервера при сохранении работы.');
      } else {
        throw new Error('Не удалось отправить работу. Проверьте соединение.');
      }
    }

    const data = await response.json().catch(() => ({}));
    const confirmedName = data.filename || (selectedFile && selectedFile.name) || 'Файл принят';
    if (overlayFilename) overlayFilename.textContent = confirmedName;
    showOverlay('success');
    setStatus(submitStatus, 'success', 'Работа принята: ' + confirmedName);
  } catch (err) {
    isSubmitting = false;
    const msg = err.name === 'TimeoutError'
      ? 'Время ожидания загрузки файла истекло. Проверьте сеть.'
      : (err.message || 'Ошибка отправки: проверьте статус приёма и поля формы.');
    if (overlayErrorMessage) overlayErrorMessage.textContent = msg;
    showOverlay('error');
    setStatus(submitStatus, 'error', msg);
    setBusy(submitForm, false, 'Отправить работу');
    submitButton.disabled = work.disabled;
  }
});

if (overlayResetButton) {
  overlayResetButton.addEventListener('click', () => {
    isSubmitting = false;
    hideOverlay();
    file.value = '';
    resetFilePreview();
    setStatus(submitStatus, '', '');
    updateTopics();
    discipline.focus();
    setBusy(submitForm, false, 'Отправить работу');
    submitButton.disabled = work.disabled;
  });
}

if (overlayRetryButton) {
  overlayRetryButton.addEventListener('click', () => {
    isSubmitting = false;
    hideOverlay();
    setBusy(submitForm, false, 'Отправить работу');
    submitButton.disabled = work.disabled;
    if (submitButton && !submitButton.disabled) {
      submitButton.focus();
    } else {
      file.focus();
    }
  });
}

document.addEventListener('keydown', (event) => {
  if (!submissionOverlay || submissionOverlay.hidden) return;

  if (event.key === 'Escape') {
    if (overlayError && !overlayError.hidden) {
      if (overlayRetryButton) overlayRetryButton.click();
    } else {
      event.preventDefault();
    }
    return;
  }

  if (event.key === 'Tab') {
    const focusable = submissionOverlay.querySelectorAll(
      'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'
    );
    const visibleFocusable = Array.from(focusable).filter(
      (el) => !el.closest('[hidden]')
    );

    if (visibleFocusable.length === 0) {
      event.preventDefault();
      return;
    }

    const first = visibleFocusable[0];
    const last = visibleFocusable[visibleFocusable.length - 1];

    if (event.shiftKey) {
      if (document.activeElement === first || !submissionOverlay.contains(document.activeElement)) {
        event.preventDefault();
        last.focus();
      }
    } else {
      if (document.activeElement === last || !submissionOverlay.contains(document.activeElement)) {
        event.preventDefault();
        first.focus();
      }
    }
  }
});
