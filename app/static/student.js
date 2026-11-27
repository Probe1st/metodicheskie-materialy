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
const submitButton = submitForm ? submitForm.querySelector('button[type="submit"]') : null;

const selectedGroup = document.querySelector('#selected_group');
const groupDisplay = document.querySelector('#group_display');
const accessSummary = document.querySelector('#access_summary');
const changeGroup = document.querySelector('#change_group');

const studentSelect = document.querySelector('#student_id');
const studentSelectionWarning = document.querySelector('#student_selection_warning');
const studentSelectionNotice = document.querySelector('#student_selection_notice');
const toggleNewStudentBtn = document.querySelector('#toggle_new_student_btn');
const newStudentPanel = document.querySelector('#new_student_panel');
const newStudentInput = document.querySelector('#new_student_input');
const newStudentPreview = document.querySelector('#new_student_preview');
const newStudentWarning = document.querySelector('#new_student_warning');
const newStudentMatches = document.querySelector('#new_student_matches');
const saveStudentBtn = document.querySelector('#save_student_btn');
const cancelStudentBtn = document.querySelector('#cancel_student_btn');
const newStudentStatus = document.querySelector('#new_student_status');

const studentConfirmDialog = document.querySelector('#student_confirm_dialog');
const studentDialogTitle = document.querySelector('#student_dialog_title');
const studentDialogMessage = document.querySelector('#student_dialog_message');
const studentDialogPreview = document.querySelector('#student_dialog_preview');
const studentDialogWarnings = document.querySelector('#student_dialog_warnings');
const studentDialogMatches = document.querySelector('#student_dialog_matches');
const studentDialogPickListBtn = document.querySelector('#student_dialog_pick_list_btn');
const studentDialogCancelBtn = document.querySelector('#student_dialog_cancel_btn');
const studentDialogConfirmBtn = document.querySelector('#student_dialog_confirm_btn');
const studentDialogCloseBtn = document.querySelector('#student_dialog_close');

const studentNameInput = document.querySelector('#student_name');
const groupStudentsList = document.querySelector('#group_students_list');
const studentNamePreview = document.querySelector('#student_name_preview');
const studentNameWarning = document.querySelector('#student_name_warning');

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
let groupStudents = [];
let lastWarnedStudentId = null;

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
  if (!file || !fileName || !clearFile || !dropzone) return;
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
  if (!fileName || !clearFile) return true;
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
  fileName.textContent = `${selectedFile.name} · ${formatFileSize(selectedFile.size)}`;
  clearFile.hidden = false;
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
  if (element.hasAttribute('hidden') || element.id.endsWith('_warning') || element.id.endsWith('_notice')) {
    element.hidden = !message;
  }
}

function setWarningBox(element, messages) {
  if (!element) return;
  const list = Array.isArray(messages) ? messages.filter(Boolean) : (messages ? [messages] : []);
  if (list.length === 0) {
    element.textContent = '';
    element.hidden = true;
    return;
  }
  element.dataset.state = 'warning';
  element.textContent = list.join(' ');
  element.hidden = false;
}

function clearNewStudentFeedback() {
  if (newStudentPreview) {
    newStudentPreview.textContent = '';
    newStudentPreview.hidden = true;
  }
  setWarningBox(newStudentWarning, []);
  if (newStudentMatches) {
    newStudentMatches.replaceChildren();
    newStudentMatches.hidden = true;
  }
  setStatus(newStudentStatus, '', '');
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
  if (!unlockForm) return;
  groupAssignments = [];
  groupStudents = [];
  if (selectedGroup) selectedGroup.value = '';
  if (groupDisplay) groupDisplay.textContent = '';
  if (accessSummary) accessSummary.hidden = true;
  if (submitForm) submitForm.hidden = true;
  unlockForm.hidden = false;
  const pwInput = unlockForm.querySelector('[name="password"]');
  if (pwInput) {
    pwInput.value = '';
    pwInput.focus();
  }
  showActiveStep(0);
  setBusy(unlockForm, false, 'Подтвердить доступ');
}

