/* Pure data boundary shared by the Pages UI and Node's built-in regression tests. */
(function(root) {
  'use strict';
  const states = {'': '未着手', doing: '対応中', waiting: 'ブロック', done: '完了'};
  const ciLabels = {FAILED:'失敗', RUNNING:'実行中', COMPLETE:'成功', INCOMPLETE:'不完全', 'NO CHECKS':'チェックなし', UNKNOWN:'不明', '-':'対象外'};
  const fields = ['state','priority','next','blocker','notes','completed'];
  function safeURL(value) {
    if (typeof value !== 'string' || !/^https:\/\/github\.com\/myon-bioinformatics\/[A-Za-z0-9_.-]+\/(pull|issues)\/[1-9][0-9]*$/.test(value)) throw Error('対象外のGitHub URL');
    return value;
  }
  function normalize(o) {
    let rows = Array.isArray(o) ? o : o.records || o.items;
    if (!Array.isArray(rows)) throw Error('監査データの形式が不正です');
    const result = rows.map(r => {
      const url = safeURL(r.url || r.id);
      const canonical = Boolean(r.repository && r.kind);
      const checks = r.checks || [];
      return {...r, id:url, record_id:canonical?r.id:undefined, repo:r.repository || r.repo,
        type:canonical?(r.kind === 'pr'?'PR':'Issue'):r.type,
        head:r.head_sha || r.head || '', base:r.base_ref || r.base || '',
        ci:ciLabels[r.ci] || r.ci || '不明', state:states[r.state] || r.state || '未着手',
        checked:r.checked || o.generated_at || r.checked || '',
        review:canonical?(r.review_complete?r.review:null):(r.review??null),
        failure:checks.filter(c=>['failure','timed_out','action_required','startup_failure'].includes(c.conclusion))
          .map(c=>c.name+(c.url?' '+c.url:'')).join('; ') || r.failure || '',
        labels:r.labels || [], next:r.next || '最新headの証跡と受入条件を確認',
        notes:r.notes || '', blocker:r.blocker || ''};
    });
    if (new Set(result.map(r=>r.id)).size !== result.length) throw Error('項目IDが重複しています');
    return result;
  }
  function mergeRecord(observation, drafts) {
    const result = {...observation};
    const draft = drafts[observation.id] || drafts[observation.record_id] || {};
    for (const k of fields) if (Object.hasOwn(draft,k) && typeof draft[k] === 'string') result[k] = draft[k];
    if (Object.hasOwn(states,result.state)) result.state=states[result.state];
    return result;
  }
  function importBundle(o) {
    if (!o || typeof o !== 'object' || Array.isArray(o)) throw Error('記録オブジェクトが必要です');
    const result = {};
    if (!o.records || typeof o.records !== 'object' || Array.isArray(o.records)) throw Error('記録形式が不正です');
    for (const [id,draft] of Object.entries(o.records)) {
      if (!/^https:\/\/github\.com\/myon-bioinformatics\//.test(id) && !/^[A-Za-z0-9_.-]+#i?[1-9][0-9]*$/.test(id)) throw Error('記録IDが不正です');
      if (!draft || typeof draft !== 'object' || Array.isArray(draft)) throw Error('記録形式が不正です');
      result[id] = Object.fromEntries(fields.filter(k=>Object.hasOwn(draft,k)).map(k=>{
        if (typeof draft[k] !== 'string') throw Error('記録値は文字列で指定してください');
        return [k,draft[k]];
      }));
    }
    return result;
  }
  function completedRecords(current, archive, drafts) {
    const ids=new Set(current.map(r=>r.id));
    return Object.values(archive).filter(r=>!ids.has(r.id) && mergeRecord(r,drafts).state==='完了')
      .map(r=>({...r,archived:true}));
  }
  const model={normalize,mergeRecord,importBundle,completedRecords};
  if (typeof module !== 'undefined') module.exports=model;
  else root.AuditModel=model;
})(typeof window === 'undefined'?{}:window);
