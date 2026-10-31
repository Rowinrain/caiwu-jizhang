"""科目余额表：期初 / 本期发生 / 本年累计 / 期末，上级科目汇总，辅助核算明细，跨月跨年，期初余额。"""
from app import ledger
from app.db import AppError
from tests.support import LedgerTestCase, Y, D, C


def rows_by_code(table, with_aux=False):
    return {r['code']: r for r in table['rows'] if with_aux or not r['is_aux']}


class BalanceTableTest(LedgerTestCase):

    def test_empty_company_is_balanced(self):
        t = ledger.balance_table(self.conn, 202601, 202601)
        self.assertTrue(t['balanced'])
        self.assertEqual(t['rows'], [])
        self.assertTrue(all(v == 0 for v in t['totals'].values()))

    def test_single_month(self):
        self.build_basic()
        t = ledger.balance_table(self.conn, 202601, 202601)
        r = rows_by_code(t)
        self.assertTrue(t['balanced'])

        cash = r['1001']
        self.assertEqual((cash['begin_d'], cash['begin_c']), (0, 0))
        self.assertEqual(cash['pd'], Y(1000000 + 11300 + 50))
        self.assertEqual(cash['pc'], Y(2000 + 1000))
        self.assertEqual((cash['end_d'], cash['end_c']), (Y(1008350), 0))

        # 贷方余额显示在贷方栏
        self.assertEqual((r['4001']['end_d'], r['4001']['end_c']), (0, Y(1000000)))
        # 上级科目由末级汇总：应交税费 ← 应交增值税 ← 销项税额
        for code in ('2221', '222101', '22210102'):
            self.assertEqual(r[code]['end_c'], Y(1300), code)
        self.assertEqual(r['6001']['pc'], Y(10000))
        self.assertEqual(r['6603']['end_c'], Y(50))  # 利息收入冲减财务费用，余额在贷方

        # 合计行只加一级科目，不会把上下级重复相加
        tot = t['totals']
        self.assertEqual(tot['pd'], Y(1000000 + 11300 + 2000 + 1000 + 50))
        self.assertEqual(tot['pd'], tot['pc'])
        self.assertEqual(tot['end_d'], Y(1008350 + 2000 + 1000))
        self.assertEqual(tot['end_d'], tot['end_c'])

    def test_next_month_begin_and_ytd(self):
        self.build_basic()
        r = rows_by_code(ledger.balance_table(self.conn, 202602, 202602))
        cash = r['1001']
        self.assertEqual(cash['begin_d'], Y(1008350))           # 期初 = 1 月末
        self.assertEqual((cash['pd'], cash['pc']), (Y(5650), Y(3000)))  # 只含 2 月
        self.assertEqual(cash['yd'], Y(1011350 + 5650))          # 本年累计含 1 月
        self.assertEqual(cash['yc'], Y(3000 + 3000))
        self.assertEqual(cash['end_d'], Y(1011000))
        self.assertEqual(r['6001']['yc'], Y(15000))

    def test_multi_month_range(self):
        self.build_basic()
        t = ledger.balance_table(self.conn, 202601, 202602)
        cash = rows_by_code(t)['1001']
        self.assertEqual(cash['begin_d'], 0)
        self.assertEqual(cash['pd'], Y(1011350 + 5650))
        self.assertEqual(cash['end_d'], Y(1011000))
        self.assertTrue(t['balanced'])

    def test_level_filter_and_zero_rows(self):
        self.build_basic()
        t = ledger.balance_table(self.conn, 202601, 202601, level=1)
        self.assertTrue(all(r['level'] == 1 for r in t['rows']))
        self.assertNotIn('1002', rows_by_code(t))  # 没有发生额的科目默认不显示
        t0 = ledger.balance_table(self.conn, 202601, 202601, level=1, show_zero=True)
        self.assertIn('1002', rows_by_code(t0))
        self.assertEqual(t['totals'], t0['totals'])

    def test_cross_year_resets_ytd(self):
        self.post('2026-12-10', D('cash', Y(500), cf='CF01'), C('rent_short', Y(500)))
        self.post('2027-01-10', D('cash', Y(200), cf='CF01'), C('rent_short', Y(200)))
        cash = rows_by_code(ledger.balance_table(self.conn, 202701, 202701))['1001']
        self.assertEqual(cash['begin_d'], Y(500))
        self.assertEqual(cash['yd'], Y(200))  # 新年度本年累计重新开始
        span = rows_by_code(ledger.balance_table(self.conn, 202612, 202701))['1001']
        self.assertEqual(span['pd'], Y(700))
        self.assertEqual(span['yd'], Y(200))  # 跨年区间的本年累计按结束期间所在年度

    def test_aux_rows(self):
        a = self.add_aux('客户', 'K01', '客户甲')
        b = self.add_aux('客户', 'K02', '客户乙')
        self.post('2026-01-10', D('ar', Y(1000), aux=a), C('rent_short', Y(1000)))
        self.post('2026-01-12', D('cash', Y(300), cf='CF01'), C('ar', Y(300), aux=b))
        t = ledger.balance_table(self.conn, 202601, 202601, with_aux=True)
        aux_rows = {r['aux_id']: r for r in t['rows'] if r['is_aux'] and r['code'] == '1122'}
        self.assertEqual(aux_rows[a]['end_d'], Y(1000))
        self.assertEqual(aux_rows[b]['end_c'], Y(300))
        self.assertEqual(rows_by_code(t)['1122']['end_d'], Y(700))  # 科目行是各辅助项的净额

        ab = {r['aux_id']: r for r in ledger.aux_balance(self.conn, '1122', 202601, 202601)['rows']}
        self.assertEqual(ab[a]['end'], Y(1000))
        self.assertEqual(ab[b]['end'], -Y(300))

    def test_range_validation(self):
        with self.assertRaises(AppError):
            ledger.balance_table(self.conn, 202602, 202601)
        with self.assertRaises(AppError):
            ledger.balance_table(self.conn, 202512, 202512)  # 早于启用期间
        with self.assertRaises(AppError):
            ledger.balance_table(self.conn, 202613, 202613)


