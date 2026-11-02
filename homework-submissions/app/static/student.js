const stepperSteps = Array.from(document.querySelectorAll('.student-step'));
function showActiveStep(activeIndex) {
  stepperSteps.forEach((step, index) => {
    step.classList.toggle('student-step--active', index === activeIndex);
    if (index === activeIndex) step.setAttribute('aria-current', 'step');
    else step.removeAttribute('aria-current');
  });
}

if (stepperSteps.length > 0) {
  showActiveStep(0);
}

const unlockForm = document.querySelector('#unlock');
const submitForm = document.querySelector('#submit');
const unlockStatus = document.querySelector('#unlock_status');
const submitStatus = document.querySelector('#submit_status');
const discipline = document.querySelector('#discipline');
const work = document.querySelector('#work');
const topicHint = document.querySelector('#topic_hint');
const submitButton = submitForm?.querySelector('button[type="submit"]');
const selectedGroup = document.querySelector('#selected_group');
const groupDisplay = document.querySelector('#group_display');
const accessSummary = document.querySelector('#access_summary');
const changeGroup = document.querySelector('#change_group');
const file = document.querySelector('#file');
const fileName = document.querySelector('#file_name');

const topicPlaceholder = work?.options?.[0] ? work.options[0].cloneNode(true) : null;
const topicOptions = work?.options
  ? Array.from(work.options).filter((option) => option.dataset.subjectId).map((option) => option.cloneNode(true))
  : [];

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

  [overlayLoading, overlaySuccess, overlayError].forEach((view) => {
    if (view) view.hidden = true;
  });

  const stateView = state === 'loading'
    ? overlayLoading
    : state === 'success'
      ? overlaySuccess
      : overlayError;

  if (stateView) stateView.hidden = false;

  const focusTarget = state === 'success'
    ? overlayResetButton
    : state === 'error'
      ? overlayRetryButton
      : null;

  if (focusTarget) {
    setTimeout(() => focusTarget.focus(), 50);
  }
}

function hideOverlay() {
  if (!submissionOverlay) return;
  submissionOverlay.hidden = true;
  document.body.style.overflow = '';
}

function resetFilePreview() {
  if (!file) return;
  file.value = '';
  if (fileName) {
    fileName.textContent = 'Файл не выбран';
    fileName.classList.remove('is-selected', 'is-invalid');
  }
  if (dropzone) dropzone.classList.remove('has-file');
  if (clearFile) clearFile.hidden = true;
}

function showSelectedFile(selectedFile) {
  if (!selectedFile) {
    resetFilePreview();
    return false;
  }
  const maxBytes = window.submissionMaxBytes || 209715200;
  const isTooLarge = selectedFile.size > maxBytes;

  if (fileName) {
    fileName.textContent = `${selectedFile.name} (${(selectedFile.size / 1024 / 1024).toFixed(2)} МБ)`;
    fileName.classList.add('is-selected');
    fileName.classList.toggle('is-invalid', isTooLarge);
  }
  if (dropzone) dropzone.classList.add('has-file');
  if (clearFile) clearFile.hidden = false;

  if (isTooLarge && submitStatus) {
    setStatus(submitStatus, 'error', `Файл слишком большой. Максимальный размер: ${window.submissionLimit || '200 МБ'}.`);
    return false;
  }
  return true;
}

function acceptDroppedFile(selectedFile) {
  if (!file) return;
  const transfer = new DataTransfer();
  transfer.items.add(selectedFile);
  file.files = transfer.files;
  showSelectedFile(selectedFile);
}

function setStatus(element, state, message) {
  if (!element) return;
  element.dataset.state = state;
  element.textContent = message;
}

function setBusy(form, busy, label) {
  if (!form) return;
  const button = form.querySelector('button[type="submit"]');
  if (button) {
    button.disabled = busy;
    button.textContent = label;
  }
}

let groupAssignments = [];