function updateAssignments() {
  if (!assignmentSelect || assignmentSelect.tagName !== 'SELECT') return;
  const selectedSubjectId = discipline ? discipline.value : '';
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
  if (studentSelect) {
    const currentValue = studentSelect.value;
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
    if (currentValue && groupStudents.some((s) => s.id === currentValue)) {
      studentSelect.value = currentValue;
    }
  }

  if (groupStudentsList) {
    groupStudentsList.replaceChildren();
    groupStudents.forEach((s) => {
      const opt = document.createElement('option');
      opt.value = s.display_name;
      groupStudentsList.appendChild(opt);
    });
  }
}

function selectExistingStudent(studentObj, closePanel = true, noticeMessage = '') {
  if (!studentObj || !studentSelect) return;
  if (!groupStudents.some((s) => s.id === studentObj.id)) {
    groupStudents.push(studentObj);
    groupStudents.sort((a, b) => a.display_name.localeCompare(b.display_name, 'ru'));
  }
  updateStudentsList();
  studentSelect.value = studentObj.id;
  lastWarnedStudentId = noticeMessage ? studentObj.id : null;
  setWarningBox(studentSelectionWarning, []);
  setStatus(studentSelectionNotice, noticeMessage ? 'info' : '', noticeMessage || '');
  if (closePanel && newStudentPanel) {
    newStudentPanel.hidden = true;
    if (newStudentInput) newStudentInput.value = '';
    clearNewStudentFeedback();
  }
}

let studentDialogResolver = null;
let studentDialogOutcome = { action: 'cancel' };
let studentDialogListenersBound = false;
let studentDialogMouseDownBackdrop = false;

function initStudentDialogListeners() {
  if (studentDialogListenersBound || !studentConfirmDialog) return;
  studentDialogListenersBound = true;

  const finishWith = (outcome) => {
    studentDialogOutcome = outcome;
    studentConfirmDialog.close();
  };

  studentConfirmDialog.addEventListener('cancel', (event) => {
    event.preventDefault();
    finishWith({ action: 'cancel' });
  });

  studentConfirmDialog.addEventListener('mousedown', (event) => {
    if (event.target === studentConfirmDialog) {
      const rect = studentConfirmDialog.getBoundingClientRect();
      const inside = (
        event.clientX >= rect.left
        && event.clientX <= rect.right
        && event.clientY >= rect.top
        && event.clientY <= rect.bottom
      );
      studentDialogMouseDownBackdrop = !inside;
    } else {
      studentDialogMouseDownBackdrop = false;
    }
  });

  studentConfirmDialog.addEventListener('click', (event) => {
    if (event.target === studentConfirmDialog && studentDialogMouseDownBackdrop) {
      const rect = studentConfirmDialog.getBoundingClientRect();
      const inside = (
        event.clientX >= rect.left
        && event.clientX <= rect.right
        && event.clientY >= rect.top
        && event.clientY <= rect.bottom
      );
      if (!inside) finishWith({ action: 'cancel' });
    }
    studentDialogMouseDownBackdrop = false;
  });

  if (studentDialogCloseBtn) {
    studentDialogCloseBtn.addEventListener('click', () => finishWith({ action: 'cancel' }));
  }
  if (studentDialogCancelBtn) {
    studentDialogCancelBtn.addEventListener('click', () => finishWith({ action: 'cancel' }));
  }
  if (studentDialogPickListBtn) {
    studentDialogPickListBtn.addEventListener('click', () => finishWith({ action: 'pick_list' }));
  }
  if (studentDialogConfirmBtn) {
    studentDialogConfirmBtn.addEventListener('click', () => finishWith({ action: 'confirm_new' }));
  }

  studentConfirmDialog.addEventListener('close', () => {
    const resolve = studentDialogResolver;
    const outcome = studentDialogOutcome;
    studentDialogResolver = null;
    studentDialogOutcome = { action: 'cancel' };
    if (resolve) resolve(outcome);
  });
}

