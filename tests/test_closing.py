"""期末处理：结转损益、年末结转、结账检查、结账 / 反结账。"""
from app import closing, ledger, vouchers
from app.db import AppError
from tests.support import LedgerTestCase, Y, D, C


class CarryForwardTest(LedgerTestCase):

    def setUp(self):
        super().setUp()
        self.build_basic()

    def carry_vouchers(self):
        return self.conn.execute("SELECT id FROM vouchers WHERE kind = '结转损益' ORDER BY number").fetchall()

    def test_clears_pl_and_credits_profit(self):
        res = closing.carry_forward(self.conn, 202601)
        self.assertEqual(res['profit'], Y(7050))
        self.assertEqual(closing.pl_residual(self.conn, 202601), {})
        bal = ledger.leaf_end_balances(self.conn, 202601)
        self.assertEqual(bal[('4103', 0)], -Y(7050))  # 本年利润贷方 7050
        self.assertAllVouchersBalanced()
        self.assertTrue(ledger.balance_table(self.conn, 202601, 202601)['balanced'])

    def test_one_voucher_per_level1_account(self):
        res = closing.carry_forward(self.conn, 202601)
        # 1 月有余额的损益一级科目：主营业务收入、主营业务成本、管理费用、财务费用
        self.assertEqual(res['count'], 4)
        self.assertEqual([v['account'] for v in res['vouchers']], ['6001', '6401', '6602', '6603'])
        self.assertEqual(len(self.carry_vouchers()), 4)
        amounts = {v['account']: v['amount'] for v in res['vouchers']}
        self.assertEqual(amounts, {'6001': Y(10000), '6401': -Y(1000), '6602': -Y(2000), '6603': Y(50)})
        # 凭证号接着本月已有的凭证往后编
        self.assertEqual((res['first'], res['last']), (6, 9))

    def test_preview_matches(self):
        preview = closing.carry_preview(self.conn, 202601)
        res = closing.carry_forward(self.conn, 202601)
        self.assertEqual([p['code'] for p in preview], [v['account'] for v in res['vouchers']])

    def test_aux_details_are_carried_individually(self):
        """有辅助核算（车辆）的收入明细逐项结转，结转后每辆车都没有余额。"""
        v1 = self.add_vehicle('京A00001', '2025-12-01', Y(100000))
        v2 = self.add_vehicle('京A00002', '2025-12-01', Y(100000))
        aux = dict(self.conn.execute('SELECT id, aux_id FROM vehicles').fetchall())
        self.post('2026-01-25', D('cash', Y(300), cf='CF01'), C('rent_short', Y(100), aux=aux[v1]),
                  C('rent_short', Y(200), aux=aux[v2]))
        closing.carry_forward(self.conn, 202601)
        bal = ledger.leaf_end_balances(self.conn, 202601)
        self.assertEqual(bal.get(('600101', aux[v1])), 0)
        self.assertEqual(bal.get(('600101', aux[v2])), 0)
        self.assertEqual(bal[('4103', 0)], -Y(7350))

    def test_nothing_to_carry(self):
        closing.carry_forward(self.conn, 202601)
        with self.assertRaises(AppError):
            closing.carry_forward(self.conn, 202601)

    def test_only_current_period(self):
        with self.assertRaises(AppError):
            closing.carry_forward(self.conn, 202602)  # 1 月还没结账

    def test_second_month_after_close(self):
        closing.carry_forward(self.conn, 202601)
        self.audit_period(202601)
        closing.close_period(self.conn, 202601)
        res = closing.carry_forward(self.conn, 202602)
        self.assertEqual(res['profit'], Y(2000))
        bal = ledger.leaf_end_balances(self.conn, 202602)
        self.assertEqual(bal[('4103', 0)], -Y(9050))

    def test_loss(self):
        """亏损时本年利润在借方。"""
        self.post('2026-01-28', D('office', Y(10000)), C('cash', Y(10000), cf='CF07'))
        res = closing.carry_forward(self.conn, 202601)
        self.assertEqual(res['profit'], -Y(2950))
        self.assertEqual(ledger.leaf_end_balances(self.conn, 202601)[('4103', 0)], Y(2950))


