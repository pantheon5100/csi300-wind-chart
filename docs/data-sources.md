# Data-source verification — 2026-11-20

This record describes observed responses, not guaranteed future availability.

| Index and provider | Observed history/cutoff | Recent comparison |
| --- | --- | --- |
| H00300, CSI official API | Through 2026-11-19 | Primary raw close series |
| H00300, CharlesPai public research page | 5,313 observations, 2004-12-31–2026-11-18 | Latest 60 overlapping expected dates present; maximum difference 0.01 point |
| 885003.WI, 老钱 JSON mirror | Through 2026-11-19 | Primary raw close series |
| 885003.WI, FundDB raw-close endpoint | 2,415 records, 2016-11-21–2026-11-13 | Latest continuous expected overlap: 29 dates, 2026-09-29–11-13; maximum difference 0 |

## Exact endpoints

- CSI: <https://www.csindex.com.cn/csindex-home/perf/index-perf?indexCode=H00300&startDate=20041231&endDate=20261120>. `data` records identify `indexCode=H00300`, with `tradeDate` and `close`. The end date is generated daily.
- CSI backup: <https://research.charlespai.com/cn/>. Public HTML contains literal JSON in `const DATA`, specifically `DATA.tr.h00300.dates` and `.close`. Its index name is 沪深300全收益. The page credits Tushare Pro H00300.CSI; this project reads the public mirror and has no authenticated Tushare connection. The adapter parses bounded JSON and never executes page JavaScript.
- Wind primary: <https://ashare.laoqianriritan.com/data/fund_index.json>. Exact key `series["885003.WI"]`, with dates and original levels. The provider homepage is <https://ashare.laoqianriritan.com/>.
- Wind backup raw history: <https://ai2.jiucaishuo.com/api/index-valuation/history?code=885003.WI&metric=close&years2=10>.
- Wind backup identity and latest quote: <https://ai2.jiucaishuo.com/api/index-valuation/detail?code=885003.WI>. The public application at <https://funddb.cn/> uses these GET routes. History code/metric, detail code/name, latest date and latest raw close must agree.

The production backup requests succeeded without credentials, login, cookies or access-control workarounds. These are public web endpoints, not contractual service APIs. No public uptime guarantee or open-data redistribution license was established by this check.

## Historical discrepancies and how they are contained

The CSI backup overlaps the current published stock history on 5,313 dates. Its maximum historical difference is 0.14 point; four older dates exceed 0.01 point (2006-04-21, 2006-04-24, 2010-01-21, 2011-04-07). The most recent 60 dates agree within 0.01 point. Older backup values are never imported into published history.

The FundDB raw-close history overlaps on 2,414 dates. Its latest 60 *common* dates agree exactly, but that does not mean its latest 60 *expected* dates are complete. The expected 2026-08-14–11-13 window is missing 2026-08-27, 2026-09-02 and 2026-09-28. Starting at the end and stopping at the first missing date leaves **29 consecutive expected observations**, all exact matches. The updater uses that continuous tail, not a date intersection that conceals the missing dates.

Older FundDB history contains 19 material date-alignment discrepancies in May–June 2026, an old Sunday label, and a few smaller precision differences. It is therefore unsuitable for silently replacing the full published history. The adapter retains raw source values, while the updater checks a continuous recent tail of at least 20 and at most 60 expected observations, requires maximum error no greater than 0.01 point, and only appends new dates. Any newly appended weekend, future date or mismatched common-date sequence is rejected.

On the verification date both backups were behind the primaries. They are configured alternatives, not evidence that fresh data is always obtainable. An older backup is reported as older; it cannot roll the page backward. If all usable providers fail, verified cached data remains visible with a source warning and its actual data cutoff.

## Other candidates investigated

- <https://ashare.laoqianriritan.com/data/wide_base.json> contains H00300 normalized to 100 on 2005-01-04. It shares the Wind primary host and loses precision when converted back to raw levels, so it was not used as an independent raw-level backup.
- Older FundDB POST routes provide rounded cumulative percentage returns. Reconstructed levels may differ by about 0.1 point. They were superseded by the public raw-close GET endpoint and are not production fallbacks.
- Tested Eastmoney H00300 candidates returned no matching data; tested Sina candidates returned HTTP 403; tested CSI static XLS/export routes returned HTTP 404. None was represented as a working alternative, and no price index, net-return index, ETF or other fund index was substituted.
- [Wind's official index service page](https://www.wind.com.cn/portal/zh/WindIndex/index.html) lists terminal, API and data-service access. A direct authorized Wind connection is a separate option; no working anonymous official complete-history route was established here.

Different public hosts reduce dependence on one host. They do not prove fully independent underlying index calculations or vendor collection chains.