function openStudentConfirmDialog(options) {
  if (!studentConfirmDialog || typeof studentConfirmDialog.showModal !== 'function') {
    const parts = [options.message];
    if (Array.isArray(options.warnings) && options.warnings.length > 0) {
      parts.push(options.warnings.join(' '));
    }
    if (Array.isArray(options.matches) && options.matches.length > 0) {
      parts.push(`Похожие: ${options.matches.map((m) => m.display_name).join(', ')}`);
    }
    const ok = window.confirm(parts.filter(Boolean).join('\n\n'));
    return Promise.resolve({ action: ok ? 'confirm_new' : 'cancel' });
  }

  initStudentDialogListeners();
  studentDialogOutcome = { action: 'cancel' };

  if (studentDialogTitle) studentDialogTitle.textContent = options.title || '';
  if (studentDialogMessage) studentDialogMessage.textContent = options.message || '';

  if (studentDialogPreview) {
    if (options.normalizedName) {
      studentDialogPreview.textContent = `Будет записано как: «${options.normalizedName}»`;
      studentDialogPreview.hidden = false;
    } else {
      studentDialogPreview.textContent = '';
      studentDialogPreview.hidden = true;
    }
  }

  if (studentDialogWarnings) {
    const warns = Array.isArray(options.warnings) ? options.warnings.filter(Boolean) : [];
    if (warns.length > 0) {
      studentDialogWarnings.textContent = warns.join(' ');
      studentDialogWarnings.hidden = false;
    } else {
      studentDialogWarnings.textContent = '';
      studentDialogWarnings.hidden = true;
    }
  }

  if (studentDialogMatches) {
    studentDialogMatches.replaceChildren();
    const matches = Array.isArray(options.matches) ? options.matches : [];
    if (matches.length > 0) {
      const label = document.createElement('p');
      label.className = 'modal-matches__label';
      label.textContent = options.matchesLabel || 'Похожие студенты в списке группы:';
      studentDialogMatches.appendChild(label);

      matches.forEach((match) => {
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'button button--outline modal-match-btn';
        btn.textContent = `Выбрать «${match.display_name}»`;
        btn.addEventListener('click', () => {
          studentDialogOutcome = { action: 'select', student: match };
          studentConfirmDialog.close();
        });
        studentDialogMatches.appendChild(btn);
      });
      studentDialogMatches.hidden = false;
    } else {
      studentDialogMatches.hidden = true;
    }
  }

  if (studentDialogPickListBtn) {
    studentDialogPickListBtn.hidden = !options.allowPickList;
  }
  if (studentDialogConfirmBtn) {
    studentDialogConfirmBtn.textContent = options.confirmLabel || 'Всё равно создать нового';
  }

  return new Promise((resolve) => {
    studentDialogResolver = resolve;
    studentConfirmDialog.showModal();
    if (studentDialogCancelBtn) {
      studentDialogCancelBtn.focus();
    }
  });
}

function renderMatchCards(container, matches, labelPrefix, extraConfirm) {
  if (!container) return;
  container.replaceChildren();
  const hasMatches = Array.isArray(matches) && matches.length > 0;
  if (!hasMatches && !extraConfirm) {
    container.hidden = true;
    return;
  }

  if (hasMatches) {
    matches.forEach((match) => {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'button button--outline student-match-card';
      btn.textContent = `${labelPrefix} «${match.display_name}»`;
      btn.addEventListener('click', () => selectExistingStudent(match, true));
      container.appendChild(btn);
    });
  }

  if (extraConfirm) {
    const confirmBtn = document.createElement('button');
    confirmBtn.type = 'button';
    confirmBtn.className = 'button button--outline student-match-card';
    confirmBtn.textContent = extraConfirm.label;
    confirmBtn.addEventListener('click', extraConfirm.onClick);
    container.appendChild(confirmBtn);
  }

  container.hidden = false;
}

async function checkStudentNameApi(rawName, signal) {
  const csrfToken = getCsrfToken();
  const res = await fetch('/student/students/check', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Accept': 'application/json',
      ...(csrfToken ? { 'X-CSRFToken': csrfToken } : {}),
    },
    body: JSON.stringify({ display_name: rawName }),
    signal,
  });
  if (!res.ok) {
    throw new Error(`HTTP ${res.status}`);
  }
  return res.json();
}

