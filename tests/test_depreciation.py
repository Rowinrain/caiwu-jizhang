"""车辆折旧（平均年限法）：月折旧额、起止月份、尾差、计提凭证、处置当月照提。"""
from app import closing, ledger, vehicles
from app.db import AppError
from tests.support import LedgerTestCase, Y, D, C


class MonthlyAmountTest(LedgerTestCase):

    def test_formula(self):
        v = {'original': Y(100000), 'salvage_bp': 500, 'life_months': 48}
        # 100000 × (1 − 5%) ÷ 48 = 1979.1666… → 1979.17
        self.assertEqual(vehicles.monthly_amount(v), 197917)
        self.assertEqual(vehicles.depreciable_limit(v), Y(95000))

    def test_zero_salvage(self):
        v = {'original': Y(120000), 'salvage_bp': 0, 'life_months': 60}
        self.assertEqual(vehicles.monthly_amount(v), Y(2000))
        self.assertEqual(vehicles.depreciable_limit(v), Y(120000))

    def test_saved_vehicle_fields(self):
        vid = self.add_vehicle('京a00001', '2025-12-01', Y(100000), salvage_rate=5, life_months=48)
        v = [x for x in vehicles.list_vehicles(self.conn)['vehicles'] if x['id'] == vid][0]
        self.assertEqual(v['plate'], '京A00001')
        self.assertEqual(v['salvage_bp'], 500)
        self.assertEqual(v['monthly'], 197917)


class PlanTest(LedgerTestCase):

    def setUp(self):
        super().setUp()
        self.a = self.add_vehicle('京A00001', '2025-12-15', Y(100000))                 # 上月购入：本月起提
        self.b = self.add_vehicle('京A00002', '2026-01-10', Y(120000), 0, 60)          # 本月购入：下月起提
        # 启用前已提 45000，限额 45600，本月只补提尾差 600
        self.c = self.add_vehicle('京A00003', '2022-01-01', Y(48000), opening_accum=Y(45000))
        self.d = self.add_vehicle('京A00004', '2021-01-01', Y(48000), opening_accum=Y(45600))  # 已提足

    def plan(self, p):
        return {v['id']: amt for v, amt in vehicles.plan_depreciation(self.conn, p)}

    def test_first_month(self):
        self.assertEqual(self.plan(202601), {self.a: 197917, self.c: Y(600)})

    def test_not_before_start_period(self):
        self.assertEqual(self.plan(202512), {})

    def test_run_depreciation(self):
        res = closing.run_depreciation(self.conn, 202601)
        self.assertEqual(res['count'], 2)
        self.assertEqual(res['amount'], 197917 + Y(600))
        lines = self.conn.execute('SELECT account_code, debit, credit FROM lines WHERE voucher_id = ? ORDER BY seq',
                                  (res['voucher_id'],)).fetchall()
        self.assertEqual([tuple(r) for r in lines],
                         [('640101', res['amount'], 0), ('160201', 0, res['amount'])])
        self.assertEqual(self.conn.execute('SELECT kind FROM vouchers WHERE id = ?', (res['voucher_id'],)).fetchone()[0],
                         '折旧')
        recs = dict(self.conn.execute('SELECT vehicle_id, amount FROM depr_records WHERE period = 202601').fetchall())
        self.assertEqual(recs, {self.a: 197917, self.c: Y(600)})
        self.assertTrue(closing.depreciation_preview(self.conn, 202601)['done'])
        self.assertEqual(ledger.leaf_end_balances(self.conn, 202601)[('160201', 0)], -(197917 + Y(600)))

    def test_cannot_run_twice(self):
        closing.run_depreciation(self.conn, 202601)
        with self.assertRaises(AppError):
            closing.run_depreciation(self.conn, 202601)

    def test_next_month_uses_recorded_amounts(self):
        closing.run_depreciation(self.conn, 202601)
        # 2 月：A 继续提，B 开始提，C 已提足
        self.assertEqual(self.plan(202602), {self.a: 197917, self.b: Y(2000)})

    def test_last_month_rounding_difference(self):
        """最后一个月补足尾差：累计折旧正好等于 原值 − 残值。"""
        v = {'original': Y(100000), 'salvage_bp': 500, 'life_months': 48}
        limit = vehicles.depreciable_limit(v)
        # 已提 47 个月
        self.conn.execute('UPDATE vehicles SET opening_accum = ? WHERE id = ?', (197917 * 47, self.a))
        amt = self.plan(202601)[self.a]
        self.assertEqual(197917 * 47 + amt, limit)
        self.assertLess(amt, 197917)

    def test_close_check_requires_depreciation(self):
        self.post('2026-01-10', D('cash', Y(100), cf='CF01'), C('rent_short', Y(100)))
        closing.carry_forward(self.conn, 202601)
        self.audit_period(202601)
        names = {c['name'] for c in closing.close_checks(self.conn, 202601) if not c['ok']}
        self.assertIn('计提车辆折旧', names)
        with self.assertRaises(AppError):
            closing.close_period(self.conn, 202601)

    def test_depreciate_close_and_continue(self):
        """计提 → 结转 → 审核 → 结账，下个月接着计提，累计折旧连续。"""
        closing.run_depreciation(self.conn, 202601)
        closing.carry_forward(self.conn, 202601)
        self.audit_period(202601)
        closing.close_period(self.conn, 202601)
        res = closing.run_depreciation(self.conn, 202602)
        self.assertEqual(res['amount'], 197917 + Y(2000))
        self.assertEqual(vehicles.recorded_accum(self.conn, self.a), 197917 * 2)
        self.assertEqual(ledger.leaf_end_balances(self.conn, 202602)[('160201', 0)],
                         -(197917 * 2 + Y(600) + Y(2000)))


class DisposalTest(LedgerTestCase):

    def setUp(self):
        super().setUp()
        self.vid = self.add_vehicle('京A00001', '2025-12-15', Y(100000))

    def test_disposed_month_still_depreciates(self):
        draft = vehicles.dispose(self.conn, {'id': self.vid, 'date': '2026-01-20', 'price': Y(90000),
                                             'cash_account': '1001', 'vat_mode': '不计税'})
        plan = vehicles.plan_depreciation(self.conn, 202601)
        self.assertEqual([(v['id'], a) for v, a in plan], [(self.vid, 197917)])  # 处置当月照提
        self.assertEqual(vehicles.plan_depreciation(self.conn, 202602), [])        # 下月停止
        # 处置凭证草稿：借贷平衡，累计折旧含当月应提
        lines = draft['lines']
        self.assertEqual(sum(l['debit'] for l in lines), sum(l['credit'] for l in lines))
        by_acct = {l['account']: l for l in lines}
        self.assertEqual(by_acct['160201']['debit'], 197917)
        self.assertEqual(by_acct['160101']['credit'], Y(100000))
        gain = Y(90000) - (Y(100000) - 197917)
        self.assertEqual(by_acct['6115']['debit'], -gain)  # 售价低于净值：处置损失

    def test_dispose_twice(self):
        vehicles.dispose(self.conn, {'id': self.vid, 'date': '2026-01-20', 'price': 0, 'vat_mode': '不计税'})
        with self.assertRaises(AppError):
            vehicles.dispose(self.conn, {'id': self.vid, 'date': '2026-01-20', 'price': 0, 'vat_mode': '不计税'})
