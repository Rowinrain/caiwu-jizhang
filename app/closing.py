"""
期末处理：计提折旧 → 结转损益 →（12 月）年末结转 → 结账检查 → 结账 / 反结账。
"""
from .db import AppError, log, now_str, acct_key
from .ledger import (account_map, check_open, closed_periods, current_period, last_day, leaf_end_balances,
                     plabel, start_period, next_period)
from .vehicles import plan_depreciation
from .vouchers import next_number


def _insert_voucher(conn, p, kind, lines, maker='系统'):
    number = next_number(conn, p)
    ts = now_str()
    vid = conn.execute('INSERT INTO vouchers(period, number, date, attachments, kind, maker, created_at, updated_at) '
                       'VALUES (?,?,?,?,?,?,?,?)', (p, number, last_day(p), 0, kind, maker, ts, ts)).lastrowid
    for seq, ln in enumerate(lines, 1):
        conn.execute('INSERT INTO lines(voucher_id, period, seq, summary, account_code, aux_id, debit, credit, cf_item) '
                     'VALUES (?,?,?,?,?,?,?,?,?)', (vid, p, seq, ln['summary'], ln['account'], ln.get('aux_id', 0),
                                                    ln.get('debit', 0), ln.get('credit', 0), ''))
    return vid, number


def _require_current(conn, p):
    p = int(p)
    check_open(conn, p)
    cur = current_period(conn)
    if p != cur:
        raise AppError('期末处理只能在当前会计期间（%s）进行' % plabel(cur))
    return p


# ---------------------------------------------------------------- 计提折旧


def depreciation_done(conn, p):
    return conn.execute('SELECT COUNT(*) FROM depr_records WHERE period = ?', (p,)).fetchone()[0] > 0


def depreciation_preview(conn, p):
    plan = plan_depreciation(conn, int(p))
    return {'count': len(plan), 'amount': sum(a for v, a in plan), 'done': depreciation_done(conn, int(p))}


def run_depreciation(conn, p):
    p = _require_current(conn, p)
    if depreciation_done(conn, p):
        raise AppError('%s 已经计提过折旧。如需重新计提，请先删除本月的折旧凭证。' % plabel(p))
    plan = plan_depreciation(conn, p)
    total = sum(a for v, a in plan)
    if not total:
        raise AppError('本月没有需要计提折旧的车辆')
    accts = account_map(conn)
    exp_code, acc_code = acct_key(conn, 'depr_exp'), acct_key(conn, 'vehicle_depr')
    for c in (exp_code, acc_code):
        if not accts[c]['is_leaf']:
            raise AppError('折旧科目 %s 不是末级科目，请检查会计科目设置' % c)
    s = '计提%s车辆折旧（%d 辆）' % (plabel(p), len(plan))
    vid, number = _insert_voucher(conn, p, '折旧', [
        {'summary': s, 'account': exp_code, 'debit': total},
        {'summary': s, 'account': acc_code, 'credit': total},
    ])
    conn.executemany('INSERT INTO depr_records(period, vehicle_id, amount, voucher_id) VALUES (?,?,?,?)',
                     [(p, v['id'], a, vid) for v, a in plan])
    log(conn, '计提折旧', '%s %d 辆 %.2f 元' % (plabel(p), len(plan), total / 100))
    return {'voucher_id': vid, 'number': number, 'count': len(plan), 'amount': total}


# ---------------------------------------------------------------- 结转损益


def pl_residual(conn, p):
    """p 期末仍有余额的损益类（末级科目, 辅助项）。"""
    accts = account_map(conn)
    bal = leaf_end_balances(conn, p)
    return {k: v for k, v in bal.items() if v and k[0] in accts and accts[k[0]]['category'] == '损益'}


def _carry_groups(res, accts):
    """把待结转的损益余额按一级科目分组：每个一级科目（含其全部明细、辅助项）生成一张凭证。
    顺序：收入类科目在前，成本费用类在后，各自按科目编码排列。"""
    groups = {}
    for (code, aux), v in res.items():
        groups.setdefault(code[:4], []).append((code, aux, v))
    def order(l1):
        a = accts.get(l1, {})
        return (0 if a.get('direction') == '贷' else 1, l1)
    return [(l1, sorted(groups[l1])) for l1 in sorted(groups, key=order)]


