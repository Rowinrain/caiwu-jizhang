/* 期末处理：计提折旧、结转损益、年末结转、结账、反结账 */
App.pages.closing = async (page) => {
  const st = await api('/api/closing/status');
  const p = st.period;
  const dep = st.depreciation;
  const isDec = p % 100 === 12;
  const veh = hasFeature('vehicles');
  let n = 0;
  const step = () => ++n;
  const profitName = (App.meta.accounts.find(a => a.code === App.meta.keymap.profit) || {}).name || '本年利润';
  const profitCode = App.meta.keymap.profit || '';
  const failed = st.checks.filter(c => !c.ok && !c.warn_only);
  page.innerHTML = pageHead('期末处理与结账', `当前会计期间：${plabel(p)}。按顺序完成下面的步骤后结账。`) + `
    ${veh ? `<div class="card"><h2>第 ${step()} 步　计提车辆折旧</h2>
      <p class="hint">按车辆台账自动计算：当月购入的车辆下月开始计提，当月处置的车辆当月照提。生成一张“借：主营业务成本—车辆折旧，贷：累计折旧”的凭证。</p>
      ${dep.done ? '<div class="notice ok">本月已计提折旧。如需重新计提，请在凭证查询中删除本月的“折旧”凭证。</div>'
        : dep.count ? `<div class="notice info">本月应计提 <b>${dep.count}</b> 辆车，折旧合计 <b>${fmtN(dep.amount)}</b> 元。</div><button class="btn primary" id="do-dep">计提折旧</button>`
          : '<div class="notice info">本月没有需要计提折旧的车辆。</div>'}
    </div>` : ''}
    <div class="card"><h2>第 ${step()} 步　结转损益</h2>
      <p class="hint">${veh ? '' : '固定资产折旧、费用摊销等请先用凭证录入（可用“凭证模板”）。'}每个收入、成本、费用科目单独生成一张凭证，把余额转入“${profitCode} ${esc(profitName)}”。请在本月所有业务凭证（包括工资、税费等）录完之后再执行。如果结转后又补录了凭证，再执行一次即可，系统只结转剩余的金额。</p>
      ${st.carry_preview.length ? `<div class="notice info">将生成 <b>${st.carry_preview.length}</b> 张结转凭证，每个损益科目一张（含该科目的全部明细）：${st.carry_preview.map(x => esc(x.code + ' ' + x.name)).join('、')}。</div><button class="btn primary" id="do-carry">结转损益</button>`
        : '<div class="notice ok">损益类科目已全部结转（或没有余额）。</div>'}
    </div>
    ${isDec ? `<div class="card"><h2>第 ${step()} 步　年末结转（仅 12 月）</h2>
      <p class="hint">把“本年利润”和“利润分配”各明细科目的余额转入“利润分配—未分配利润”。如需提取盈余公积，请先手工录入“借：利润分配—提取盈余公积，贷：盈余公积”。</p>
      ${st.year_end_needed ? '<button class="btn primary" id="do-ye">年末结转</button>' : '<div class="notice ok">不需要年末结转，或已完成。</div>'}</div>` : ''}
    <div class="card"><h2>第 ${step()} 步　结账检查</h2>
      <ul class="check-list">${st.checks.map(c => `<li><span class="ic ${c.ok ? 'green' : c.warn_only ? '' : 'red'}">${c.ok ? '✓' : c.warn_only ? '!' : '✕'}</span><b style="min-width:150px;display:inline-block">${esc(c.name)}</b><span class="${c.ok ? 'muted' : c.warn_only ? '' : 'red'}">${esc(c.msg)}</span></li>`).join('')}</ul>
      <div class="toolbar" style="margin-top:14px">
        <button class="btn primary" id="do-close" ${failed.length ? 'disabled' : ''}>结账（${plabel(p)}）</button>
        ${st.last_closed ? `<button class="btn danger" id="do-reopen">反结账（${plabel(st.last_closed)}）</button>` : ''}
        <a class="btn" href="#/balance">查看科目余额表</a><a class="btn" href="#/bs">查看报表</a>
      </div>
      <p class="hint">结账后本月凭证不能再修改；发现错误时可以“反结账”，每次只能退回最近结账的一个月。</p>
    </div>`;
  const run = async (url, okMsg) => {
    try { const r = await api(url, { period: p }); toast(okMsg(r), 'ok'); await refreshMeta(); reloadTab(); }
    catch (e) { toast(e.message, 'err'); }
  };
  const b = id => $('#' + id, page);
  if (b('do-dep')) b('do-dep').onclick = () => run('/api/closing/depreciation', r => `已生成折旧凭证 记-${String(r.number).padStart(4, '0')}，${r.count} 辆，${fmtN(r.amount)} 元`);
  if (b('do-carry')) b('do-carry').onclick = () => run('/api/closing/carry', r => `已生成 ${r.count} 张结转凭证（记-${String(r.first).padStart(4, '0')} 至 记-${String(r.last).padStart(4, '0')}），本次结转利润 ${fmtN(r.profit)} 元`);
  if (b('do-ye')) b('do-ye').onclick = () => run('/api/closing/yearend', r => `已生成年末结转凭证 记-${String(r.number).padStart(4, '0')}`);
  b('do-close').onclick = async () => {
    if (!await confirmBox(`确定结账 ${plabel(p)} 吗？结账后该月凭证将被锁定。`, '结账')) return;
    run('/api/closing/close', r => `${plabel(r.closed)} 已结账，当前期间进入 ${plabel(r.current_period)}`);
  };
  if (b('do-reopen')) b('do-reopen').onclick = async () => {
    if (!await confirmBox(`确定反结账 ${plabel(st.last_closed)} 吗？反结账后可以修改该月凭证，修改完需要重新结账。`, '反结账', true)) return;
    run('/api/closing/reopen', r => `${plabel(r.reopened)} 已反结账`);
  };
};
