function getCsrfToken() {
  const meta = document.querySelector('meta[name="csrf-token"]');
  return meta ? meta.getAttribute('content') : '';
}

const globalStatus = document.querySelector('#admin_students_status');

function setGlobalStatus(state, message) {
  if (!globalStatus) return;
  globalStatus.dataset.state = state;
  globalStatus.textContent = message;
  globalStatus.hidden = !message;
}

async function postJson(url, payload) {
  const csrfToken = getCsrfToken();
  const res = await fetch(url, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Accept': 'application/json',
      ...(csrfToken ? { 'X-CSRFToken': csrfToken } : {}),
    },
    body: JSON.stringify(payload),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(data.message || data.error || `Ошибка сервера (${res.status})`);
  }
  return data;
}

function reloadWithNotice(message) {
  const url = new URL(window.location.href);
  url.searchParams.set('notice', message);
  window.location.href = url.toString();
}

// 1. Batch normalize group(s)
const normalizeForm = document.querySelector('[data-normalize-form]');
if (normalizeForm) {
  normalizeForm.addEventListener('submit', async (event) => {
    event.preventDefault();
    const gid = normalizeForm.querySelector('[name="group_id"]')?.value || '';
    const ok = await window.AppDialog.confirm({
      title: 'Привести записи к единому формату?',
      message: 'Точные и регистровые дубли будут объединены, отчества и хвосты имён файлов — обрезаны до формата «Фамилия Имя». Все сданные работы и номера попыток сохранятся.',
      hint: 'Похожие ФИО (опечатки) и парные записи останутся для ручной проверки.',
      confirmLabel: 'Выполнить очистку',
      cancelLabel: 'Отмена',
    });
    if (!ok) return;

    setGlobalStatus('working', 'Выполняется нормализация и объединение…');
    try {
      const res = await postJson(normalizeForm.action, {
        group_id: gid || null,
        apply_fuzzy: false,
        split_paired: false,
      });
      reloadWithNotice(
        `Готово: объединено групп дублей — ${res.merged_groups}, переименовано записей — ${res.renamed_students}, перенесено работ — ${res.moved_submissions}.`
      );
    } catch (err) {
      setGlobalStatus('error', err.message);
    }
  });
}

// 2. One-click exact duplicate merge cards
document.querySelectorAll('[data-merge-btn]').forEach((btn) => {
  btn.addEventListener('click', async () => {
    const groupId = btn.dataset.groupId;
    const targetId = btn.dataset.targetId;
    const targetName = btn.dataset.targetName;
    const sourceIds = JSON.parse(btn.dataset.sourceIds || '[]');
    if (!groupId || !targetId || sourceIds.length === 0) return;

    const ok = await window.AppDialog.confirm({
      title: 'Объединить дубли студента?',
      message: `Все работы выбранных записей будут перенесены на «${targetName}» с перенумерацией попыток.`,
      confirmLabel: 'Объединить',
      cancelLabel: 'Отмена',
    });
    if (!ok) return;

    btn.disabled = true;
    setGlobalStatus('working', 'Объединяем записи…');
    try {
      const res = await postJson('/admin/students/merge', {
        group_id: groupId,
        target_student_id: targetId,
        source_student_ids: sourceIds,
        target_display_name: targetName,
      });
      reloadWithNotice(
        `Записи объединены в «${res.target_student.display_name}» (перенесено работ: ${res.moved_submissions}).`
      );
    } catch (err) {
      btn.disabled = false;
      setGlobalStatus('error', err.message);
    }
  });
});

