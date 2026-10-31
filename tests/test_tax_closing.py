"""期末税费：结转应交增值税、计提税金及附加；资产负债表固定资产构成；累计折旧等科目的辅助核算。"""
from app import closing, db, ledger, reports, vehicles
from app.db import AppError
from tests.support import LedgerTestCase, Y, D, C


def bal_of(conn, p, code):
    return sum(v for (c, a), v in ledger.leaf_end_balances(conn, p).items() if c == code)


class VatTransferTest(LedgerTestCase):

    def setUp(self):
        super().setUp()
        self.build_basic()  # 1 月销项税 1300
        # 1 月取得专票：维修费 500，进项税 65
        self.post('2026-01-21', D('veh_repair', Y(500)), D('vat_in', Y(65)), C('cash', Y(565), cf='CF04'))

    def test_status(self):
        st = closing.vat_status(self.conn, 202601)
        self.assertEqual(st['payable'], Y(1235))
        self.assertEqual(st['pending'], Y(1235))
        self.assertEqual(st['retained'], 0)
        self.assertEqual(st['base'], Y(1235))

    def test_transfer(self):
        res = closing.transfer_vat(self.conn, 202601)
        self.assertEqual(res['amount'], Y(1235))
        self.assertVoucherBalanced(res['voucher_id'])
        lines = self.conn.execute('SELECT account_code, debit, credit FROM lines WHERE voucher_id = ? ORDER BY seq',
                                  (res['voucher_id'],)).fetchall()
        self.assertEqual([tuple(r) for r in lines],
                         [(self.code('vat_transfer'), Y(1235), 0), (self.code('vat_unpaid'), 0, Y(1235))])
        kind = self.conn.execute('SELECT kind FROM vouchers WHERE id = ?', (res['voucher_id'],)).fetchone()[0]
        self.assertEqual(kind, '结转增值税')
        # 结转后“应交增值税”合计为零，“未交增值税”贷方 1235
        vat = self.code('vat')
        self.assertEqual(sum(v for (c, a), v in ledger.leaf_end_balances(self.conn, 202601).items()
                             if c.startswith(vat)), 0)
        self.assertEqual(bal_of(self.conn, 202601, self.code('vat_unpaid')), -Y(1235))
        st = closing.vat_status(self.conn, 202601)
        self.assertEqual((st['pending'], st['transferred'], st['base']), (0, Y(1235), Y(1235)))
        with self.assertRaises(AppError):  # 已结清，不能重复结转
            closing.transfer_vat(self.conn, 202601)

    def test_transfer_again_after_more_vouchers(self):
        closing.transfer_vat(self.conn, 202601)
        self.post('2026-01-30', D('cash', Y(1130), cf='CF01'), C('rent_short', Y(1000)), C('vat_out', Y(130)))
        res = closing.transfer_vat(self.conn, 202601)
        self.assertEqual(res['amount'], Y(130))  # 只结转新增部分
        self.assertEqual(closing.vat_status(self.conn, 202601)['base'], Y(1365))

    def test_debit_balance_is_retained(self):
        # 购入车辆进项税 13000，远大于销项：留抵，不结转
        self.post('2026-01-25', D('vat_in', Y(13000)), C('ap', Y(13000), aux=self.add_aux('供应商', 'S01', '车行')))
        st = closing.vat_status(self.conn, 202601)
        self.assertEqual(st['pending'], 0)
        self.assertEqual(st['retained'], Y(13000 + 65 - 1300))
        self.assertEqual(st['base'], 0)
        with self.assertRaises(AppError) as cm:
            closing.transfer_vat(self.conn, 202601)
        self.assertIn('留抵', str(cm.exception))

    def test_prepaid_vat_reduces_transfer(self):
        # 本月已预缴（已交税金）235：只结转 1000
        self.post('2026-01-28', D('vat_paid', Y(235)), C('cash', Y(235), cf='CF06'))
        self.assertEqual(closing.transfer_vat(self.conn, 202601)['amount'], Y(1000))

    def test_only_current_period(self):
        with self.assertRaises(AppError):
            closing.transfer_vat(self.conn, 202602)

    def test_close_check_warns(self):
        names = {c['name']: c for c in closing.close_checks(self.conn, 202601)}
        self.assertFalse(names['结转增值税']['ok'])
        self.assertTrue(names['结转增值税']['warn_only'])
        closing.transfer_vat(self.conn, 202601)
        names = {c['name']: c for c in closing.close_checks(self.conn, 202601)}
        self.assertTrue(names['结转增值税']['ok'])
        self.assertFalse(names['计提税金及附加']['ok'])