function resetAccess() {
  groupAssignments = [];
  if (selectedGroup) selectedGroup.value = '';
  if (groupDisplay) groupDisplay.textContent = '';
  if (accessSummary) accessSummary.hidden = true;
  if (submitForm) submitForm.hidden = true;
  const unlockView = document.querySelector('#student_unlock_view');
  const workspaceView = document.querySelector('#student_workspace_view');
  if (workspaceView) workspaceView.hidden = true;
  if (unlockView) unlockView.hidden = false;
  if (unlockForm) {
    unlockForm.hidden = false;
    const pwd = unlockForm.querySelector('[name="password"]');
    if (pwd) {
      pwd.value = '';
      pwd.focus();
    }
    setBusy(unlockForm, false, 'Подтвердить доступ');
  }
  showActiveStep(0);
}

function updateTopics() {
  if (!discipline || !work || !topicPlaceholder) return;
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
  if (topicHint) {
    topicHint.hidden = hasTopics;
    topicHint.textContent = hasTopics
      ? ''
      : 'Для выбранного предмета нет доступных тем.';
  }
  if (submitButton) submitButton.disabled = !hasTopics;
}

if (discipline) {
  discipline.addEventListener('change', () => updateTopics());
  updateTopics();
}
if (submitButton && work) {
  submitButton.disabled = work.disabled;
}

if (file) {
  file.addEventListener('change', () => {
    if (file.files[0]) showSelectedFile(file.files[0]);
    else resetFilePreview();
  });
}

if (clearFile) {
  clearFile.addEventListener('click', () => {
    resetFilePreview();
    if (file) file.focus();
  });
}

if (dropzone) {
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
}

if (changeGroup) {
  changeGroup.addEventListener('click', resetAccess);
}

if (unlockForm) {
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

      if (selectedGroup) selectedGroup.value = formData.get('group');
      if (groupDisplay) groupDisplay.textContent = formData.get('group');
      setStatus(unlockStatus, '', '');

      const unlockView = document.querySelector('#student_unlock_view');
      const workspaceView = document.querySelector('#student_workspace_view');

      if (workspaceView && typeof window.updateStudentLessons === 'function') {
        if (unlockView) unlockView.hidden = true;
        window.updateStudentLessons(groupAssignments);
      } else if (submitForm) {
        if (unlockForm) unlockForm.hidden = true;
        if (accessSummary) accessSummary.hidden = false;
        submitForm.hidden = false;
        updateTopics();
        if (discipline) discipline.focus();
      }
    } catch {
      setStatus(unlockStatus, 'error', 'Доступ к приёму работ закрыт или пароль неверен.');
    } finally {
      setBusy(unlockForm, false, 'Подтвердить доступ');
    }
  });
}

if (submitForm) {
  submitForm.addEventListener('submit', async (event) => {
    event.preventDefault();
    if (isSubmitting) return;

    const selectedFile = file?.files?.[0];
    if (!selectedFile) {
      setStatus(submitStatus, 'error', 'Прикрепите файл с выполненной работой.');
      if (file) file.focus();
      return;
    }
    if (!showSelectedFile(selectedFile)) return;

    if (work) {
      if (work.tagName === 'SELECT' && (work.disabled || !work.value)) {
        setStatus(submitStatus, 'error', 'Выберите тему работы.');
        work.focus();
        return;
      } else if (work.tagName === 'INPUT' && !work.value.trim()) {
        setStatus(submitStatus, 'error', 'Выберите тему работы.');
        return;
      }
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
      if (submitButton && work) submitButton.disabled = work.disabled;
    }
  });
}

if (overlayResetButton) {
  overlayResetButton.addEventListener('click', () => {
    isSubmitting = false;
    hideOverlay();
    if (file) file.value = '';
    resetFilePreview();
    setStatus(submitStatus, '', '');
    updateTopics();
    if (discipline) discipline.focus();
    else if (file) file.focus();
    setBusy(submitForm, false, 'Отправить работу');
    if (submitButton && work) submitButton.disabled = work.disabled;
  });
}

if (overlayRetryButton) {
  overlayRetryButton.addEventListener('click', () => {
    isSubmitting = false;
    hideOverlay();
    setBusy(submitForm, false, 'Отправить работу');
    if (submitButton && work) submitButton.disabled = work.disabled;
    if (submitButton && !submitButton.disabled) {
      submitButton.focus();
    } else if (file) {
      file.focus();
    }
  });
}
