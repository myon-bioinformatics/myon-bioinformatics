/* Pure data boundary shared by the Pages UI and Node's built-in regression tests. */
(function(root) {
  'use strict';
  const states = {'': '調査中', todo:'調査中', doing:'着手中', waiting:'ブロッカー', done:'完了', '未着手':'調査中', '対応中':'着手中', '作業中':'着手中', 'ブロック':'ブロッカー', '統合済み':'完了'};
  const ciLabels = {FAILED:'失敗', RUNNING:'実行中', COMPLETE:'成功', INCOMPLETE:'不完全', 'NO CHECKS':'チェックなし', UNKNOWN:'不明', '-':'対象外'};
  const fields = ['state','priority','next','blocker','notes','completed','dependencies','conflicts','order','noted_at','revalidated_head','revalidated_base','revalidated_at','revalidation_notes'];
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
        ci:ciLabels[r.ci] || r.ci || '不明', state:states[r.state] || r.state || '調査中',
        checked:r.checked || o.generated_at || r.checked || '',
        review:canonical?(r.review_complete?r.review:null):(r.review??null),
        failure:checks.filter(c=>['failure','timed_out','action_required','startup_failure'].includes(c.conclusion))
          .map(c=>c.name+(c.url?' '+c.url:'')).join('; ') || r.failure || '',
        dependencies:r.dependencies || r.depends_on || [], observed_dependencies:r.depends_on || [],
        conflicts:r.conflicts || (r.mergeable === false ? 'GitHub: mergeable=false（統合不可）' : ''),
        integration_rank:(o.dependency_plan?.order || []).indexOf(canonical?r.id:r.record_id) + 1,
        integration_waiting:o.dependency_plan?.waiting?.[r.id] || [],
        labels:r.labels || [], next:r.next || '最新headの証跡と受入条件を確認',
        notes:r.notes || '', blocker:r.blocker || ''};
    });
    if (new Set(result.map(r=>r.id)).size !== result.length) throw Error('項目IDが重複しています');
    return result;
  }
  function mergeRecord(observation, drafts) {
    const result = {...observation};
    const draft = drafts[observation.id] || drafts[observation.record_id] || {};
    for (const k of fields) if (Object.hasOwn(draft,k) && (typeof draft[k] === 'string'||(['dependencies','conflicts'].includes(k)&&Array.isArray(draft[k])&&draft[k].every(v=>typeof v==='string')))) result[k] = draft[k];
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
        if (typeof draft[k] !== 'string' && !(['dependencies','conflicts'].includes(k)&&Array.isArray(draft[k])&&draft[k].every(v=>typeof v==='string'))) throw Error('記録値は文字列で指定してください');
        return [k,draft[k]];
      }));
    }
    return result;
  }
  function completedRecords(current, archive, drafts) {
    const ids=new Set(current.map(r=>r.id));
    return Object.values(archive).filter(r=>!ids.has(r.id) && ['完了','統合済み'].includes(mergeRecord(r,drafts).state))
      .map(r=>({...r,archived:true}));
  }
  function priority(r) {
    if (r.ci === '失敗' || r.review > 0 || r.mergeable === false) return 1;
    const labels=(r.labels || []).map(l=>typeof l==='string'?l:l.name);
    return Number(r.priority || (r.mergeable === false || labels.some(l=>/^(bug|type:bug|status:ci-failing)$/i.test(l)) ? 2 : 3));
  }
  function diff(current, previous) {
    const old=new Map(previous.map(r=>[r.id,r])), now=new Map(current.map(r=>[r.id,r]));
    const tracked=['head','head_ref','base_sha','ci','review','base','mergeable','mergeable_state','github_state','title','labels'];
    return {added:current.filter(r=>!old.has(r.id)), gone:previous.filter(r=>!now.has(r.id)),
      changed:current.filter(r=>old.has(r.id) && tracked.some(k=>JSON.stringify(r[k])!==JSON.stringify(old.get(r.id)[k])))};
  }
  const mainFailed = new Set(['failure','timed_out','action_required','startup_failure']);
  function repoOverview(o) {
    const counts = Array.isArray(o?.repository_counts) ? o.repository_counts : [];
    const runs = Array.isArray(o?.main_workflows?.runs) ? o.main_workflows.runs : [];
    const latest = new Map();
    for (const r of runs) {
      if (!r || r.current_main !== true || typeof r.repository !== 'string') continue;
      const old = latest.get(r.repository);
      if (!old || String(r.at || r.checked || '') > String(old.at || old.checked || '')) latest.set(r.repository, r);
    }
    const label = r => mainFailed.has(r.conclusion) ? '失敗' : r.status !== 'completed' || r.conclusion == null ? '実行中'
      : r.conclusion === 'success' ? '成功' : r.conclusion === 'cancelled' ? '取消' : r.conclusion === 'skipped' ? 'スキップ' : '不明';
    const openPullCount = c => {
      if (Number.isInteger(c.pull_requests) && c.pull_requests >= 0) return c.pull_requests;
      const ids = c.open_pr_numbers;
      return c.pulls_complete === true && Array.isArray(ids) &&
        ids.every(n => Number.isInteger(n) && n > 0) && new Set(ids).size === ids.length ? ids.length : null;
    };
    const rows = counts.map(c => {
      const run = latest.get(c.repository) || null;
      return {repository:c.repository, pull_requests:openPullCount(c), issues:c.issues ?? null,
        open_issues_count:c.open_issues_count ?? null, complete:c.complete === true, checked:c.checked || '',
        main_ci:run ? {label:label(run), name:run.name || '', url:run.url || '', at:run.at || run.checked || ''} : null};
    }).sort((a, b) => (b.main_ci?.label === '失敗') - (a.main_ci?.label === '失敗') || a.repository.localeCompare(b.repository));
    const metric = key => {
      const known = rows.filter(r => r[key] != null);
      return {total:known.length ? known.reduce((n, r) => n + r[key], 0) : null, repositories:known.length};
    };
    const pullRequests = metric('pull_requests'), issues = metric('issues');
    return {totals:{repositories:o?.summary?.repositories ?? rows.length,
        pull_requests:pullRequests.total, issues:issues.total,
        main_failed:rows.filter(r => r.main_ci?.label === '失敗').length},
      coverage:{pull_requests:pullRequests.repositories, issues:issues.repositories}, rows};
  }
  function weekBaseline(weeks, at) {
    const date=new Date(new Date(at).getTime()+9*3600000);
    const day=date.getUTCDay() || 7;
    date.setUTCDate(date.getUTCDate()-day+1);date.setUTCHours(0,0,0,0);
    const monday=date.getTime()-9*3600000;
    return weeks.filter(w=>new Date(w.at).getTime()<monday).sort((a,b)=>new Date(a.at)-new Date(b.at)).at(-1);
  }
  const model={normalize,mergeRecord,importBundle,completedRecords,priority,diff,weekBaseline,repoOverview};
  if (typeof module !== 'undefined') module.exports=model;
  else root.AuditModel=model;
})(typeof window === 'undefined'?{}:window);