class SurtaxTest(LedgerTestCase):

    def setUp(self):
        super().setUp()
        self.build_basic()
        self.post('2026-01-21', D('veh_repair', Y(500)), D('vat_in', Y(65)), C('cash', Y(565), cf='CF04'))

    def test_requires_vat_transfer_first(self):
        with self.assertRaises(AppError):
            closing.run_surtax(self.conn, 202601)

    def test_default_rates(self):
        closing.transfer_vat(self.conn, 202601)
        pv = closing.surtax_preview(self.conn, 202601)
        self.assertEqual(pv['base'], Y(1235))
        # 1235 × 7% = 86.45；× 3% = 37.05；× 2% = 24.70
        self.assertEqual([i['amount'] for i in pv['items']], [8645, 3705, 2470])
        res = closing.run_surtax(self.conn, 202601)
        self.assertEqual(res['amount'], 8645 + 3705 + 2470)
        self.assertVoucherBalanced(res['voucher_id'])
        lines = self.conn.execute('SELECT account_code, debit, credit FROM lines WHERE voucher_id = ? ORDER BY seq',
                                  (res['voucher_id'],)).fetchall()
        c = self.code
        self.assertEqual([tuple(r) for r in lines], [
            (c('tx_city'), 8645, 0), (c('tx_edu'), 3705, 0), (c('tx_local_edu'), 2470, 0),
            (c('city_tax'), 0, 8645), (c('edu'), 0, 3705), (c('local_edu'), 0, 2470)])
        self.assertTrue(closing.surtax_preview(self.conn, 202601)['done'])
        with self.assertRaises(AppError):
            closing.run_surtax(self.conn, 202601)

    def test_custom_rates_rounding_and_saved(self):
        closing.transfer_vat(self.conn, 202601)
        # 六税两费减半：3.5% / 1.5% / 1%，四舍五入到分
        res = closing.run_surtax(self.conn, 202601, {'city': '3.5', 'edu': '1.5', 'local_edu': '1'})
        self.assertEqual(res['amount'], 4323 + 1853 + 1235)
        self.assertEqual(closing.surtax_rates(self.conn), {'city': '3.5', 'edu': '1.5', 'local_edu': '1'})

    def test_zero_rate_line_skipped(self):
        closing.transfer_vat(self.conn, 202601)
        res = closing.run_surtax(self.conn, 202601, {'city': '7', 'edu': '0', 'local_edu': '0'})
        n = self.conn.execute('SELECT COUNT(*) FROM lines WHERE voucher_id = ?', (res['voucher_id'],)).fetchone()[0]
        self.assertEqual(n, 2)

    def test_bad_rate(self):
        with self.assertRaises(AppError):
            closing.save_surtax_rates(self.conn, {'city': 'abc', 'edu': '3', 'local_edu': '2'})
        with self.assertRaises(AppError):
            closing.save_surtax_rates(self.conn, {'city': '-1', 'edu': '3', 'local_edu': '2'})

    def test_simple_tax_in_base(self):
        # 处置旧车按简易计税 3% 减按 2%：应纳税额 200 计入附加税计税依据
        self.post('2026-01-26', D('cash', Y(10200), cf='CF13'), C('fa_clear', Y(10000)), C('vat_simple', Y(200)))
        closing.transfer_vat(self.conn, 202601)
        self.assertEqual(closing.surtax_preview(self.conn, 202601)['base'], Y(1235 + 200))

    def test_simple_tax_only(self):
        # 一般计税部分留抵，只有简易计税：不需要结转，但要计提附加税
        self.post('2026-01-25', D('vat_in', Y(13000)), C('ap', Y(13000), aux=self.add_aux('供应商', 'S01', '车行')))
        self.post('2026-01-26', D('cash', Y(10200), cf='CF13'), C('fa_clear', Y(10000)), C('vat_simple', Y(200)))
        self.assertEqual(closing.surtax_preview(self.conn, 202601)['base'], Y(200))
        self.assertEqual(closing.run_surtax(self.conn, 202601)['amount'], 1400 + 600 + 400)

    def test_flows_into_income_statement_and_balance_sheet(self):
        closing.transfer_vat(self.conn, 202601)
        res = closing.run_surtax(self.conn, 202601)
        inc = {r['key']: r['month'] for r in reports.income_statement(self.conn, 202601)['rows']}
        self.assertEqual(inc['taxes'], res['amount'])
        closing.carry_forward(self.conn, 202601)
        self.assertEqual(closing.pl_residual(self.conn, 202601), {})
        self.assertEqual(bal_of(self.conn, 202601, self.code('taxes')[:4]), 0)
        bs = reports.balance_sheet(self.conn, 202601)
        self.assertEqual(bs['checks']['end_diff'], 0)
        v = {r['key']: r['end'] for r in bs['rows']}
        self.assertEqual(v['tax'], Y(1235) + res['amount'])  # 未交增值税 + 三项附加
        self.assertAllVouchersBalanced()
        names = {c['name']: c for c in closing.close_checks(self.conn, 202601)}
        self.assertTrue(names['计提税金及附加']['ok'])

    def test_status_payload(self):
        st = closing.closing_status(self.conn, 202601)
        self.assertEqual(st['vat']['pending'], Y(1235))
        self.assertEqual(st['surtax']['base'], Y(1235))
        self.assertFalse(st['surtax']['done'])


