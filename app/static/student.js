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
function getCsrfToken() {
  const meta = document.querySelector('meta[name="csrf-token"]');
  return meta ? meta.getAttribute('content') : '';
}


const unlockForm = document.querySelector('#unlock');
const submitForm = document.querySelector('#submit');
const unlockStatus = document.querySelector('#unlock_status');
const submitStatus = document.querySelector('#submit_status');

const discipline = document.querySelector('#discipline');
const assignmentSelect = document.querySelector('#assignment_id');
const topicHint = document.querySelector('#topic_hint');
const submitButton = submitForm?.querySelector('button[type="submit"]');

const selectedGroup = document.querySelector('#selected_group');
const groupDisplay = document.querySelector('#group_display');
const accessSummary = document.querySelector('#access_summary');
const changeGroup = document.querySelector('#change_group');

const studentSelect = document.querySelector('#student_id');
const toggleNewStudentBtn = document.querySelector('#toggle_new_student_btn');
const newStudentPanel = document.querySelector('#new_student_panel');
const newStudentInput = document.querySelector('#new_student_input');
const saveStudentBtn = document.querySelector('#save_student_btn');
const cancelStudentBtn = document.querySelector('#cancel_student_btn');
const newStudentStatus = document.querySelector('#new_student_status');

const file = document.querySelector('#file');
const fileName = document.querySelector('#file_name');
const dropzone = document.querySelector('#file_dropzone');
const clearFile = document.querySelector('#clear_file');

const submissionOverlay = document.querySelector('#submission_overlay');
const overlayLoading = document.querySelector('#overlay_loading');
const overlaySuccess = document.querySelector('#overlay_success');
const overlayError = document.querySelector('#overlay_error');
const overlayFilename = document.querySelector('#overlay_filename');
const overlayErrorMessage = document.querySelector('#overlay_error_message');
const overlayResetButton = document.querySelector('#overlay_reset_button');
const overlayRetryButton = document.querySelector('#overlay_retry_button');

let isSubmitting = false;
let groupAssignments = [];
let groupStudents = studentSelect && studentSelect.tagName === 'SELECT'
  ? Array.from(studentSelect.options)
      .filter((opt) => opt.value)
      .map((opt) => ({ id: opt.value, display_name: opt.textContent || '' }))
  : [];

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
  if (!file) return;
  file.value = '';
  if (fileName) fileName.textContent = 'Файл не выбран';
  if (clearFile) clearFile.hidden = true;
  if (dropzone) dropzone.classList.remove('is-dragging');
}

function formatFileSize(bytes) {
  if (bytes < 1024 * 1024) {
    return `${Math.max(1, Math.round(bytes / 1024))} КБ`;
  }
  return `${(bytes / (1024 * 1024)).toFixed(1)} МБ`;
}