class YearEndTest(LedgerTestCase):

    START = (2026, 12)

    def setUp(self):
        super().setUp()
        self.post('2026-12-05', D('cash', Y(100000), cf='CF21'), C('capital', Y(100000)))
        self.post('2026-12-10', D('cash', Y(5000), cf='CF01'), C('rent_short', Y(5000)))
        self.post('2026-12-20', D('office', Y(1200)), C('cash', Y(1200), cf='CF07'))

    def test_requires_carry_forward_first(self):
        with self.assertRaises(AppError):
            closing.year_end(self.conn, 202612)

    def test_moves_profit_to_retained_earnings(self):
        closing.carry_forward(self.conn, 202612)
        closing.year_end(self.conn, 202612)
        bal = ledger.leaf_end_balances(self.conn, 202612)
        self.assertEqual(bal.get(('4103', 0), 0), 0)
        self.assertEqual(bal[('410499', 0)], -Y(3800))
        self.assertEqual(closing.year_end_residual(self.conn, 202612), {})
        self.assertAllVouchersBalanced()

    def test_dividend_detail_also_transferred(self):
        closing.carry_forward(self.conn, 202612)
        self.post('2026-12-31', D('div_dist', Y(1000)), C('div_pay', Y(1000)))
        closing.year_end(self.conn, 202612)
        bal = ledger.leaf_end_balances(self.conn, 202612)
        self.assertEqual(bal.get(('410402', 0), 0), 0)
        self.assertEqual(bal[('410499', 0)], -Y(2800))

    def test_full_close_and_next_year(self):
        closing.carry_forward(self.conn, 202612)
        closing.year_end(self.conn, 202612)
        self.audit_period(202612)
        closing.close_period(self.conn, 202612)
        self.assertEqual(ledger.current_period(self.conn), 202701)
        r = {x['code']: x for x in ledger.balance_table(self.conn, 202701, 202701, show_zero=True)['rows']}
        self.assertEqual(r['4104']['begin_c'], Y(3800))
        self.assertEqual(r['4103']['begin_c'], 0)


class YearEndOnlyInDecemberTest(LedgerTestCase):

    def test_not_december(self):
        self.post('2026-01-10', D('cash', Y(100), cf='CF01'), C('rent_short', Y(100)))
        closing.carry_forward(self.conn, 202601)
        with self.assertRaises(AppError):
            closing.year_end(self.conn, 202601)


class ClosePeriodTest(LedgerTestCase):

    def setUp(self):
        super().setUp()
        self.build_basic()

    def failed(self, p):
        """会阻止结账的检查项（“仅提醒”的不算）。"""
        return {c['name'] for c in closing.close_checks(self.conn, p) if not c['ok'] and not c.get('warn_only')}

    def test_checks_block_closing(self):
        self.assertEqual(self.failed(202601), {'凭证全部审核', '结转损益'})
        with self.assertRaises(AppError):
            closing.close_period(self.conn, 202601)
        closing.carry_forward(self.conn, 202601)
        self.audit_period(202601)
        self.assertEqual(self.failed(202601), set())

    def test_close_and_reopen(self):
        closing.carry_forward(self.conn, 202601)
        self.audit_period(202601)
        res = closing.close_period(self.conn, 202601)
        self.assertEqual(res['current_period'], 202602)
        self.assertEqual(ledger.current_period(self.conn), 202602)
        with self.assertRaises(AppError):  # 已结账期间不能再录凭证
            self.post('2026-01-31', D('cash', Y(1), cf='CF01'), C('rent_short', Y(1)))
        closing.reopen_period(self.conn)
        self.assertEqual(ledger.current_period(self.conn), 202601)
        self.post('2026-01-31', D('cash', Y(1), cf='CF01'), C('rent_short', Y(1)))

    def test_unbalanced_opening_blocks_closing(self):
        vouchers.save_opening(self.conn, [{'account_code': '1001', 'year_begin': Y(100)}])
        closing.carry_forward(self.conn, 202601)
        self.audit_period(202601)
        self.assertIn('期初余额平衡', self.failed(202601))
        self.assertIn('试算平衡', self.failed(202601))