class SmallStandardTaxTest(LedgerTestCase):
    STANDARD = 'small'

    def test_vat_and_surtax(self):
        self.build_basic()
        closing.transfer_vat(self.conn, 202601)
        res = closing.run_surtax(self.conn, 202601)
        self.assertEqual(res['base'], Y(1300))
        self.assertEqual(res['amount'], 9100 + 3900 + 2600)
        closing.carry_forward(self.conn, 202601)
        self.assertEqual(reports.balance_sheet(self.conn, 202601)['checks']['end_diff'], 0)


class FixedAssetLinesTest(LedgerTestCase):

    def setUp(self):
        super().setUp()
        self.post('2026-01-05', D('cash', Y(200000), cf='CF21'), C('capital', Y(200000)))
        vid = self.add_vehicle('京A00001', '2025-12-01', Y(100000))
        self.aux = self.conn.execute('SELECT aux_id FROM vehicles WHERE id = ?', (vid,)).fetchone()[0]
        self.post('2026-01-06', D('vehicle_fa', Y(100000), aux=self.aux), C('cash', Y(100000), cf='CF16'))
        closing.run_depreciation(self.conn, 202601)

    def test_enterprise_memo_lines(self):
        # 另一辆旧车转入清理，清理科目借方 3000
        self.post('2026-01-20', D('fa_clear', Y(3000), aux=self.aux), C('cash', Y(3000), cf='CF07'))
        rep = reports.balance_sheet(self.conn, 202601)
        self.assertEqual(rep['checks']['end_diff'], 0)
        rows = {r['key']: r for r in rep['rows']}
        self.assertEqual(rows['fa_cost']['end'], Y(100000))
        self.assertEqual(rows['accum']['end'], 197917)
        self.assertEqual(rows['fa_clear']['end'], Y(3000))
        # 固定资产项目 = 原价 − 累计折旧 ± 固定资产清理
        self.assertEqual(rows['fixed']['end'], Y(100000) - 197917 + Y(3000))
        for k in ('fa_cost', 'accum', 'fa_clear'):
            self.assertTrue(rows[k]['memo'], k)
        self.assertFalse(rows['fixed']['memo'])
        # 附注行不重复计入非流动资产合计
        self.assertEqual(rows['nca_total']['end'], rows['fixed']['end'])
        # 没有减值准备时不显示该行
        self.assertNotIn('fa_impair', rows)

    def test_impairment_line(self):
        self.assertEqual(self.code('fa_impair'), '1603')
        self.post('2026-01-31', D('asset_impair', Y(1000)), C('fa_impair', Y(1000)))
        rows = {r['key']: r['end'] for r in reports.balance_sheet(self.conn, 202601)['rows']}
        self.assertEqual(rows['fa_impair'], Y(1000))
        self.assertEqual(rows['fixed'], Y(100000) - 197917 - Y(1000))

    def test_memo_lines_not_report_line_options(self):
        keys = [x['key'] for x in reports.report_line_options('enterprise')['bs']]
        self.assertIn('fixed', keys)
        self.assertNotIn('fa_cost', keys)
        self.assertNotIn('accum', keys)


