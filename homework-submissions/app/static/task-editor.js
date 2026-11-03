document.addEventListener('DOMContentLoaded', () => {
  const form = document.querySelector('#task_editor_form');
  if (!form) return;

  const subjectSelect = document.querySelector('#subject_select');
  const topicSelect = document.querySelector('#topic_select');
  const copyTopicBtn = document.querySelector('#copy_topic_task_btn');
  const copyNotice = document.querySelector('#copy_topic_notice');

  const tabEditorBtn = document.querySelector('#tab_editor_btn');
  const tabPreviewBtn = document.querySelector('#tab_preview_btn');
  const paneEditor = document.querySelector('#pane_editor');
  const panePreview = document.querySelector('#pane_preview');
  const previewContent = document.querySelector('#task_preview_content');

  const descTextarea = document.querySelector('#task_description');
  const attachmentIdInput = document.querySelector('#attachment_id_input');
  const removeAttachmentInput = document.querySelector('#remove_attachment_input');
  const attachmentCurrentCard = document.querySelector('#attachment_current_card');
  const attachmentNameDisplay = document.querySelector('#attachment_name_display');
  const attachmentSizeDisplay = document.querySelector('#attachment_size_display');
  const downloadLink = document.querySelector('#download_attachment_link');
  const replaceAttachmentBtn = document.querySelector('#replace_attachment_btn');
  const deleteAttachmentBtn = document.querySelector('#delete_attachment_btn');
  const attachmentUploadBlock = document.querySelector('#attachment_upload_block');
  const fileInput = document.querySelector('#task_file_input');
  const cancelUploadBtn = document.querySelector('#cancel_upload_btn');

  const saveBtn = document.querySelector('#save_btn');
  const cancelBtn = document.querySelector('#cancel_btn');
  const editorStatus = document.querySelector('#editor_status');

  let isDirty = false;
  let isSubmitting = false;

  function markDirty() {
    isDirty = true;
  }

  // Format bytes helper
  function formatBytes(bytes) {
    if (!bytes || bytes === 0) return '0 Б';
    const k = 1024;
    const sizes = ['Б', 'КБ', 'МБ', 'ГБ'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + ' ' + sizes[i];
  }

  function restoreFocus(focusTrigger) {
    if (!focusTrigger || !(focusTrigger instanceof HTMLElement) || !focusTrigger.isConnected) {
      return;
    }
    if (focusTrigger.matches(':disabled')) {
      return;
    }
    if (document.querySelector('#app_dialog[open]')) {
      return;
    }
    const active = document.activeElement;
    const inClosedDialog = active && active.closest && active.closest('#app_dialog:not([open])');
    if (!active || active === document.body || inClosedDialog) {
      focusTrigger.focus();
    }
  }

  let currentAttachment = null;
  const initialAttId = (attachmentIdInput?.value || '').trim();
  if (initialAttId) {
    const name = attachmentNameDisplay ? attachmentNameDisplay.textContent.trim() : '';
    const size = attachmentSizeDisplay ? attachmentSizeDisplay.textContent.trim() : '';
    const dlHref = downloadLink ? downloadLink.getAttribute('href') : `/admin/tasks/attachments/${initialAttId}`;
    currentAttachment = {
      id: initialAttId,
      name,
      sizeFormatted: size,
      downloadUrl: dlHref,
      source: 'initial',
      topicId: null,
    };
  }

  function updateAttachmentUI() {
    if (currentAttachment) {
      if (attachmentNameDisplay) attachmentNameDisplay.textContent = currentAttachment.name;
      if (attachmentSizeDisplay) attachmentSizeDisplay.textContent = currentAttachment.sizeFormatted;
      if (downloadLink) {
        if (currentAttachment.downloadUrl) {
          downloadLink.href = currentAttachment.downloadUrl;
          downloadLink.style.display = '';
        } else {
          downloadLink.removeAttribute('href');
          downloadLink.style.display = 'none';
        }
      }
      if (attachmentCurrentCard) attachmentCurrentCard.style.display = 'flex';
      if (fileInput && fileInput.files && fileInput.files.length > 0) {
        if (attachmentUploadBlock) attachmentUploadBlock.style.display = 'block';
        if (cancelUploadBtn) cancelUploadBtn.style.display = 'inline-block';
      } else {
        if (attachmentUploadBlock) attachmentUploadBlock.style.display = 'none';
        if (cancelUploadBtn) cancelUploadBtn.style.display = 'none';
      }
    } else {
      if (downloadLink) {
        downloadLink.removeAttribute('href');
        downloadLink.style.display = 'none';
      }
      if (attachmentCurrentCard) attachmentCurrentCard.style.display = 'none';
      if (attachmentUploadBlock) attachmentUploadBlock.style.display = 'block';
      if (cancelUploadBtn) cancelUploadBtn.style.display = 'none';
    }
  }

  // Filter topics by selected subject
  function filterTopics() {
    if (!subjectSelect || !topicSelect) return;
    const selectedSubjectId = subjectSelect.value;
    let firstVisible = null;
    let currentSelectedStillValid = false;

    Array.from(topicSelect.options).forEach((opt) => {
      const match = opt.dataset.subjectId === selectedSubjectId;
      opt.hidden = !match;
      opt.disabled = !match;
      if (match) {
        if (!firstVisible) firstVisible = opt;
        if (opt.value === topicSelect.value) {
          currentSelectedStillValid = true;
        }
      }
    });

    if (!currentSelectedStillValid) {
      if (firstVisible) {
        topicSelect.value = firstVisible.value;
      } else {
        topicSelect.value = '';
      }
    }
  }

  if (topicSelect) {
    topicSelect.addEventListener('change', () => {
      markDirty();
    });
  }

  if (subjectSelect) {
    subjectSelect.addEventListener('change', () => {
      markDirty();
      filterTopics();
    });
    filterTopics();
  }
  // Tabs: Editor vs Preview
  if (tabEditorBtn && tabPreviewBtn) {
    tabEditorBtn.addEventListener('click', () => {
      tabEditorBtn.classList.add('is-active');
      tabEditorBtn.setAttribute('aria-selected', 'true');
      tabPreviewBtn.classList.remove('is-active');
      tabPreviewBtn.setAttribute('aria-selected', 'false');

      paneEditor.hidden = false;
      panePreview.hidden = true;
    });

    tabPreviewBtn.addEventListener('click', async () => {
      tabPreviewBtn.classList.add('is-active');
      tabPreviewBtn.setAttribute('aria-selected', 'true');
      tabEditorBtn.classList.remove('is-active');
      tabEditorBtn.setAttribute('aria-selected', 'false');

      paneEditor.hidden = true;
      panePreview.hidden = false;

      // Request server preview
      previewContent.innerHTML = '<p class="field__hint">Загрузка предпросмотра...</p>';
      const currentFormat = form.querySelector('input[name="description_format"]:checked')?.value || 'txt';
      const text = descTextarea.value;

      try {
        const formData = new FormData();
        formData.append('description', text);
        formData.append('description_format', currentFormat);

        const res = await fetch('/admin/tasks/preview', {
          method: 'POST',
          body: formData,
        });
        if (!res.ok) {
          throw new Error('Ошибка при загрузке предпросмотра');
        }
        const data = await res.json();
        if (!data.html || !data.html.trim()) {
          previewContent.innerHTML = '<p class="field__hint" style="font-style: italic;">Описание не заполнено.</p>';
        } else {
          previewContent.innerHTML = data.html;
          if (currentFormat === 'txt') {
            previewContent.classList.add('task-prose--txt');
          } else {
            previewContent.classList.remove('task-prose--txt');
          }
        }
      } catch (err) {
        previewContent.innerHTML = `<p class="field__hint" style="color: var(--primitive-ruby-600);">${err.message || 'Ошибка предпросмотра'}</p>`;
      }
    });
  }

  // Apply topic task data to form
  function applyTopicTask(data, appliedTopicId) {
    descTextarea.value = data.description || '';
    const fmtRadio = form.querySelector(`input[name="description_format"][value="${data.description_format || 'txt'}"]`);
    if (fmtRadio) fmtRadio.checked = true;

    if (fileInput) fileInput.value = '';

    if (data.attachment) {
      currentAttachment = {
        id: data.attachment.id,
        name: data.attachment.original_name,
        sizeFormatted: data.attachment.size_bytes ? `(${formatBytes(data.attachment.size_bytes)})` : '',
        downloadUrl: `/admin/tasks/attachments/${data.attachment.id}`,
        source: 'topic',
        topicId: appliedTopicId,
      };
      attachmentIdInput.value = data.attachment.id;
      removeAttachmentInput.value = '0';
    } else {
      currentAttachment = null;
      attachmentIdInput.value = '';
      removeAttachmentInput.value = '0';
    }
    updateAttachmentUI();
    markDirty();

    if (copyNotice) {
      copyNotice.style.color = 'var(--primitive-emerald-600)';
      copyNotice.textContent = 'Задание темы скопировано!';
      setTimeout(() => { copyNotice.textContent = ''; }, 3000);
    }
  }

  // "Взять задание темы"
  if (copyTopicBtn && topicSelect) {
    copyTopicBtn.addEventListener('click', async () => {
      const focusTrigger = document.activeElement;
      const topicId = topicSelect.value;
      const topicName = topicSelect.options[topicSelect.selectedIndex]?.text?.trim() || '';
      if (!topicId) {
        if (copyNotice) copyNotice.textContent = 'Сначала выберите тему.';
        return;
      }
      copyTopicBtn.disabled = true;
      if (copyNotice) copyNotice.textContent = 'Получаем задание темы...';

      try {
        const res = await fetch(`/admin/topics/${topicId}/task`, {
          headers: { Accept: 'application/json' },
        });
        if (!res.ok) {
          throw new Error('Не удалось загрузить задание темы');
        }
        const data = await res.json();

        if (topicSelect.value !== topicId) {
          if (copyNotice) copyNotice.textContent = '';
          return;
        }

        if (!data.has_task) {
          if (copyNotice) {
            copyNotice.style.color = 'var(--primitive-amber-600)';
            copyNotice.textContent = 'У темы нет заготовленного задания';
          }
          return;
        }

        if (copyNotice) copyNotice.textContent = '';

        let details = '';
        if (data.attachment) {
          const sizeStr = data.attachment.size_bytes ? ` (${formatBytes(data.attachment.size_bytes)})` : '';
          details = `Вложение темы: ${data.attachment.original_name}${sizeStr}`;
        } else {
          details = 'Тема не содержит прикреплённого файла.';
        }

        const hasExistingContent = Boolean(
          descTextarea.value.trim() ||
          attachmentIdInput.value ||
          currentAttachment ||
          (fileInput && fileInput.files && fileInput.files.length > 0)
        );

        const hint = (isDirty || hasExistingContent)
          ? 'Текущее описание и прикреплённые материалы занятия будут заменены.'
          : '';

        const confirmed = await AppDialog.confirm({
          title: 'Взять задание темы',
          message: `Скопировать заготовленное задание «${topicName}» в текущее занятие?`,
          details,
          hint,
          confirmLabel: 'Скопировать',
        });

        if (!confirmed) {
          return;
        }

        if (topicSelect.value !== topicId) {
          return;
        }

        applyTopicTask(data, topicId);
      } catch (err) {
        if (copyNotice) {
          copyNotice.style.color = 'var(--primitive-ruby-600)';
          copyNotice.textContent = err.message || 'Ошибка загрузки';
        }
      } finally {
        copyTopicBtn.disabled = false;
        restoreFocus(focusTrigger);
      }
    });
  }

  // Attachment controls
  if (replaceAttachmentBtn) {
    replaceAttachmentBtn.addEventListener('click', () => {
      markDirty();
      if (attachmentUploadBlock) attachmentUploadBlock.style.display = 'block';
      if (cancelUploadBtn) {
        cancelUploadBtn.style.display = currentAttachment ? 'inline-block' : 'none';
      }
      if (fileInput) fileInput.focus();
    });
  }

  if (cancelUploadBtn) {
    cancelUploadBtn.addEventListener('click', () => {
      if (fileInput) fileInput.value = '';
      if (currentAttachment) {
        attachmentIdInput.value = currentAttachment.id;
        removeAttachmentInput.value = '0';
      } else {
        attachmentIdInput.value = '';
        removeAttachmentInput.value = '0';
      }
      updateAttachmentUI();
    });
  }

  if (deleteAttachmentBtn) {
    deleteAttachmentBtn.addEventListener('click', () => {
      markDirty();
      currentAttachment = null;
      attachmentIdInput.value = '';
      removeAttachmentInput.value = '1';
      if (fileInput) fileInput.value = '';
      updateAttachmentUI();
    });
  }

  if (fileInput) {
    fileInput.addEventListener('change', () => {
      if (fileInput.files && fileInput.files.length > 0) {
        markDirty();
        removeAttachmentInput.value = '0';
        attachmentIdInput.value = '';
        if (currentAttachment && cancelUploadBtn) {
          cancelUploadBtn.style.display = 'inline-block';
        }
      }
    });
  }

  // Track changes across form inputs
  form.addEventListener('input', () => markDirty());
  form.addEventListener('change', () => markDirty());

  window.addEventListener('beforeunload', (event) => {
    if (isDirty && !isSubmitting) {
      event.preventDefault();
      event.returnValue = '';
    }
  });

  // Intercept navigation links when form is dirty
  document.addEventListener('click', async (e) => {
    if (e.button !== 0 || e.ctrlKey || e.metaKey || e.shiftKey || e.altKey) return;
    const link = e.target.closest('a[href]');
    if (!link) return;
    const target = (link.getAttribute('target') || '').trim().toLowerCase();
    if (target && target !== '_self') return;
    if (link.hasAttribute('download')) return;
    const rawHref = link.getAttribute('href');
    if (!rawHref || rawHref.startsWith('#')) return;
    const lower = rawHref.toLowerCase();
    if (lower.startsWith('javascript:') || lower.startsWith('mailto:') || lower.startsWith('tel:')) return;

    if (!isDirty || isSubmitting) return;

    e.preventDefault();
    const destination = link.href;

    const confirmed = await AppDialog.confirm({
      title: 'Несохранённые изменения',
      message: 'Есть несохранённые изменения. Вы действительно хотите уйти?',
      confirmLabel: 'Уйти',
      danger: true,
    });

    if (confirmed) {
      isDirty = false;
      window.location.assign(destination);
    }
  });
  // Intercept logout form when dirty
  const logoutForm = document.querySelector('#admin_logout');
  let pendingLogout = false;
  if (logoutForm) {
    logoutForm.addEventListener('submit', async (e) => {
      if (isSubmitting || pendingLogout) {
        e.preventDefault();
        return;
      }
      if (!isDirty) {
        return;
      }
      e.preventDefault();
      pendingLogout = true;
      try {
        const confirmed = await AppDialog.confirm({
          title: 'Несохранённые изменения',
          message: 'Есть несохранённые изменения. Вы действительно хотите выйти?',
          confirmLabel: 'Выйти',
          danger: true,
        });
        if (confirmed) {
          isDirty = false;
          HTMLFormElement.prototype.submit.call(logoutForm);
        }
      } finally {
        pendingLogout = false;
      }
    });
  }


  // Form submission via fetch
  form.addEventListener('submit', async (e) => {
    e.preventDefault();

    // Check for conflicting actions
    if (removeAttachmentInput.value === '1' && fileInput && fileInput.files && fileInput.files.length > 0) {
      await AppDialog.alert({
        title: 'Конфликт вложений',
        message: 'Конфликт: одновременно выбрано удаление файла и новый файл для загрузки.',
      });
      return;
    }

    if (saveBtn) saveBtn.disabled = true;
    if (editorStatus) {
      editorStatus.dataset.state = 'working';
      editorStatus.textContent = 'Сохранение...';
    }
    isSubmitting = true;

    try {
      const formData = new FormData(form);
      const res = await fetch(form.action, {
        method: 'POST',
        body: formData,
      });

      if (res.status === 409) {
        const body = await res.json().catch(() => ({}));
        isSubmitting = false;
        if (saveBtn) saveBtn.disabled = false;
        if (editorStatus) {
          editorStatus.dataset.state = 'error';
          editorStatus.textContent = body.message || 'Пара на эту дату и номер уже занята.';
        }
        return;
      }

      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.message || 'Ошибка сохранения');
      }

      const body = await res.json().catch(() => ({}));
      isDirty = false;
      if (editorStatus) {
        editorStatus.dataset.state = 'success';
        editorStatus.textContent = 'Успешно сохранено!';
      }

      const redirectTarget = body.redirect_url || cancelBtn?.href || '/admin/assignments';
      window.location.href = redirectTarget;
    } catch (err) {
      isSubmitting = false;
      if (saveBtn) saveBtn.disabled = false;
      if (editorStatus) {
        editorStatus.dataset.state = 'error';
        editorStatus.textContent = err.message || 'Не удалось сохранить.';
      }
    }
  });
});