function attachLiveNameChecker(inputEl, options) {
  if (!inputEl) return;
  let timerId = null;
  let abortController = null;
  let seq = 0;

  const clearFeedback = () => {
    if (options.previewEl) {
      options.previewEl.textContent = '';
      options.previewEl.hidden = true;
    }
    setWarningBox(options.warningEl, []);
    if (options.matchesEl) {
      options.matchesEl.replaceChildren();
      options.matchesEl.hidden = true;
    }
  };

  inputEl.addEventListener('input', () => {
    if (timerId) clearTimeout(timerId);
    if (abortController) abortController.abort();

    const raw = inputEl.value.trim();
    if (!raw) {
      clearFeedback();
      return;
    }

    const mySeq = ++seq;
    timerId = setTimeout(async () => {
      abortController = new AbortController();
      try {
        const result = await checkStudentNameApi(raw, abortController.signal);
        if (mySeq !== seq) return;

        if (options.previewEl) {
          if (result.normalized_name) {
            options.previewEl.textContent = `Будет записано как: «${result.normalized_name}»`;
            options.previewEl.hidden = false;
          } else {
            options.previewEl.textContent = '';
            options.previewEl.hidden = true;
          }
        }

        const warnings = Array.isArray(result.warnings) ? [...result.warnings] : [];
        if (options.warnOnFuzzy && Array.isArray(result.fuzzy_matches) && result.fuzzy_matches.length > 0 && (!result.exact_matches || result.exact_matches.length === 0)) {
          const names = result.fuzzy_matches.map((m) => `«${m.display_name}»`).join(', ');
          warnings.push(`Похожая запись уже есть в группе (${names}) — формат или написание могут быть неверными.`);
        }
        setWarningBox(options.warningEl, warnings);

        if (options.matchesEl) {
          if (result.exact_matches && result.exact_matches.length > 0) {
            renderMatchCards(options.matchesEl, result.exact_matches, 'Выбрать из списка:', null);
          } else if (result.fuzzy_matches && result.fuzzy_matches.length > 0) {
            renderMatchCards(options.matchesEl, result.fuzzy_matches, 'Возможно, вы имели в виду', null);
          } else {
            options.matchesEl.replaceChildren();
            options.matchesEl.hidden = true;
          }
        }
      } catch (err) {
        if (err && err.name === 'AbortError') return;
      }
    }, 200);
  });
}

async function promptMatchOrWarningDialog(checkData, { allowPickList, isSubmitMode = false } = {}) {
  const exact = Array.isArray(checkData.exact_matches) ? checkData.exact_matches : [];
  const fuzzy = Array.isArray(checkData.fuzzy_matches) ? checkData.fuzzy_matches : [];
  const warnings = Array.isArray(checkData.warnings) ? checkData.warnings : [];

  const matches = exact.length > 1 ? exact : fuzzy;
  const hasMatches = matches.length > 0;

  let title = 'Формат может быть неверным';
  let message = isSubmitMode
    ? 'Проверьте фамилию и имя перед отправкой работы — формат может быть неверным.'
    : 'Проверьте фамилию и имя перед добавлением — формат может быть неверным.';
  let confirmLabel = isSubmitMode ? 'Подтвердить и отправить' : 'Подтвердить и добавить';
  let matchesLabel = 'Похожие студенты в списке группы:';

  if (exact.length > 1) {
    title = 'Найдены совпадения в списке';
    message = 'В группе уже есть несколько студентов с таким именем. Выберите нужную запись или подтвердите создание новой.';
    confirmLabel = 'Всё равно создать нового';
    matchesLabel = 'Существующие студенты в группе:';
  } else if (hasMatches) {
    title = 'Найден похожий студент';
    message = 'В списке группы уже есть похожий студент. Возможно, вы уже добавлены — выберите запись из списка или создайте нового.';
    confirmLabel = isSubmitMode ? 'Всё равно отправить с новым именем' : 'Всё равно создать нового';
  }

  return openStudentConfirmDialog({
    title,
    message,
    normalizedName: checkData.normalized_name || '',
    warnings,
    matches,
    matchesLabel,
    confirmLabel,
    allowPickList: Boolean(allowPickList && hasMatches && groupStudents.length > 0),
  });
}

