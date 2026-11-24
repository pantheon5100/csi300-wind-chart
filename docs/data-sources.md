# Data-source verification and reviewed corrections

This record describes observed responses, not guaranteed future availability.

## Reviewed correction on 2026-11-24

The live site stopped at 2026-11-20 because its history-preservation check rejected changed Wind values. The November 23 deployment completed successfully in cached mode. A fresh November 24 check reproduced that rejection even though both configured Wind endpoints now contained November 23.

The current public mirror and FundDB raw-close endpoint agree on these observations:

| Date | Previously published | Current mirror | Current FundDB | Change |
| --- | ---: | ---: | ---: | ---: |
| 2026-11-18 | 4973.26 | 4968.32 | 4968.32 | -4.94 |
| 2026-11-19 | 4976.58 | 4963.24 | 4963.24 | -13.34 |
| 2026-11-20 | 4979.57 | 4940.44 | 4940.44 | -39.13 |
| 2026-11-23 | Not yet published | 4950.07 | 4950.07 | New observation |

FundDB's detail and history responses both identify **885003.WI**, the **偏债混合型基金指数**, the **close** metric, update date **2026-11-23**, and latest raw close **4950.07**. The two public providers agree exactly across the **34 continuous published dates from 2026-09-29 through 2026-11-20**. Only the three rows above differ from the published snapshot. Their latest 60 common dates also agree, but that is not a gap-free 60-observation window: FundDB omits August 27, September 2, and September 28. Older FundDB data has substantial date-alignment differences and is not imported.

The explicit reviewed revision changes only those three dates, recalculates their derived return fields, and preserves an audit record in the dataset. It is idempotent and cannot replace an unexpected third value. It becomes publishable only when a current Wind candidate passes the ordinary identity, recent-overlap, and common-date checks against the corrected history. If only cached Wind data is available, no correction is published. The normal **0.01-point** tolerance remains unchanged, and future unreviewed revisions still stop acceptance.

This is **corroboration by public redistributors**, not direct confirmation from authenticated Wind. Different hosts do not prove independent upstream collection chains.

### Ambiguous upstream provenance

The mirror's global `source` field says `Wind AIFin 不可用，本轮为中证官网降级源（中证官网H20269(同口径)）`. H20269 is a CSI Dividend Low Volatility total-return equity index, not 885003.WI. The JSON contains multiple series and does not identify which one uses this substitute. Its revised Wind-labelled returns also do not match H20269's recent daily returns. It would therefore be unjustified to assert that the entire Wind series is H20269, or to accept the declaration as proof of equivalent methodology.

The updater now rejects that ambiguous declaration even if the `885003.WI` key, name, and older anchors match. The unchanged archived fixture retains this declaration as negative regression evidence. Earlier acceptance by the parser did not authenticate the source; the three recent corrections above are supported separately by the exact-code FundDB response.

Sources: [mirror JSON](https://ashare.laoqianriritan.com/data/fund_index.json), [FundDB raw history](https://ai2.jiucaishuo.com/api/index-valuation/history?code=885003.WI&metric=close&years2=10), [FundDB identity/latest-close metadata](https://ai2.jiucaishuo.com/api/index-valuation/detail?code=885003.WI), and [CSI's index fact sheet](https://oss-ch.csindex.com.cn/static/html/csindex/public/uploads/indices/detail/files/zh_CN/H30269factsheet.pdf).

### Scheduling observed during this incident

The requested schedule remains **00:00 UTC / 08:00 Beijing / 09:00 Korea**. The November 21–23 scheduled workflows actually started at 04:20, 03:34, and 04:06 UTC respectively. At 03:36 UTC on November 24, no November 24 run had appeared. GitHub documents that scheduled events can be delayed or dropped under load; this is separate from the data-validation rejection. Successful deployment means the page was published, not necessarily that new observations were acquired. The workflow summary now distinguishes dataset generation time from the latest source-check time and includes exact rejection messages.

## Initial verification on 2026-11-20

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

## Historical discrepancies observed on 2026-11-20

The CSI backup overlaps the current published stock history on 5,313 dates. Its maximum historical difference is 0.14 point; four older dates exceed 0.01 point (2006-04-21, 2006-04-24, 2010-01-21, 2011-04-07). The most recent 60 dates agree within 0.01 point. Older backup values are never imported into published history.

The FundDB raw-close history overlaps on 2,414 dates. Its latest 60 *common* dates agree exactly, but that does not mean its latest 60 *expected* dates are complete. The expected 2026-08-14–11-13 window is missing 2026-08-27, 2026-09-02 and 2026-09-28. Starting at the end and stopping at the first missing date leaves **29 consecutive expected observations**, all exact matches. The updater uses that continuous tail, not a date intersection that conceals the missing dates.

Older FundDB history contains 19 material date-alignment discrepancies in May–June 2026, an old Sunday label, and a few smaller precision differences. It is therefore unsuitable for silently replacing the full published history. The adapter retains raw source values, while the updater checks a continuous recent tail of at least 20 and at most 60 expected observations, requires maximum error no greater than 0.01 point, and only appends new dates. Any newly appended weekend, future date or mismatched common-date sequence is rejected.

On the initial verification date both backups were behind the primaries. They are configured alternatives, not evidence that fresh data is always obtainable. An older backup is reported as older; it cannot roll the page backward. If all usable providers fail, the last accepted cached data remains visible with a source warning and its actual data cutoff.

## Other candidates investigated

- <https://ashare.laoqianriritan.com/data/wide_base.json> contains H00300 normalized to 100 on 2005-01-04. It shares the Wind primary host and loses precision when converted back to raw levels, so it was not used as an independent raw-level backup.
- Older FundDB POST routes provide rounded cumulative percentage returns. Reconstructed levels may differ by about 0.1 point. They were superseded by the public raw-close GET endpoint and are not production fallbacks.
- Tested Eastmoney H00300 candidates returned no matching data; tested Sina candidates returned HTTP 403; tested CSI static XLS/export routes returned HTTP 404. None was represented as a working alternative, and no price index, net-return index, ETF or other fund index was substituted.
- [Wind's official index service page](https://www.wind.com.cn/portal/zh/WindIndex/index.html) lists terminal, API and data-service access. A direct authorized Wind connection is a separate option; no working anonymous official complete-history route was established here.

Different public hosts reduce dependence on one host. They do not prove fully independent underlying index calculations or vendor collection chains.
