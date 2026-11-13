(function(root){'use strict';
const states=['調査中','着手中','完了','保留','ブロッカー'];
const esc=x=>String(x??'—').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const api={states,esc};if(typeof module!=='undefined')module.exports=api;root.SharedUI=api;
})(typeof window==='undefined'?globalThis:window);
