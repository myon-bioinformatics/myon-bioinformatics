/* Map audit viewports to the shared PSWB scroll component. */
import {bindScroll, refreshScroll} from './pswb-scroll.js';
function mount() {
  document.querySelectorAll('.horizontal-row, .table-wrap').forEach(viewport => {
    viewport.dataset.uiScroll = viewport.classList.contains('table-wrap') ? 'table' : 'row';
  });
  bindScroll(document);
}
new MutationObserver(mutations => {
  if (mutations.some(m => m.type === 'childList')) mount();
}).observe(document.body, {childList: true, subtree: true});
window.AuditScroll = {mount, refresh: refreshScroll};
mount();