class AuxOnDepreciationTest(LedgerTestCase):

    def test_new_company_accounts_have_vehicle_aux(self):
        accts = ledger.account_map(self.conn)
        for key in ('vehicle_depr', 'fa_clear', 'vehicle_fa'):
            self.assertEqual(accts[self.code(key)]['aux_type'], '车辆', key)
        self.assertEqual(accts[self.code('vehicle_depr')]['aux_required'], 0)

    def test_manual_voucher_with_aux_on_accum_depr(self):
        vid = self.add_vehicle('京A00001', '2025-12-01', Y(100000))
        aux = self.conn.execute('SELECT aux_id FROM vehicles WHERE id = ?', (vid,)).fetchone()[0]
        self.post('2026-01-31', D('depr_exp', Y(100)), C('vehicle_depr', Y(100), aux=aux))
        self.assertEqual(ledger.leaf_end_balances(self.conn, 202601)[(self.code('vehicle_depr'), aux)], -Y(100))
        # 不选车辆也可以（可选辅助核算）
        self.post('2026-01-31', D('depr_exp', Y(50)), C('vehicle_depr', Y(50)))

    def test_dispose_draft_uses_vehicle_on_accum_depr(self):
        vid = self.add_vehicle('京A00001', '2025-12-15', Y(100000))
        aux = self.conn.execute('SELECT aux_id FROM vehicles WHERE id = ?', (vid,)).fetchone()[0]
        draft = vehicles.dispose(self.conn, {'id': vid, 'date': '2026-01-20', 'price': 0, 'vat_mode': '不计税'})
        line = [l for l in draft['lines'] if l['account'] == self.code('vehicle_depr')][0]
        self.assertEqual(line['aux_id'], aux)

    def test_migration_for_old_company(self):
        code = self.code('vehicle_depr')
        # 模拟旧版本账套：累计折旧没有辅助核算，也没有调整标记
        self.conn.execute("UPDATE accounts SET aux_type = '' WHERE code IN (?, ?)", (code, self.code('fa_clear')))
        self.conn.execute("DELETE FROM meta WHERE key = 'fix_vehicle_aux'")
        db.migrate(self.conn)
        accts = ledger.account_map(self.conn)
        self.assertEqual(accts[code]['aux_type'], '车辆')
        self.assertEqual(accts[self.code('fa_clear')]['aux_type'], '车辆')
        # 只调整一次：之后用户自己去掉了，不会再被改回来
        self.conn.execute("UPDATE accounts SET aux_type = '' WHERE code = ?", (code,))
        db.migrate(self.conn)
        self.assertEqual(ledger.account_map(self.conn)[code]['aux_type'], '')

    def test_keymap_migration_adds_impairment(self):
        km = db.company_profile(self.conn)['keymap']
        del km['fa_impair']
        import json
        db.set_meta(self.conn, 'keymap', json.dumps(km))
        db.migrate(self.conn)
        self.assertEqual(db.company_profile(self.conn)['keymap']['fa_impair'], '1603')


class NonVehicleIndustryTest(LedgerTestCase):
    INDUSTRY = 'trade'

    def test_no_vehicle_aux_and_vat_works(self):
        accts = ledger.account_map(self.conn)
        self.assertEqual(accts[self.code('fa_clear')]['aux_type'], '')
        self.post('2026-01-05', D('cash', Y(1130), cf='CF01'), C('600101', Y(1000)), C('vat_out', Y(130)))
        self.assertEqual(closing.transfer_vat(self.conn, 202601)['amount'], Y(130))