async function submitNewStudent(name, { confirmDuplicate = false, confirmSimilar = false } = {}) {
  setStatus(newStudentStatus, 'working', 'Добавляем…');
  if (!confirmDuplicate && !confirmSimilar && newStudentMatches) {
    newStudentMatches.replaceChildren();
    newStudentMatches.hidden = true;
  }

  try {
    const csrfToken = getCsrfToken();
    const res = await fetch('/student/students', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Accept': 'application/json',
        ...(csrfToken ? { 'X-CSRFToken': csrfToken } : {}),
      },
      body: JSON.stringify({
        display_name: name,
        confirm_duplicate: confirmDuplicate,
        confirm_similar: confirmSimilar,
      }),
    });

    if (res.status === 409) {
      const conflict = await res.json().catch(() => ({}));
      setStatus(newStudentStatus, '', '');
      const outcome = await promptMatchOrWarningDialog(
        {
          normalized_name: conflict.normalized_name || name,
          warnings: conflict.warnings || [],
          exact_matches: conflict.error === 'multiple_exact_students' ? (conflict.existing_students || []) : [],
          fuzzy_matches: conflict.fuzzy_matches || [],
        },
        { allowPickList: true },
      );
      if (outcome.action === 'select' && outcome.student) {
        selectExistingStudent(
          outcome.student,
          true,
          `Выбран существующий студент «${outcome.student.display_name}».`,
        );
      } else if (outcome.action === 'pick_list') {
        if (newStudentPanel) newStudentPanel.hidden = true;
        if (newStudentInput) newStudentInput.value = '';
        clearNewStudentFeedback();
        setStatus(studentSelectionNotice, 'info', 'Выберите подходящего студента из списка.');
        if (studentSelect) studentSelect.focus();
      } else if (outcome.action === 'confirm_new') {
        await submitNewStudent(name, {
          confirmDuplicate: conflict.error === 'multiple_exact_students',
          confirmSimilar: true,
        });
      } else if (newStudentInput) {
        newStudentInput.focus();
      }
      return;
    }

    if (!res.ok) {
      if (res.status === 403) {
        resetAccess();
        throw new Error('Доступ группы истёк или был отозван. Подтвердите пароль снова.');
      }
      const err = await res.json().catch(() => ({}));
      if (Array.isArray(err.warnings) && err.warnings.length > 0) {
        setWarningBox(newStudentWarning, err.warnings);
      }
      throw new Error(err.message || err.error || 'Ошибка при добавлении студента.');
    }

    const data = await res.json();
    if (res.status === 200 || data.existing) {
      selectExistingStudent(
        { id: data.id, display_name: data.display_name },
        true,
        `Студент «${data.display_name}» уже есть в списке группы и выбран автоматически.`,
      );
      return;
    }

    groupStudents.push({ id: data.id, display_name: data.display_name });
    groupStudents.sort((a, b) => a.display_name.localeCompare(b.display_name, 'ru'));
    updateStudentsList();
    if (studentSelect) studentSelect.value = data.id;

    if (newStudentPanel) newStudentPanel.hidden = true;
    if (newStudentInput) newStudentInput.value = '';
    clearNewStudentFeedback();

    const postWarnings = Array.isArray(data.warnings) ? [...data.warnings] : [];
    lastWarnedStudentId = postWarnings.length > 0 ? data.id : null;
    setWarningBox(studentSelectionWarning, postWarnings);
    setStatus(studentSelectionNotice, '', '');
  } catch (err) {
    setStatus(newStudentStatus, 'error', err.message);
  }
}