class OpeningBalanceTest(LedgerTestCase):
    """年中（7 月）启用：录入年初余额和启用前本年累计。"""

    START = (2026, 7)

    def setUp(self):
        super().setUp()
        # 年初：现金 50 万 / 实收资本 50 万；1~6 月：收入 8 万、管理费用 1 万，利润 7 万已结转到本年利润
        self.set_opening([
            ('cash', Y(500000), Y(100000), Y(30000)),
            ('capital', -Y(500000), 0, 0),
            ('profit', 0, Y(10000), Y(80000)),          # 1~6 月已逐月结转
            ('rent_short', 0, 0, Y(80000)),            # 损益类只填启用前实际发生额
            ('office', 0, Y(10000), 0),
        ])

    def test_opening_check(self):
        from app import vouchers
        chk = vouchers.opening_check(self.conn)
        self.assertTrue(chk['balanced'], chk)

    def test_balance_at_start(self):
        self.post('2026-07-10', D('cash', Y(1130), cf='CF01'), C('rent_short', Y(1000)), C('vat_out', Y(130)))
        t = ledger.balance_table(self.conn, 202607, 202607)
        self.assertTrue(t['balanced'])
        r = rows_by_code(t)
        self.assertEqual(r['1001']['begin_d'], Y(570000))        # 年初 + 启用前借 − 启用前贷
        self.assertEqual(r['1001']['yd'], Y(100000 + 1130))       # 本年累计含启用前
        self.assertEqual(r['4103']['begin_c'], Y(70000))
        self.assertEqual(r['1001']['end_d'], Y(571130))
        # 损益类在启用时点已经结转，期初余额为 0
        self.assertEqual(r['6001']['begin_c'], 0)
        self.assertEqual(r['6001']['end_c'], Y(1000))

    def test_query_year_begin_balance(self):
        self.assertEqual(ledger.leaf_year_begin_balances(self.conn, 2026)[('1001', 0)], Y(500000))
        self.assertEqual(ledger.leaf_begin_balances(self.conn, 202607)[('1001', 0)], Y(570000))
        self.assertEqual(ledger.leaf_end_balances(self.conn, 202607)[('1001', 0)], Y(570000))
