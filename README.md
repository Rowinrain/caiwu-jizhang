# 财务记账软件

本地单机的财务记账软件：录凭证 → 自动出科目余额表、总账、明细账 → 资产负债表、利润表、现金流量表。
数据只保存在你自己的电脑上，不联网、不上传。

- 会计准则：《企业会计准则》或《小企业会计准则》，报表按“会企01/02/03表”或“会小企01/02/03表”格式出具
- 行业：汽车租赁、旅客运输、道路货物运输、居家养老服务、商贸、餐饮、物业管理、现代服务业、通用
- 多账套（每家公司一个）、多窗口标签页、跨年度期间查询、打印与导出 Excel
- 运输、租赁行业：车辆台账、自动计提折旧、单车盈利分析
- 期末：计提折旧、结转损益（每个损益科目一张凭证）、年末结转、结账/反结账

## 下载（免安装，解压即用）

| 电脑 | 下载 |
|---|---|
| Windows 10 / 11 | [caiwu-jizhang-windows10-11.zip](https://github.com/Rowinrain/caiwu-jizhang/releases/latest/download/caiwu-jizhang-windows10-11.zip) |
| Windows 7 | [caiwu-jizhang-windows7.zip](https://github.com/Rowinrain/caiwu-jizhang/releases/latest/download/caiwu-jizhang-windows7.zip) |
| Mac | [caiwu-jizhang-mac.zip](https://github.com/Rowinrain/caiwu-jizhang/releases/latest/download/caiwu-jizhang-mac.zip) |

全部版本见 [Releases](https://github.com/Rowinrain/caiwu-jizhang/releases)。安装和使用方法见 [使用说明.md](使用说明.md)。

## 更新

软件里“设置 → 软件更新 → 检查更新”会自动从本仓库的最新 Release 下载更新包并安装，账套数据不受影响。
连不上 GitHub 时，可以在 Releases 页面下载 `caiwu-jizhang-update.zip`，在同一页面选择它离线更新。

## 开发

后端只用 Python 标准库（兼容 3.8），前端为原生 JavaScript。开发时运行 `python3 start.py`。
代码结构、打包和发布新版本的步骤见 [开发说明.md](开发说明.md)。