def carry_forward(conn, p):
    """结转损益：每个损益类一级科目单独生成一张“结转损益”凭证，例如
    “结转2026年1月主营业务收入”“结转2026年1月管理费用”……各明细科目和辅助项都在这张凭证里。"""
    p = _require_current(conn, p)
    res = pl_residual(conn, p)
    if not res:
        raise AppError('损益类科目没有余额，不需要结转')
    accts = account_map(conn)
    profit = acct_key(conn, 'profit')
    made = []
    total = 0
    for l1, items in _carry_groups(res, accts):
        name = accts[l1]['name'] if l1 in accts else l1
        s = '结转%s%s' % (plabel(p), name)
        lines = []
        # 先借方后贷方：收入类明细在借方转出，费用类明细在贷方转出
        for code, aux, v in sorted(items, key=lambda x: (x[2] > 0, x[0], x[1])):
            if v < 0:
                lines.append({'summary': s, 'account': code, 'aux_id': aux, 'debit': -v})
            else:
                lines.append({'summary': s, 'account': code, 'aux_id': aux, 'credit': v})
        net = sum(v for _, _, v in items)  # >0：费用（转入本年利润借方）
        if net > 0:
            lines.append({'summary': s, 'account': profit, 'debit': net})
        elif net < 0:
            lines.append({'summary': s, 'account': profit, 'credit': -net})
        vid, number = _insert_voucher(conn, p, '结转损益', lines)
        made.append({'voucher_id': vid, 'number': number, 'account': l1, 'name': name, 'amount': -net})
        total += net
    log(conn, '结转损益', '%s 生成 %d 张凭证，净利润 %.2f 元' % (plabel(p), len(made), -total / 100))
    return {'count': len(made), 'vouchers': made, 'first': made[0]['number'], 'last': made[-1]['number'],
            'profit': -total}


def carry_preview(conn, p):
    """将要生成的结转凭证：[(一级科目编码, 名称)]"""
    res = pl_residual(conn, p)
    accts = account_map(conn)
    return [{'code': l1, 'name': accts[l1]['name'] if l1 in accts else l1} for l1, _ in _carry_groups(res, accts)]


# ---------------------------------------------------------------- 年末结转


def year_end_residual(conn, p):
    profit, dist, retained = acct_key(conn, 'profit'), acct_key(conn, 'dist'), acct_key(conn, 'retained')
    bal = leaf_end_balances(conn, p)
    out = {}
    for (code, aux), v in bal.items():
        if v and (code == profit or (code.startswith(dist) and code != retained)):
            out[(code, aux)] = v
    return out


def year_end(conn, p):
    p = _require_current(conn, p)
    if p % 100 != 12:
        raise AppError('年末结转只在 12 月进行')
    if pl_residual(conn, p):
        raise AppError('请先结转本月损益')
    accts = account_map(conn)
    retained, profit = acct_key(conn, 'retained'), acct_key(conn, 'profit')
    res = year_end_residual(conn, p)
    if not res:
        raise AppError('本年利润和利润分配明细科目都没有余额，不需要年末结转')
    lines = []
    total = 0
    for (code, aux), v in sorted(res.items()):
        s = '结转本年利润' if code == profit else '结转利润分配明细'
        if v < 0:
            lines.append({'summary': s, 'account': code, 'aux_id': aux, 'debit': -v})
        else:
            lines.append({'summary': s, 'account': code, 'aux_id': aux, 'credit': v})
        total += v
    if total > 0:
        lines.append({'summary': '转入未分配利润', 'account': retained, 'debit': total})
    elif total < 0:
        lines.append({'summary': '转入未分配利润', 'account': retained, 'credit': -total})
    vid, number = _insert_voucher(conn, p, '年末结转', lines)
    log(conn, '年末结转', plabel(p))
    return {'voucher_id': vid, 'number': number}


# ---------------------------------------------------------------- 结账


