document.addEventListener('DOMContentLoaded', () => {
  const subjectSelect = document.querySelector('#assignment_subject');
  const topicSelect = document.querySelector('#assignment_topic');
  const dateInput = document.querySelector('#assignment_date');
  const createForm = document.querySelector('#assignment_create_form');
  const createStatus = document.querySelector('#create_status') || document.querySelector('#assignment_create_status');

  if (dateInput && !dateInput.value) {
    const today = new Date();
    const year = today.getFullYear();
    const month = String(today.getMonth() + 1).padStart(2, '0');
    const day = String(today.getDate()).padStart(2, '0');
    dateInput.value = `${year}-${month}-${day}`;
  }

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

  if (subjectSelect) {
    subjectSelect.addEventListener('change', filterTopics);
    filterTopics();
  }

  function filterEditFormTopics(form) {
    const editSubjectSelect = form.querySelector('select[name="subject_id"]');
    const editTopicSelect = form.querySelector('select[name="topic_id"]');
    if (!editTopicSelect) return;
    const targetSubjectId = editSubjectSelect ? editSubjectSelect.value : editTopicSelect.dataset.subjectId;
    if (!targetSubjectId) return;

    let firstVisible = null;
    let currentSelectedStillValid = false;

    Array.from(editTopicSelect.options).forEach((opt) => {
      const match = opt.dataset.subjectId === targetSubjectId;
      opt.hidden = !match;
      opt.disabled = !match;
      if (match) {
        if (!firstVisible) firstVisible = opt;
        if (opt.value === editTopicSelect.value) {
          currentSelectedStillValid = true;
        }
      }
    });

    if (!currentSelectedStillValid) {
      if (firstVisible) {
        editTopicSelect.value = firstVisible.value;
      } else {
        editTopicSelect.value = '';
      }
    }
  }

  if (createForm) {
    createForm.addEventListener('submit', async (e) => {
      e.preventDefault();
      const submitBtn = createForm.querySelector('button[type="submit"]');
      if (submitBtn) submitBtn.disabled = true;
      if (createStatus) {
        createStatus.dataset.state = 'working';
        createStatus.textContent = 'Сохраняем...';
      }

      try {
        const res = await fetch(createForm.action, {
          method: 'POST',
          body: new FormData(createForm),
        });
        if (res.status === 409) {
          const body = await res.json().catch(() => ({}));
          if (createStatus) {
            createStatus.dataset.state = 'error';
            createStatus.textContent = body.message || 'Данная пара уже занята.';
          }
          return;
        }
        if (!res.ok) {
          const body = await res.json().catch(() => ({}));
          throw new Error(body.message || 'Ошибка сохранения');
        }

        if (createStatus) {
          createStatus.dataset.state = 'success';
          createStatus.textContent = 'Пара успешно запланирована!';
        }
        setTimeout(() => window.location.reload(), 400);
      } catch (err) {
        if (createStatus) {
          createStatus.dataset.state = 'error';
          createStatus.textContent = err.message || 'Ошибка сохранения: проверьте введённые данные.';
        }
      } finally {
        if (submitBtn) submitBtn.disabled = false;
      }
    });
  }

  document.querySelectorAll('.assignment-card').forEach((card) => {
    const editBtn = card.querySelector('[data-action="edit"]');
    const deleteBtn = card.querySelector('[data-action="delete"]');
    const cancelBtn = card.querySelector('[data-action="cancel"]');
    const editForm = card.querySelector('.assignment-edit-form');
    const editStatus = card.querySelector('.edit-status');
    const cardId = card.dataset.assignmentId;

    if (editForm) {
      filterEditFormTopics(editForm);
      const editSubjectSelect = editForm.querySelector('select[name="subject_id"]');
      if (editSubjectSelect) {
        editSubjectSelect.addEventListener('change', () => filterEditFormTopics(editForm));
      }
    }

    if (editBtn && editForm) {
      editBtn.addEventListener('click', () => {
        editForm.hidden = false;
        editBtn.hidden = true;
      });
    }

    if (cancelBtn && editForm) {
      cancelBtn.addEventListener('click', () => {
        editForm.hidden = true;
        editBtn.hidden = false;
        if (editStatus) {
          editStatus.textContent = '';
          delete editStatus.dataset.state;
        }
      });
    }

    if (editForm) {
      editForm.addEventListener('submit', async (e) => {
        e.preventDefault();
        const submitBtn = editForm.querySelector('button[type="submit"]');
        if (submitBtn) submitBtn.disabled = true;
        if (editStatus) {
          editStatus.dataset.state = 'working';
          editStatus.textContent = 'Обновляем...';
        }

        try {
          const res = await fetch(editForm.action, {
            method: 'POST',
            body: new FormData(editForm),
          });
          if (res.status === 409) {
            const body = await res.json().catch(() => ({}));
            if (editStatus) {
              editStatus.dataset.state = 'error';
              editStatus.textContent = body.message || 'Пара уже занята.';
            }
            return;
          }
          if (!res.ok) {
            const body = await res.json().catch(() => ({}));
            throw new Error(body.message || 'Ошибка обновления');
          }

          const body = await res.json().catch(() => ({}));
          const dateBadge = card.querySelector('.date-badge');
          if (dateBadge && body.lesson_date) {
            const parts = body.lesson_date.split('-');
            if (parts.length === 3) dateBadge.textContent = `${parts[2]}.${parts[1]}.${parts[0]}`;
          }
          const pairChip = card.querySelector('.pair-chip');
          if (pairChip && body.pair_number) {
            pairChip.textContent = `Пара ${body.pair_number}`;
          }
          const topicSelect = editForm.querySelector('select[name="topic_id"]');
          const topicHeading = card.querySelector('h3');
          if (topicSelect && topicHeading) {
            const selectedText = topicSelect.options[topicSelect.selectedIndex]?.text;
            if (selectedText) topicHeading.textContent = selectedText;
          }

          if (editStatus) {
            editStatus.dataset.state = 'success';
            editStatus.textContent = 'Обновлено!';
          }
          setTimeout(() => window.location.reload(), 400);
        } catch (err) {
          if (editStatus) {
            editStatus.dataset.state = 'error';
            editStatus.textContent = err.message || 'Ошибка при обновлении.';
          }
        } finally {
          if (submitBtn) submitBtn.disabled = false;
        }
      });
    }

    if (deleteBtn) {
      deleteBtn.addEventListener('click', async () => {
        const topicTitle = card.querySelector('h3')?.textContent || '';
        if (!confirm(`Удалить занятие «${topicTitle}»?`)) return;

        try {
          const res = await fetch(`/admin/assignments/${cardId}/delete`, {
            method: 'POST',
          });
          if (!res.ok) throw new Error('Delete failed');
          card.remove();
          const countEl = document.querySelector('#assigned_count');
          if (countEl) {
            const current = parseInt(countEl.textContent, 10);
            if (!isNaN(current) && current > 0) {
              countEl.textContent = String(current - 1);
            }
          }
          const list = document.querySelector('[data-assignment-list]');
          if (list && list.querySelectorAll('.assignment-card').length === 0) {
            const journal = document.querySelector('.assignment-journal');
            if (journal) {
              journal.innerHTML = '<p class="empty-state">Для выбранной группы пока не назначено ни одной пары. Заполните форму слева, чтобы запланировать занятие.</p>';
            }
          }
        } catch {
          alert('Не удалось удалить занятие.');
        }
      });
    }
  });
});