// 3. Fuzzy candidate merge cards
document.querySelectorAll('[data-fuzzy-card]').forEach((card) => {
  const mergeBtn = card.querySelector('[data-fuzzy-merge-btn]');
  if (!mergeBtn) return;

  // Give each fuzzy card unique radio name so groups don't collide
  const cardIndex = Array.from(document.querySelectorAll('[data-fuzzy-card]')).indexOf(card);
  card.querySelectorAll('[data-fuzzy-target]').forEach((radio) => {
    radio.name = `fuzzy_target_group_${cardIndex}`;
  });

  mergeBtn.addEventListener('click', async () => {
    const groupId = card.dataset.groupId;
    const selectedMembers = Array.from(card.querySelectorAll('[data-fuzzy-member]:checked')).map((el) => el.value);
    const chosenTarget = card.querySelector('[data-fuzzy-target]:checked')?.value || selectedMembers[0];
    const targetName = card.querySelector('[data-fuzzy-target-name]')?.value?.trim() || '';

    if (selectedMembers.length < 2) {
      setGlobalStatus('warning', 'Выберите минимум двух студентов в карточке для объединения.');
      return;
    }
    const targetId = selectedMembers.includes(chosenTarget) ? chosenTarget : selectedMembers[0];
    const sourceIds = selectedMembers.filter((id) => id !== targetId);

    const ok = await window.AppDialog.confirm({
      title: 'Объединить похожие ФИО?',
      message: `Выбранные записи (${selectedMembers.length}) будут объединены в «${targetName || 'выбранного студента'}» с сохранением всех работ.`,
      hint: 'Убедитесь, что это действительно один и тот же человек, а не разные однофамильцы.',
      confirmLabel: 'Объединить',
      cancelLabel: 'Отмена',
    });
    if (!ok) return;

    mergeBtn.disabled = true;
    setGlobalStatus('working', 'Объединяем похожие записи…');
    try {
      const res = await postJson('/admin/students/merge', {
        group_id: groupId,
        target_student_id: targetId,
        source_student_ids: sourceIds,
        ...(targetName ? { target_display_name: targetName } : {}),
      });
      reloadWithNotice(
        `Похожие записи объединены в «${res.target_student.display_name}» (перенесено работ: ${res.moved_submissions}).`
      );
    } catch (err) {
      mergeBtn.disabled = false;
      setGlobalStatus('error', err.message);
    }
  });
});

// 4. Paired student splitting
document.querySelectorAll('[data-paired-card]').forEach((card) => {
  const container = card.querySelector('[data-paired-inputs]');
  const addBtn = card.querySelector('[data-add-split-target]');
  const splitBtn = card.querySelector('[data-split-btn]');
  if (!container || !splitBtn) return;

  if (addBtn) {
    addBtn.addEventListener('click', () => {
      const input = document.createElement('input');
      input.type = 'text';
      input.className = 'paired-target-input';
      input.dataset.pairedPartName = '';
      input.placeholder = 'Фамилия Имя';
      container.insertBefore(input, addBtn);
      input.focus();
    });
  }

  splitBtn.addEventListener('click', async () => {
    const groupId = card.dataset.groupId;
    const sourceId = card.dataset.sourceId;
    const names = Array.from(card.querySelectorAll('[data-paired-part-name]'))
      .map((el) => el.value.trim())
      .filter(Boolean);

    if (names.length < 1) {
      setGlobalStatus('warning', 'Укажите хотя бы одного студента в формате «Фамилия Имя».');
      return;
    }

    const ok = await window.AppDialog.confirm({
      title: 'Разделить парную запись?',
      message: `Сданные работы этой записи будут распределены между: ${names.map((n) => `«${n}»`).join(', ')}.`,
      hint: 'Файлы будут скопированы для каждого дополнительного студента.',
      confirmLabel: 'Разделить',
      cancelLabel: 'Отмена',
    });
    if (!ok) return;

    splitBtn.disabled = true;
    setGlobalStatus('working', 'Разделяем запись и копируем файлы…');
    try {
      const res = await postJson(`/admin/students/${sourceId}/split`, {
        group_id: groupId,
        targets: names.map((display_name) => ({ display_name })),
      });
      reloadWithNotice(
        `Парная запись разделена на ${res.target_students.length} студ. (перенесено: ${res.moved_submissions}, скопировано: ${res.copied_submissions}).`
      );
    } catch (err) {
      splitBtn.disabled = false;
      setGlobalStatus('error', err.message);
    }
  });
});

// 5. Inline rename forms
document.querySelectorAll('[data-rename-form]').forEach((form) => {
  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    const studentId = form.dataset.studentId;
    const input = form.querySelector('input[name="display_name"]');
    const rawName = input ? input.value.trim() : '';
    if (!rawName) {
      setGlobalStatus('error', 'Введите новое имя студента в формате «Фамилия Имя».');
      if (input) input.focus();
      return;
    }

    const btn = form.querySelector('button[type="submit"]');
    if (btn) btn.disabled = true;
    setGlobalStatus('working', 'Сохраняем новое имя…');
    try {
      const res = await postJson(`/admin/students/${studentId}/rename`, {
        display_name: rawName,
        normalize: true,
      });
      const warnNote = Array.isArray(res.warnings) && res.warnings.length > 0
        ? ` Внимание: ${res.warnings.join(' ')}`
        : '';
      reloadWithNotice(`Имя обновлено на «${res.student.display_name}».${warnNote}`);
    } catch (err) {
      if (btn) btn.disabled = false;
      setGlobalStatus('error', err.message);
    }
  });
});