async function handleSaveNewStudent() {
  const name = newStudentInput ? newStudentInput.value.trim() : '';
  if (!name) {
    setStatus(newStudentStatus, 'error', 'Введите фамилию и имя студента.');
    if (newStudentInput) newStudentInput.focus();
    return;
  }

  if (saveStudentBtn) saveStudentBtn.disabled = true;
  setStatus(newStudentStatus, 'working', 'Проверяем имя…');
  try {
    const checkData = await checkStudentNameApi(name);
    setStatus(newStudentStatus, '', '');

    if (checkData.error === 'validation_error') {
      if (Array.isArray(checkData.warnings) && checkData.warnings.length > 0) {
        setWarningBox(newStudentWarning, checkData.warnings);
      }
      setStatus(newStudentStatus, 'error', 'Укажите фамилию и имя буквами.');
      if (newStudentInput) newStudentInput.focus();
      return;
    }

    if (checkData.is_paired) {
      if (Array.isArray(checkData.warnings) && checkData.warnings.length > 0) {
        setWarningBox(newStudentWarning, checkData.warnings);
      }
      setStatus(newStudentStatus, 'error', 'Укажите только одного студента в формате «Фамилия Имя».');
      if (newStudentInput) newStudentInput.focus();
      return;
    }

    const exact = Array.isArray(checkData.exact_matches) ? checkData.exact_matches : [];
    const fuzzy = Array.isArray(checkData.fuzzy_matches) ? checkData.fuzzy_matches : [];
    const warnings = Array.isArray(checkData.warnings) ? checkData.warnings : [];

    if (exact.length === 1) {
      selectExistingStudent(
        exact[0],
        true,
        `Студент «${exact[0].display_name}» уже есть в списке группы и выбран автоматически.`,
      );
      return;
    }

    if (exact.length > 1 || fuzzy.length > 0 || warnings.length > 0) {
      const outcome = await promptMatchOrWarningDialog(checkData, { allowPickList: true });
      if (outcome.action === 'select' && outcome.student) {
        selectExistingStudent(
          outcome.student,
          true,
          `Выбран существующий студент «${outcome.student.display_name}».`,
        );
      } else if (outcome.action === 'pick_list') {
        if (newStudentPanel) newStudentPanel.hidden = true;
        if (newStudentInput) newStudentInput.value = '';
        clearNewStudentFeedback();
        setStatus(studentSelectionNotice, 'info', 'Выберите подходящего студента из списка.');
        if (studentSelect) studentSelect.focus();
      } else if (outcome.action === 'confirm_new') {
        await submitNewStudent(name, {
          confirmDuplicate: exact.length > 1,
          confirmSimilar: true,
        });
      } else if (newStudentInput) {
        newStudentInput.focus();
      }
      return;
    }

    await submitNewStudent(name, { confirmDuplicate: false, confirmSimilar: false });
  } catch (err) {
    if (err && err.message === 'HTTP 403') {
      resetAccess();
      setStatus(newStudentStatus, 'error', 'Доступ группы истёк или был отозван. Подтвердите пароль снова.');
      return;
    }
    setStatus(newStudentStatus, 'error', (err && err.message) || 'Не удалось проверить имя студента.');
  } finally {
    if (saveStudentBtn) saveStudentBtn.disabled = false;
  }
}

if (discipline) {
  discipline.addEventListener('change', updateAssignments);
}

if (studentSelect) {
  studentSelect.addEventListener('change', () => {
    if (studentSelect.value !== lastWarnedStudentId) {
      lastWarnedStudentId = null;
      setWarningBox(studentSelectionWarning, []);
      setStatus(studentSelectionNotice, '', '');
    }
  });
}

if (newStudentInput) {
  attachLiveNameChecker(newStudentInput, {
    previewEl: newStudentPreview,
    warningEl: newStudentWarning,
    matchesEl: newStudentMatches,
    warnOnFuzzy: true,
  });
  newStudentInput.addEventListener('keydown', (event) => {
    if (event.key === 'Enter' && saveStudentBtn) {
      event.preventDefault();
      saveStudentBtn.click();
    }
  });
}

if (studentNameInput) {
  attachLiveNameChecker(studentNameInput, {
    previewEl: studentNamePreview,
    warningEl: studentNameWarning,
    matchesEl: null,
    warnOnFuzzy: true,
  });
  if (groupStudentsList) {
    fetch('/student/students', { headers: { 'Accept': 'application/json' } })
      .then((res) => (res.ok ? res.json() : []))
      .then((list) => {
        if (Array.isArray(list)) {
          groupStudents = list;
          updateStudentsList();
        }
      })
      .catch(() => {});
  }
}

if (toggleNewStudentBtn) {
  toggleNewStudentBtn.addEventListener('click', () => {
    if (newStudentPanel) {
      newStudentPanel.hidden = !newStudentPanel.hidden;
      if (!newStudentPanel.hidden && newStudentInput) {
        newStudentInput.focus();
      } else {
        clearNewStudentFeedback();
      }
    }
  });
}

if (cancelStudentBtn) {
  cancelStudentBtn.addEventListener('click', () => {
    if (newStudentPanel) {
      newStudentPanel.hidden = true;
      if (newStudentInput) newStudentInput.value = '';
      clearNewStudentFeedback();
    }
  });
}

if (saveStudentBtn) {
  saveStudentBtn.addEventListener('click', handleSaveNewStudent);
}

if (file) {
  file.addEventListener('change', () => {
    if (file.files[0]) showSelectedFile(file.files[0]);
    else resetFilePreview();
  });
}