function showSelectedFile(selectedFile) {
  if (!selectedFile) {
    resetFilePreview();
    return false;
  }
  const maxBytes = window.submissionMaxBytes || (typeof window.submissionLimit === 'number' ? window.submissionLimit * 1024 * 1024 * 1024 : 0);
  if (maxBytes && selectedFile.size > maxBytes) {
    resetFilePreview();
    let limitLabel = window.submissionLimit || 'допустимый предел';
    if (typeof limitLabel === 'number') {
      limitLabel = `${limitLabel} ГБ`;
    }
    setStatus(submitStatus, 'error', `Файл превышает допустимый размер ${limitLabel}. Выберите файл меньшего размера.`);
    return false;
  }
  if (fileName) fileName.textContent = `${selectedFile.name} · ${formatFileSize(selectedFile.size)}`;
  if (clearFile) clearFile.hidden = false;
  setStatus(submitStatus, '', '');
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

function resetAccess() {
  groupAssignments = [];
  groupStudents = [];
  if (selectedGroup) selectedGroup.value = '';
  if (groupDisplay) groupDisplay.textContent = '';

  const csrfToken = getCsrfToken();
  fetch('/student/logout', {
    method: 'POST',
    headers: csrfToken ? { 'X-CSRFToken': csrfToken } : {},
  }).catch(() => {});

  const unlockView = document.querySelector('#student_unlock_view');
  const workspaceView = document.querySelector('#student_workspace_view');
  if (workspaceView) workspaceView.hidden = true;
  if (unlockView) unlockView.hidden = false;
  if (!workspaceView) {
    if (accessSummary) accessSummary.hidden = true;
    if (submitForm) submitForm.hidden = true;
  }

  if (unlockForm) {
    unlockForm.hidden = false;
    const pwInput = unlockForm.querySelector('[name="password"]');
    if (pwInput) {
      pwInput.value = '';
      pwInput.focus();
    }
    setBusy(unlockForm, false, 'Подтвердить доступ');
  }
  if (stepperSteps.length > 0) {
    showActiveStep(0);
  }
}

function updateAssignments() {
  if (!discipline || !assignmentSelect || assignmentSelect.tagName !== 'SELECT') return;
  const selectedSubjectId = discipline.value;
  let available = groupAssignments;
  if (selectedSubjectId) {
    available = groupAssignments.filter((a) => a.subject_id === selectedSubjectId);
  }

  assignmentSelect.replaceChildren();
  const defaultOption = document.createElement('option');
  defaultOption.value = '';
  defaultOption.textContent = available.length > 0 ? 'Выберите занятие' : 'Нет доступных заданий';
  assignmentSelect.appendChild(defaultOption);

  available.forEach((a) => {
    const opt = document.createElement('option');
    opt.value = a.id;
    opt.dataset.subjectId = a.subject_id;
    opt.dataset.topicId = a.topic_id;
    opt.textContent = a.label || `${a.lesson_date}, пара ${a.pair_number} — ${a.topic_name}`;
    assignmentSelect.appendChild(opt);
  });

  const hasItems = available.length > 0;
  assignmentSelect.disabled = !hasItems;
  if (topicHint) {
    topicHint.hidden = hasItems;
    topicHint.textContent = hasItems ? '' : 'Для выбранного предмета нет доступных пар/тем.';
  }
  if (submitButton) submitButton.disabled = !hasItems;
}

function updateStudentsList() {
  if (!studentSelect || studentSelect.tagName !== 'SELECT') return;
  studentSelect.replaceChildren();
  const defaultOption = document.createElement('option');
  defaultOption.value = '';
  defaultOption.textContent = groupStudents.length > 0 ? 'Выберите себя из списка' : 'Список пуст — добавьте себя';
  studentSelect.appendChild(defaultOption);

  groupStudents.forEach((s) => {
    const opt = document.createElement('option');
    opt.value = s.id;
    opt.textContent = s.display_name;
    studentSelect.appendChild(opt);
  });
}

if (discipline) {
  discipline.addEventListener('change', updateAssignments);
}

if (toggleNewStudentBtn) {
  toggleNewStudentBtn.addEventListener('click', () => {
    if (newStudentPanel) {
      newStudentPanel.hidden = !newStudentPanel.hidden;
      if (!newStudentPanel.hidden && newStudentInput) {
        newStudentInput.focus();
      }
    }
  });
}

if (cancelStudentBtn) {
  cancelStudentBtn.addEventListener('click', () => {
    if (newStudentPanel) {
      newStudentPanel.hidden = true;
      if (newStudentInput) newStudentInput.value = '';
      setStatus(newStudentStatus, '', '');
    }
  });
}

if (saveStudentBtn) {
  saveStudentBtn.addEventListener('click', async () => {
    const name = newStudentInput ? newStudentInput.value.trim() : '';
    if (!name) {
      setStatus(newStudentStatus, 'error', 'Введите ФИО студента.');
      if (newStudentInput) newStudentInput.focus();
      return;
    }

    setStatus(newStudentStatus, 'working', 'Добавляем…');
    try {
      const res = await fetch('/student/students', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Accept': 'application/json',
          ...(getCsrfToken() ? { 'X-CSRFToken': getCsrfToken() } : {}),
        },
        body: JSON.stringify({ display_name: name }),
      });

      if (!res.ok) {
        if (res.status === 403) {
          if (unlockForm) resetAccess();
          throw new Error('Доступ группы истёк или был отозван. Подтвердите пароль снова.');
        }
        const err = await res.json().catch(() => ({}));
        throw new Error(err.error || 'Ошибка при добавлении студента.');
      }

      const created = await res.json();
      groupStudents.push(created);
      groupStudents.sort((a, b) => a.display_name.localeCompare(b.display_name));
      updateStudentsList();
      if (studentSelect) studentSelect.value = created.id;

      if (newStudentPanel) newStudentPanel.hidden = true;
      if (newStudentInput) newStudentInput.value = '';
      setStatus(newStudentStatus, '', '');
    } catch (err) {
      setStatus(newStudentStatus, 'error', err.message);
    }
  });
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

    const headers = { 'Accept': 'application/json' };
    const csrfToken = getCsrfToken();
    if (csrfToken) headers['X-CSRFToken'] = csrfToken;

    try {
      const response = await fetch('/unlock', {
        method: 'POST',
        body: formData,
        headers,
      });
      if (!response.ok) {
        const errData = await response.json().catch(() => ({}));
        if (errData.error === 'csrf_error') {
          throw new Error(errData.message || 'Сессия устарела. Обновите страницу и повторите действие.');
        }
        throw new Error('denied');
      }

      const data = await response.json();
      groupAssignments = data.assignments || [];
      groupStudents = data.students || [];

      if (selectedGroup) selectedGroup.value = data.group_id || '';
      if (groupDisplay) groupDisplay.textContent = data.group_name || data.group || '';
      setStatus(unlockStatus, '', '');

      const unlockView = document.querySelector('#student_unlock_view');
      const workspaceView = document.querySelector('#student_workspace_view');

      if (workspaceView && typeof window.updateStudentLessons === 'function') {
        if (unlockView) unlockView.hidden = true;
        if (accessSummary) accessSummary.hidden = false;
        window.updateStudentLessons(groupAssignments);
      } else {
        unlockForm.hidden = true;
        if (accessSummary) accessSummary.hidden = false;
        if (submitForm) submitForm.hidden = false;
        if (stepperSteps.length > 0) showActiveStep(1);
        updateAssignments();
        updateStudentsList();
        if (assignmentSelect && assignmentSelect.focus) assignmentSelect.focus();
      }
    } catch (err) {
      const msg = (err && err.message && err.message !== 'denied')
        ? err.message
        : 'Доступ к приёму работ закрыт или пароль неверен.';
      setStatus(unlockStatus, 'error', msg);
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

    if (assignmentSelect && assignmentSelect.tagName === 'SELECT' && !assignmentSelect.value) {
      setStatus(submitStatus, 'error', 'Выберите тему занятия.');
      assignmentSelect.focus();
      return;
    }
    if (studentSelect && !studentSelect.value) {
      setStatus(submitStatus, 'error', 'Выберите или добавьте студента.');
      studentSelect.focus();
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

      const headers = { 'Accept': 'application/json' };
      const csrfToken = getCsrfToken();
      if (csrfToken) headers['X-CSRFToken'] = csrfToken;

      const response = await fetch('/submit', {
        method: 'POST',
        body: new FormData(submitForm),
        headers,
        signal,
      });

      if (!response.ok) {
        if (response.status === 413) {
          throw new Error('Размер файла превышает допустимый лимит сервера.');
        } else if (response.status === 403) {
          if (unlockForm) resetAccess();
          throw new Error('Приём работ для данной группы закрыт или дедлайн истёк.');
        } else if (response.status === 400) {
          const errData = await response.json().catch(() => ({}));
          if (errData.error === 'csrf_error') {
            throw new Error(errData.message || 'Сессия устарела. Обновите страницу и повторите действие.');
          } else if (errData.error === 'file_type_not_allowed') {
            throw new Error(errData.message || 'Недопустимый формат файла.');
          }
          throw new Error(errData.message || errData.error || 'Неверные параметры отправки или неподдерживаемый файл.');
        } else if (response.status === 500) {
          throw new Error('Внутренняя ошибка сервера при сохранении работы.');
        } else {
          throw new Error('Не удалось отправить работу. Проверьте соединение.');
        }
      }

      const data = await response.json().catch(() => ({}));
      const confirmedName = data.filename || selectedFile.name;
      const attemptText = data.attempt_number ? ` (попытка №${data.attempt_number})` : '';
      if (overlayFilename) overlayFilename.textContent = confirmedName + attemptText;
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
    if (discipline && assignmentSelect && assignmentSelect.tagName === 'SELECT') {
      updateAssignments();
    }
    setBusy(submitForm, false, 'Отправить работу');
  });
}

if (overlayRetryButton) {
  overlayRetryButton.addEventListener('click', () => {
    isSubmitting = false;
    hideOverlay();
    setBusy(submitForm, false, 'Отправить работу');
    if (submitButton && !submitButton.disabled) {
      submitButton.focus();
    } else if (file) {
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
