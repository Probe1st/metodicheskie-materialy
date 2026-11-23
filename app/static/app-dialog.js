(() => {
  const queue = [];
  let isProcessing = false;

  let currentResolve = null;
  let currentResult = false;
  let previousActiveElement = null;
  let isMouseDownBackdrop = false;
  let listenersAttached = false;

  function getElements() {
    const dialog = document.getElementById('app_dialog');
    if (!dialog) return null;
    return {
      dialog,
      title: document.getElementById('app_dialog_title'),
      message: document.getElementById('app_dialog_message'),
      details: document.getElementById('app_dialog_details'),
      hint: document.getElementById('app_dialog_hint'),
      closeBtn: document.getElementById('app_dialog_close'),
      acceptBtn: document.getElementById('app_dialog_accept'),
      cancelBtn: document.getElementById('app_dialog_cancel'),
    };
  }

  function initListeners(els) {
    if (listenersAttached) return;
    listenersAttached = true;

    // Intercept cancel event (Escape key) and close with false
    els.dialog.addEventListener('cancel', (event) => {
      event.preventDefault();
      currentResult = false;
      els.dialog.close();
    });

    // Detect backdrop click outside dialog bounding box
    els.dialog.addEventListener('mousedown', (event) => {
      if (event.target === els.dialog) {
        const rect = els.dialog.getBoundingClientRect();
        const isInDialog = (
          event.clientX >= rect.left &&
          event.clientX <= rect.right &&
          event.clientY >= rect.top &&
          event.clientY <= rect.bottom
        );
        isMouseDownBackdrop = !isInDialog;
      } else {
        isMouseDownBackdrop = false;
      }
    });

    els.dialog.addEventListener('click', (event) => {
      if (event.target === els.dialog && isMouseDownBackdrop) {
        const rect = els.dialog.getBoundingClientRect();
        const isInDialog = (
          event.clientX >= rect.left &&
          event.clientX <= rect.right &&
          event.clientY >= rect.top &&
          event.clientY <= rect.bottom
        );
        if (!isInDialog) {
          currentResult = false;
          els.dialog.close();
        }
      }
      isMouseDownBackdrop = false;
    });

    // Close button (x)
    els.closeBtn.addEventListener('click', () => {
      currentResult = false;
      els.dialog.close();
    });

    // Cancel button
    els.cancelBtn.addEventListener('click', () => {
      currentResult = false;
      els.dialog.close();
    });

    // Accept button
    els.acceptBtn.addEventListener('click', () => {
      currentResult = true;
      els.dialog.close();
    });

    // Close event: resolve Promise exactly once
    els.dialog.addEventListener('close', () => {
      const resolve = currentResolve;
      const result = currentResult;
      const trigger = previousActiveElement;

      currentResolve = null;
      previousActiveElement = null;

      if (resolve) {
        resolve(result);
      }

      queueMicrotask(() => {
        if (trigger && typeof trigger.focus === 'function' && trigger.isConnected && !trigger.disabled) {
          try {
            trigger.focus();
          } catch {
            // Ignore any focus errors
          }
        }
        processQueue();
      });
    });
  }

  function processQueue() {
    if (queue.length === 0) {
      isProcessing = false;
      return;
    }

    const els = getElements();
    if (!els || !els.dialog || typeof els.dialog.showModal !== 'function') {
      console.error('AppDialog: #app_dialog element is missing or showModal is not supported.');
      while (queue.length > 0) {
        const item = queue.shift();
        item.reject(new Error('AppDialog elements are not available in DOM.'));
      }
      isProcessing = false;
      return;
    }

    initListeners(els);

    isProcessing = true;
    const item = queue.shift();
    currentResult = false;
    previousActiveElement = document.activeElement;

    // Reset returnValue
    els.dialog.returnValue = '';

    const opts = item.options || {};
    const isAlert = item.type === 'alert';

    // Set text contents
    els.title.textContent = opts.title || '';
    els.message.textContent = opts.message || '';

    if (opts.details && String(opts.details).trim()) {
      els.details.textContent = String(opts.details);
      els.details.hidden = false;
    } else {
      els.details.textContent = '';
      els.details.hidden = true;
    }

    if (!isAlert && opts.hint && String(opts.hint).trim()) {
      els.hint.textContent = String(opts.hint);
      els.hint.hidden = false;
    } else {
      els.hint.textContent = '';
      els.hint.hidden = true;
    }

    // Reset buttons
    els.acceptBtn.disabled = false;
    els.acceptBtn.hidden = false;
    els.acceptBtn.textContent = opts.confirmLabel || (isAlert ? 'Понятно' : 'Подтвердить');
    if (opts.danger) {
      els.acceptBtn.className = 'button button--danger';
    } else {
      els.acceptBtn.className = 'button button--primary';
    }

    els.cancelBtn.disabled = false;
    if (isAlert) {
      els.cancelBtn.hidden = true;
      els.cancelBtn.textContent = '';
    } else {
      els.cancelBtn.hidden = false;
      els.cancelBtn.className = 'button button--outline';
      els.cancelBtn.textContent = opts.cancelLabel || 'Отмена';
    }

    els.closeBtn.disabled = false;
    els.closeBtn.hidden = false;

    currentResolve = (result) => {
      if (isAlert) {
        item.resolve();
      } else {
        item.resolve(Boolean(result));
      }
    };

    els.dialog.showModal();

    // Initial focus: cancel in confirm, accept in alert
    if (isAlert) {
      els.acceptBtn.focus();
    } else {
      els.cancelBtn.focus();
    }
  }

  window.AppDialog = {
    confirm(options = {}) {
      return new Promise((resolve, reject) => {
        queue.push({ type: 'confirm', options, resolve, reject });
        if (!isProcessing) {
          processQueue();
        }
      });
    },

    alert(options = {}) {
      return new Promise((resolve, reject) => {
        queue.push({ type: 'alert', options, resolve, reject });
        if (!isProcessing) {
          processQueue();
        }
      });
    },
  };
})();