def close_checks(conn, p):
    p = int(p)
    checks = []
    cnt = conn.execute('SELECT COUNT(*) FROM vouchers WHERE period = ?', (p,)).fetchone()[0]
    un = conn.execute('SELECT COUNT(*) FROM vouchers WHERE period = ? AND audited = 0', (p,)).fetchone()[0]
    checks.append({'name': '凭证全部审核', 'ok': un == 0,
                   'msg': '本月共 %d 张凭证，全部已审核' % cnt if un == 0 else '还有 %d 张凭证未审核' % un})

    prev = conn.execute('SELECT COUNT(*) FROM vouchers WHERE period < ? AND period NOT IN '
                        '(SELECT period FROM closed_periods)', (p,)).fetchone()[0]
    if prev:
        checks.append({'name': '以前期间', 'ok': False, 'msg': '以前期间有未结账的凭证'})

    from .db import company_profile as _cp
    plan = plan_depreciation(conn, p)
    need = sum(a for v, a in plan)
    done = depreciation_done(conn, p)
    if 'vehicles' in _cp(conn)['features'] or need: checks.append({'name': '计提车辆折旧', 'ok': done or need == 0,
                   'msg': '已计提' if done else ('本月没有需要计提的车辆' if need == 0
                                               else '本月应计提折旧 %.2f 元，尚未计提' % (need / 100))})
    res = pl_residual(conn, p)
    checks.append({'name': '结转损益', 'ok': not res,
                   'msg': '损益类科目余额已全部结转' if not res else '还有 %d 个损益科目有余额，请执行“结转损益”' % len(res)})
    if p % 100 == 12:
        ye = year_end_residual(conn, p)
        checks.append({'name': '年末结转', 'ok': not ye,
                       'msg': '本年利润已转入未分配利润' if not ye else '请执行“年末结转”，把本年利润转入未分配利润'})

    from .vouchers import opening_check
    oc = opening_check(conn)
    checks.append({'name': '期初余额平衡', 'ok': oc['balanced'],
                   'msg': '期初借贷平衡' if oc['balanced'] else '期初余额借贷不平衡，差额 %.2f 元' % (oc['start_diff'] / 100)})
    bal = leaf_end_balances(conn, p)
    diff = sum(bal.values())
    checks.append({'name': '试算平衡', 'ok': diff == 0,
                   'msg': '期末余额借贷平衡' if diff == 0 else '期末借贷不平衡，差额 %.2f 元' % (diff / 100)})
    from .db import company_profile
    if 'vehicles' not in company_profile(conn)['features'] or not conn.execute('SELECT COUNT(*) FROM vehicles').fetchone()[0]:
        return checks
    from .vehicles import reconcile
    rc = reconcile(conn, p)
    ok = rc['card_original'] == rc['book_original'] and rc['card_accum'] == rc['book_accum']
    checks.append({'name': '车辆台账与账面核对', 'ok': ok, 'warn_only': True,
                   'msg': '车辆原值、累计折旧与账面一致' if ok else
                   '台账原值 %.2f / 账面 %.2f；台账累计折旧 %.2f / 账面 %.2f（仅提醒，不影响结账）' % (
                       rc['card_original'] / 100, rc['book_original'] / 100,
                       rc['card_accum'] / 100, rc['book_accum'] / 100)})
    return checks


def closing_status(conn, p=None):
    cur = current_period(conn)
    p = int(p or cur)
    closed = sorted(closed_periods(conn))
    return {
        'period': p,
        'current_period': cur,
        'is_closed': p in closed,
        'last_closed': closed[-1] if closed else None,
        'start_period': start_period(conn),
        'depreciation': depreciation_preview(conn, p),
        'pl_residual_count': len(pl_residual(conn, p)),
        'carry_preview': carry_preview(conn, p),
        'year_end_needed': p % 100 == 12 and bool(year_end_residual(conn, p)),
        'checks': close_checks(conn, p),
    }


def close_period(conn, p):
    p = _require_current(conn, p)
    failed = [c for c in close_checks(conn, p) if not c['ok'] and not c.get('warn_only')]
    if failed:
        raise AppError('不能结账：' + '；'.join(c['msg'] for c in failed))
    conn.execute('INSERT INTO closed_periods(period, closed_at) VALUES (?, ?)', (p, now_str()))
    log(conn, '结账', plabel(p))
    return {'closed': p, 'current_period': next_period(p)}


def reopen_period(conn):
    closed = sorted(closed_periods(conn))
    if not closed:
        raise AppError('还没有已结账的期间')
    p = closed[-1]
    conn.execute('DELETE FROM closed_periods WHERE period = ?', (p,))
    log(conn, '反结账', plabel(p))
    return {'reopened': p}