if (clearFile && file) {
  clearFile.addEventListener('click', () => {
    resetFilePreview();
    file.focus();
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

      if (selectedGroup) selectedGroup.value = data.group_id;
      if (groupDisplay) groupDisplay.textContent = data.group_name || formData.get('group_id');
      unlockForm.hidden = true;
      if (accessSummary) accessSummary.hidden = false;
      if (submitForm) submitForm.hidden = false;
      showActiveStep(1);
      setStatus(unlockStatus, '', '');
      updateAssignments();
      updateStudentsList();
      if (assignmentSelect && assignmentSelect.tagName === 'SELECT') assignmentSelect.focus();
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

    const selectedFile = file && file.files ? file.files[0] : null;
    if (!selectedFile) {
      setStatus(submitStatus, 'error', 'Прикрепите файл с выполненной работой.');
      if (file) file.focus();
      return;
    }
    if (!showSelectedFile(selectedFile)) return;

    if (assignmentSelect && !assignmentSelect.value) {
      setStatus(submitStatus, 'error', 'Выберите тему занятия.');
      assignmentSelect.focus();
      return;
    }
    if (studentSelect && !studentSelect.value) {
      setStatus(submitStatus, 'error', 'Выберите или добавьте студента.');
      studentSelect.focus();
      return;
    }
    if (!studentSelect && studentNameInput) {
      const typedName = studentNameInput.value.trim();
      if (!typedName) {
        setStatus(submitStatus, 'error', 'Укажите фамилию и имя студента.');
        studentNameInput.focus();
        return;
      }

      try {
        const checkData = await checkStudentNameApi(typedName);
        if (checkData.error === 'validation_error') {
          if (Array.isArray(checkData.warnings) && studentNameWarning) {
            setWarningBox(studentNameWarning, checkData.warnings);
          }
          setStatus(submitStatus, 'error', 'Укажите фамилию и имя буквами.');
          studentNameInput.focus();
          return;
        }
        if (checkData.is_paired) {
          if (Array.isArray(checkData.warnings) && studentNameWarning) {
            setWarningBox(studentNameWarning, checkData.warnings);
          }
          setStatus(submitStatus, 'error', 'Укажите только одного студента в формате «Фамилия Имя».');
          studentNameInput.focus();
          return;
        }

        const exact = Array.isArray(checkData.exact_matches) ? checkData.exact_matches : [];
        const fuzzy = Array.isArray(checkData.fuzzy_matches) ? checkData.fuzzy_matches : [];
        const warnings = Array.isArray(checkData.warnings) ? checkData.warnings : [];

        if (exact.length >= 1) {
          studentNameInput.value = exact[0].display_name;
        } else if (fuzzy.length > 0 || warnings.length > 0) {
          const outcome = await promptMatchOrWarningDialog(checkData, {
            allowPickList: false,
            isSubmitMode: true,
          });
          if (outcome.action === 'select' && outcome.student) {
            studentNameInput.value = outcome.student.display_name;
          } else if (outcome.action !== 'confirm_new') {
            studentNameInput.focus();
            return;
          }
        }
      } catch (err) {
        if (err && err.message === 'HTTP 403') {
          setStatus(submitStatus, 'error', 'Доступ группы истёк или был отозван.');
          return;
        }
      }
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
          resetAccess();
          throw new Error('Приём работ для данной группы закрыт или дедлайн истёк.');
        } else if (response.status === 400) {
          const errData = await response.json().catch(() => ({}));
          if (errData.error === 'csrf_error') {
            throw new Error(errData.message || 'Сессия устарела. Обновите страницу и повторите действие.');
          } else if (errData.error === 'file_type_not_allowed') {
            throw new Error(errData.message || 'Недопустимый формат файла.');
          }
          if (Array.isArray(errData.warnings) && studentNameWarning) {
            setWarningBox(studentNameWarning, errData.warnings);
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
      if (studentNameInput && data.student_display_name) {
        studentNameInput.value = data.student_display_name;
      }
      if (Array.isArray(data.warnings) && data.warnings.length > 0) {
        setWarningBox(studentNameWarning || studentSelectionWarning, data.warnings);
      }
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
    resetFilePreview();
    setStatus(submitStatus, '', '');
    updateAssignments();
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