// 6. Archive / Restore buttons
document.querySelectorAll('[data-archive-toggle]').forEach((btn) => {
  btn.addEventListener('click', async () => {
    const studentId = btn.dataset.studentId;
    const action = btn.dataset.action;
    const name = btn.dataset.displayName;

    if (action === 'archive') {
      const ok = await window.AppDialog.confirm({
        title: 'Отправить студента в архив?',
        message: `Запись «${name}» перестанет показываться при выборе студента, но все сданные работы останутся в истории.`,
        confirmLabel: 'В архив',
        cancelLabel: 'Отмена',
        danger: true,
      });
      if (!ok) return;
    }

    btn.disabled = true;
    try {
      await postJson(`/admin/students/${studentId}/${action}`, {});
      reloadWithNotice(action === 'archive' ? `Студент «${name}» перенесён в архив.` : `Студент «${name}» восстановлен.`);
    } catch (err) {
      btn.disabled = false;
      setGlobalStatus('error', err.message);
    }
  });
});

// 7. Manual selection merge bar
const checkboxes = Array.from(document.querySelectorAll('.student-select-checkbox'));
const manualBarControls = document.querySelector('#manual_merge_controls');
const manualHint = document.querySelector('#manual_merge_hint');
const manualTargetSelect = document.querySelector('#manual_merge_target');
const manualTargetNameInput = document.querySelector('#manual_merge_name');
const manualMergeSubmit = document.querySelector('#manual_merge_submit');

function updateManualMergeBar() {
  if (!manualBarControls || !manualHint || !manualTargetSelect || !manualTargetNameInput) return;
  const checked = checkboxes.filter((cb) => cb.checked);
  if (checked.length < 2) {
    manualBarControls.hidden = true;
    manualHint.textContent = 'Отметьте 2 или более студентов одной группы в таблице ниже, чтобы объединить их вручную.';
    return;
  }

  const groupIds = new Set(checked.map((cb) => cb.dataset.groupId));
  if (groupIds.size > 1) {
    manualBarControls.hidden = true;
    manualHint.textContent = 'Выбраны студенты из разных групп — для объединения выберите записи одной группы.';
    return;
  }

  manualHint.textContent = `Выбрано записей в группе: ${checked.length}. Выберите основную запись и проверьте «Фамилия Имя»:`;
  manualTargetSelect.replaceChildren();
  checked.forEach((cb) => {
    const opt = document.createElement('option');
    opt.value = cb.value;
    opt.textContent = cb.dataset.displayName || cb.value;
    manualTargetSelect.appendChild(opt);
  });
  manualTargetNameInput.value = checked[0].dataset.displayName || '';
  manualBarControls.hidden = false;
}

if (manualTargetSelect && manualTargetNameInput) {
  manualTargetSelect.addEventListener('change', () => {
    const selectedOpt = manualTargetSelect.options[manualTargetSelect.selectedIndex];
    if (selectedOpt) manualTargetNameInput.value = selectedOpt.textContent;
  });
}

checkboxes.forEach((cb) => cb.addEventListener('change', updateManualMergeBar));

if (manualMergeSubmit && manualTargetSelect && manualTargetNameInput) {
  manualMergeSubmit.addEventListener('click', async () => {
    const checked = checkboxes.filter((cb) => cb.checked);
    const groupId = checked[0]?.dataset.groupId;
    const targetId = manualTargetSelect.value;
    const sourceIds = checked.map((cb) => cb.value).filter((id) => id !== targetId);
    const targetName = manualTargetNameInput.value.trim();

    if (!groupId || !targetId || sourceIds.length === 0) return;

    const ok = await window.AppDialog.confirm({
      title: 'Объединить отмеченных студентов?',
      message: `В запись «${targetName || targetId}» будут объединены ${checked.length} отмеченных записей с переносом всех работ.`,
      confirmLabel: 'Объединить',
      cancelLabel: 'Отмена',
    });
    if (!ok) return;

    manualMergeSubmit.disabled = true;
    setGlobalStatus('working', 'Объединяем выбранных студентов…');
    try {
      const res = await postJson('/admin/students/merge', {
        group_id: groupId,
        target_student_id: targetId,
        source_student_ids: sourceIds,
        ...(targetName ? { target_display_name: targetName } : {}),
      });
      reloadWithNotice(`Объединение завершено: «${res.target_student.display_name}» (перенесено работ: ${res.moved_submissions}).`);
    } catch (err) {
      manualMergeSubmit.disabled = false;
      setGlobalStatus('error', err.message);
    }
  });
}
