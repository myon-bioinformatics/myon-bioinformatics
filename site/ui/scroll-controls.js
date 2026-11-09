/* Progressive enhancement of PSWB's ui-shell-scroll. No dependencies or data writes. */
(() => {
  'use strict';
  const mounted = new WeakSet(), states = new Set();
  let identity = 0, scheduled = false;

  function refresh() {
    scheduled = false;
    for (const state of states) {
      if (!state.viewport.isConnected) {
        state.observer?.disconnect();
        states.delete(state);
        continue;
      }
      const {viewport, shell, toolbar, top, spacer, previous, next} = state;
      const maximum = Math.max(0, viewport.scrollWidth - viewport.clientWidth);
      const overflow = maximum > 1;
      shell.classList.toggle('has-overflow', overflow);
      shell.classList.toggle('can-scroll-left', overflow && viewport.scrollLeft > 1);
      shell.classList.toggle('can-scroll-right', overflow && viewport.scrollLeft < maximum - 1);
      previous.disabled = !overflow || viewport.scrollLeft <= 1;
      next.disabled = !overflow || viewport.scrollLeft >= maximum - 1;
      if (previous.hidden !== !overflow) previous.hidden = !overflow;
      if (next.hidden !== !overflow) next.hidden = !overflow;
      if (toolbar) {
        if (toolbar.hidden !== !overflow) toolbar.hidden = !overflow;
        // Both scrollbars must have the same range, even though the toolbar has buttons.
        spacer.style.width = `${maximum + top.clientWidth}px`;
        if (Math.abs(top.scrollLeft - viewport.scrollLeft) > 1) top.scrollLeft = viewport.scrollLeft;
      }
    }
  }

  function schedule() {
    if (!scheduled) {
      scheduled = true;
      requestAnimationFrame(refresh);
    }
  }

  function enhance(viewport, table) {
    if (mounted.has(viewport)) return;
    mounted.add(viewport);
    const label = viewport.dataset.scrollLabel || (table ? '表' : '横スクロール');
    const shell = document.createElement('div');
    shell.className = table ? 'scroll-shell table-shell' : 'scroll-shell row-shell';
    viewport.before(shell);
    shell.append(viewport);
    viewport.classList.add('ui-shell-scroll');
    if (!viewport.id) viewport.id = `audit-scroll-${++identity}`;
    if (!viewport.hasAttribute('tabindex')) viewport.tabIndex = 0;
    if (!viewport.hasAttribute('aria-label')) viewport.setAttribute('aria-label', `${label}（横スクロール）`);
    if (table) viewport.setAttribute('role', 'region');
    const state = {viewport, shell};
    const move = direction => viewport.scrollBy({left: direction * Math.max(120, viewport.clientWidth * .7), behavior: 'auto'});
    function arrow(direction) {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'scroll-arrow';
      button.textContent = direction < 0 ? '‹' : '›';
      button.setAttribute('aria-label', `${label}を${direction < 0 ? '左' : '右'}へスクロール`);
      button.setAttribute('aria-controls', viewport.id);
      button.addEventListener('click', () => move(direction));
      return button;
    }
    state.previous = arrow(-1);
    state.next = arrow(1);
    if (table) {
      const toolbar = document.createElement('div');
      toolbar.className = 'table-scroll-toolbar';
      const hint = document.createElement('span');
      hint.className = 'scroll-hint';
      hint.textContent = '表を横に移動';
      const top = document.createElement('div'), spacer = document.createElement('div');
      top.className = 'table-scroll-top';
      top.tabIndex = 0;
      top.setAttribute('role', 'region');
      top.setAttribute('aria-label', '表ヘッダーの横スクロール');
      top.setAttribute('aria-controls', viewport.id);
      spacer.className = 'scroll-spacer';
      top.append(spacer);
      toolbar.append(hint, state.previous, top, state.next);
      shell.prepend(toolbar);
      Object.assign(state, {toolbar, top, spacer});
      top.addEventListener('scroll', () => {
        if (Math.abs(viewport.scrollLeft - top.scrollLeft) > 1) viewport.scrollLeft = top.scrollLeft;
        schedule();
      }, {passive: true});
      bindKeys(top, viewport);
    } else {
      shell.prepend(state.previous);
      shell.append(state.next);
    }
    viewport.addEventListener('scroll', schedule, {passive: true});
    viewport.addEventListener('focusin', event => {
      if (event.target === viewport) return;
      const bounds = viewport.getBoundingClientRect(), target = event.target.getBoundingClientRect();
      if (target.left < bounds.left) viewport.scrollLeft += target.left - bounds.left;
      else if (target.right > bounds.right) viewport.scrollLeft += target.right - bounds.right;
      schedule();
    });
    bindKeys(viewport, viewport);
    if (typeof ResizeObserver !== 'undefined') {
      state.observer = new ResizeObserver(schedule);
      state.observer.observe(viewport);
      for (const child of viewport.children) state.observer.observe(child);
    }
    states.add(state);
  }

  function bindKeys(control, viewport) {
    control.addEventListener('keydown', event => {
      // Preserve arrow/Home/End behavior of text fields, selects and other children.
      if (event.target !== control || event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) return;
      if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') {
        viewport.scrollLeft += event.key === 'ArrowLeft' ? -100 : 100;
      } else if (event.key === 'Home') viewport.scrollLeft = 0;
      else if (event.key === 'End') viewport.scrollLeft = viewport.scrollWidth;
      else return;
      event.preventDefault();
      if (control !== viewport) control.scrollLeft = viewport.scrollLeft;
      schedule();
    });
  }

  function mount() {
    document.querySelectorAll('.horizontal-row, .table-wrap').forEach(viewport => enhance(viewport, viewport.classList.contains('table-wrap')));
    schedule();
  }
  const observer = new MutationObserver(mutations => {
    if (mutations.some(mutation => mutation.type === 'childList')) mount();
    else schedule();
  });
  observer.observe(document.body, {childList: true, subtree: true, attributes: true, attributeFilter: ['open', 'hidden']});
  window.addEventListener('resize', schedule, {passive: true});
  document.addEventListener('toggle', schedule, true);
  document.addEventListener('load', schedule, true);
  window.AuditScroll = {mount, refresh};
  mount();
})();
