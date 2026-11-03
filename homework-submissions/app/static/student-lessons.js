document.addEventListener('DOMContentLoaded', () => {
  const workspaceView = document.querySelector('#student_workspace_view');
  if (!workspaceView) return;

  const searchInput = document.querySelector('#lessons_search');
  const subjectSelect = document.querySelector('#lessons_subject_filter');
  const dateInput = document.querySelector('#lessons_date_filter');
  const resetBtn = document.querySelector('#lessons_reset_filter_btn');
  const listContainer = document.querySelector('#student_lessons_list');
  const emptyContainer = document.querySelector('#student_lessons_empty');

  let allAssignments = [];

  function escapeHtml(str) {
    if (!str) return '';
    return str
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#039;');
  }

  function formatDate(isoDate) {
    if (!isoDate) return '';
    const parts = isoDate.split('-');
    if (parts.length === 3) return `${parts[2]}.${parts[1]}.${parts[0]}`;
    return isoDate;
  }

  function getFilterParams() {
    const q = searchInput?.value.trim() || '';
    const subjectId = subjectSelect?.value || '';
    const date = dateInput?.value || '';
    return { q, subjectId, date };
  }

  function syncUrlParams() {
    const { q, subjectId, date } = getFilterParams();
    const url = new URL(window.location.href);

    if (q) url.searchParams.set('q', q);
    else url.searchParams.delete('q');

    if (subjectId) url.searchParams.set('subject_id', subjectId);
    else url.searchParams.delete('subject_id');

    if (date) url.searchParams.set('date', date);
    else url.searchParams.delete('date');

    window.history.replaceState({}, '', url.toString());
  }

  function buildFilterQueryString() {
    const { q, subjectId, date } = getFilterParams();
    const params = new URLSearchParams();
    if (q) params.set('q', q);
    if (subjectId) params.set('subject_id', subjectId);
    if (date) params.set('date', date);
    const qs = params.toString();
    return qs ? `&${qs}` : '';
  }


  function renderList() {
    if (!listContainer || !emptyContainer) return;
    const { q, subjectId, date } = getFilterParams();
    syncUrlParams();

    if (allAssignments.length === 0) {
      listContainer.innerHTML = '';
      emptyContainer.hidden = false;
      emptyContainer.innerHTML = '<p>Для вашей группы пока нет занятий в расписании.</p>';
      return;
    }

    const qLower = q.toLowerCase();
    const filtered = allAssignments.filter((item) => {
      if (subjectId && item.subject_id !== subjectId) return false;
      if (date && item.lesson_date !== date) return false;
      if (qLower) {
        const subMatch = (item.subject_name || '').toLowerCase().includes(qLower);
        const topMatch = (item.topic_name || '').toLowerCase().includes(qLower);
        if (!subMatch && !topMatch) return false;
      }
      return true;
    });

    if (filtered.length === 0) {
      listContainer.innerHTML = '';
      emptyContainer.hidden = false;
      emptyContainer.innerHTML = `
        <p>Занятия по выбранным фильтрам не найдены.</p>
        <button class="button button--outline" type="button" id="inner_reset_filters" style="margin-top: var(--space-2);">Сбросить фильтры</button>
      `;
      const innerReset = emptyContainer.querySelector('#inner_reset_filters');
      if (innerReset) {
        innerReset.addEventListener('click', resetFilters);
      }
      return;
    }

    emptyContainer.hidden = true;
    const filterQs = buildFilterQueryString();

    const html = filtered.map((item) => `
      <article class="panel catalog-row assignment-card" data-assignment-id="${escapeHtml(item.id)}">
        <div class="catalog-row__heading">
          <div class="assignment-meta">
            <span class="date-badge">${formatDate(item.lesson_date)}</span>
            <span class="chip--pair">${item.pair_number} пара</span>
            ${item.has_description ? '<span class="chip chip--open">Есть описание</span>' : ''}
            ${item.has_attachment ? '<span class="chip chip--neutral">Есть файл</span>' : ''}
          </div>
          <div class="assignment-details">
            <p class="eyebrow">${escapeHtml(item.subject_name)}</p>
            <h3>${escapeHtml(item.topic_name)}</h3>
          </div>
        </div>
        <div class="catalog-actions" style="margin-left: auto;">
          <a class="button button--primary" href="/student/assignments/${encodeURIComponent(item.id)}?${filterQs.slice(1)}">Открыть занятие</a>
        </div>
      </article>
    `).join('');

    listContainer.innerHTML = html;
  }

  function resetFilters() {
    if (searchInput) searchInput.value = '';
    if (subjectSelect) subjectSelect.value = '';
    if (dateInput) dateInput.value = '';
    renderList();
  }

  // Restore initial filters from URL params
  const initialParams = new URLSearchParams(window.location.search);
  if (initialParams.has('q') && searchInput) searchInput.value = initialParams.get('q');
  if (initialParams.has('subject_id') && subjectSelect) subjectSelect.value = initialParams.get('subject_id');
  if (initialParams.has('date') && dateInput) dateInput.value = initialParams.get('date');

  searchInput?.addEventListener('input', () => renderList());
  subjectSelect?.addEventListener('change', () => renderList());
  dateInput?.addEventListener('change', () => renderList());
  resetBtn?.addEventListener('click', resetFilters);

  // Hook into window.updateStudentLessons
  window.updateStudentLessons = function (assignments) {
    allAssignments = assignments || [];
    const unlockView = document.querySelector('#student_unlock_view');
    if (unlockView) unlockView.hidden = true;
    workspaceView.hidden = false;
    renderList();
  };

  // Check if session is already unlocked on initial load
  fetch('/student/assignments', {
    headers: { Accept: 'application/json' },
  })
    .then((res) => {
      if (res.ok) return res.json();
      return null;
    })
    .then((data) => {
      if (data && data.status === 'ok') {
        const groupDisplay = document.querySelector('#group_display');
        const selectedGroup = document.querySelector('#selected_group');

        if (groupDisplay) groupDisplay.textContent = data.group;
        if (selectedGroup) selectedGroup.value = data.group;

        window.updateStudentLessons(data.assignments);
      }
    })
    .catch(() => {});
});
