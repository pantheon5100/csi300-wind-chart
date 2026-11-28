# Shanghai / London spot gold / Hang Seng TECH source verification

Reviewed on 2026-11-28. The new absolute-value chart has a separate dataset and updater from the existing CSI300/Wind comparison.

## Scope and initial snapshot

The shared historical starting date is **2014-12-31**, the official HSTECH base date. All available observations are preserved on each series' own calendar; dates need not intersect. No series is rebased, converted into a percentage return, or forward-filled.

| Series | Selected observations through 2026-11-27 | First close | Last close | Unit |
| --- | ---: | ---: | ---: | --- |
| SSE Composite, 000001.SH | 2,893 | 3,234.68 | 3,606.49 | Index points |
| Dukascopy XAU/USD daily BID | 3,704 | 1,183.701 | 3,653.935 | USD per troy ounce |
| Hang Seng TECH price index, HSTECH | 2,931 | 3,000.00 | 3,637.88 | Index points |

These are observations, not three investments' comparable account balances. A numerical crossing between index points and USD per troy ounce has no inherent valuation or return interpretation. Both chart axes deliberately use the same numeric range and tick marks, including on zoom.

## SSE Composite

The source is China Securities Index's exact `indexCode=000001` history:

<https://www.csindex.com.cn/csindex-home/perf/index-perf?indexCode=000001&startDate=20141231&endDate=20261127>

The response identifies 上证指数 / 上证综合指数. The updater verifies identity, dates, positive finite prices, and the relationship between each day's open, high, low and close. The recent 461-date overlap with Tencent's SSE Composite history differed by at most 0.01 point. The [SSE official index overview](https://www.sse.com.cn/market/sseindex/overview/) corroborated the 2026-11-27 value of 3606.49. Tencent is a research cross-check, not a configured automatic backup for this chart.

### Audited non-trading placeholder

The extended CSI response contains 2,894 rows, including one known holiday placeholder on **2018-06-18**. That row has null open/high/low, zero volume and turnover, zero change, and a close of 3021.90 copied from the prior session, 2018-06-15. The [SSE's official 2018 Dragon Boat Festival announcement](https://www.sse.com.cn/disclosure/announcement/general/c/c_20180606_4567900.shtml) confirms that June 16–18 were closed and trading resumed on June 19.

The updater excludes this exact, reviewed placeholder only after checking its date and no-trading signature. It does not treat it as a market observation. Other missing OHLC fields or malformed rows fail validation. The remaining **2,893** rows have internally consistent OHLC values. This exclusion does not change an observed trading close or insert a synthetic value.

## Hang Seng TECH

Eastmoney provides the exact **price index** under `secid=124.HSTECH`, daily candles `klt=101`, unadjusted mode `fqt=0`. The response verifies market 124, code HSTECH, the name 恒生科技指数, and two-decimal precision. `100.HSTECH` is not substituted: it did not return this history.

The initial response returned **2,931** rows, equal to its reported `dktotal`. It includes the actual returned 2014-12-31 observation of 3000, not an inserted baseline. All 1,561 observations from 2020-07-27 onward exactly match the earlier shorter extract. There are **1,370 pre-launch observations** through 2020-07-24.

The [official methodology](https://www.hsi.com.hk/content/dam/wpb/hsil/index/methodologies/en_hk/hstech/IM_hstech.pdf) distinguishes HSTECH from its total-return versions. The [official factsheet](https://www.hsi.com.hk/api/wsit-hsi-ddoc-ea-public-website-proxy/v1/content/gcs/download?resource=/public-website/webapp/factsheets/en-hk/hstech.pdf) establishes the 2014-12-31 base date, base value 3000 and 2020-07-27 launch date, and states that pre-launch information is backtested. The chart labels and dashes that historical portion. Official published levels also corroborated 3754.90 on 2026-10-30 and 3637.88 on 2026-11-27.

The official index page is <https://www.hsi.com.hk/eng/indexes/all-indexes/index-details/?seriesCode=hstech>. The chart also links the actual Eastmoney data URL so its selected instrument and raw candles can be inspected.

## London spot gold: one consistent vendor series

The full chart uses **Dukascopy XAU/USD BID daily UTC candles**. The [provider's gold description](https://www.dukascopy.com/swiss/english/cfd/range-of-markets/gold/) identifies its gold quotation as a rolling-spot/CFD instrument based on interbank XAU/USD spot prices centered in London. The plotted value is the vendor's daily closing **bid**, in USD per troy ounce. It is not a consolidated official gold close, LBMA AM/PM auction fixing, COMEX futures settlement, or ETF price.

The public chart request uses Dukascopy's `freeserv.dukascopy.com/2.0/index.php` endpoint with these exact identity and interval parameters:

| Parameter | Value |
| --- | --- |
| path | chart/json3 |
| instrument | XAU/USD |
| interval | 1DAY |
| offer_side | B (BID) |
| last_update | 1419897600000 (2014-12-30 UTC) |
| time_direction | N (forward) |
| limit | 5000 |

The exact request URL is recorded in the dataset's source metadata. JSONP is parsed as data with a fixed expected wrapper; it is never executed. The numeric response body does not repeat the instrument symbol. Therefore identity depends on the fixed, validated official endpoint and XAU/USD/BID request contract, the provider description and observed historical anchor. This is a provenance limitation, not cryptographic authentication.

The initial response contained 3,705 records including the requested preceding day, and **3,704** observations from 2014-12-31 through 2026-11-27. The response stayed below its 5000-row limit. Every selected timestamp is an exact UTC midnight, all dates are unique and ordered, and all selected OHLC values are finite, positive and internally consistent. Prices have at most three decimal places. The largest gap is three calendar days.

There are **613 Sunday observations and no Saturday observations**. These represent the provider's short Sunday UTC trading sessions. The chart preserves those actual bars; it does not impose the Shanghai/Hong Kong equity calendar on this OTC gold quotation. Equity closing times and the end of a UTC gold day are different instants.

A recent [Investing.com XAU/USD history](https://www.investing.com/currencies/xau-usd-historical-data) cross-check gives 3654.58 on 2026-11-27 versus Dukascopy BID 3653.935, a difference of approximately -0.01765%. The largest absolute percentage difference in the checked recent sample was 0.44124%. The observations are not treated as interchangeable: quote side, provider and daily cutoff can differ. A separate short-window Dukascopy request returned HTTP 429 on the initial attempt and one later retry; requests were then stopped. A separate same-provider window match was therefore not established.

### Why the previous Sina chart was not extended

The earlier standalone 2020-onward chart used Sina's XAU spot/CFD feed. Extending that feed exposed older inconsistent candles. For example, its 2014-12-31 open was 1181.110 while its low was 1182.544; an open below the day's low fails an elementary OHLC consistency check. Other older rows also failed that check. The cause is not established.

The website uses Dukascopy for the **entire** gold history. It does not splice early Dukascopy prices into later Sina prices or claim the two are exact historical equivalents. Their closes can differ materially on individual dates: for 2020-07-27, Dukascopy reports 1964.915 while the earlier Sina extract reports 1942.09. Source labels, bid side and UTC timing are therefore essential to interpreting the new gold line.

## Refresh and failure behavior

- The existing daily GitHub Actions trigger remains 00:00 UTC, **08:00 Beijing / 09:00 Korea**. Pushes and manual workflow dispatch also run it.
- Equity history ends before the current Beijing date; gold history ends before the current UTC date. This avoids publishing an unfinished daily candle. At the scheduled run both cutoffs refer to the previous calendar day.
- Each series is validated independently against the last accepted published snapshot. Every previously published date must still exist. Previously accepted values are retained exactly after verification, with comparison tolerances of 0.01 point for indices and 0.001 USD for gold.
- Unexpected historical revisions, missing dates, older cutoffs, invalid OHLC, response errors and rate limits cause the affected series to retain its last accepted history. Its exact failure and check time are visible. Other valid series can update.
- A valid unchanged history on a holiday is a successful check. Neither the workflow nor the browser invents a fresh observation just because a check ran.
- Each new series currently has one configured live provider. The accepted snapshot provides outage continuity; it is not an independent live alternate source. Replacing a source requires a reviewed identity, quote-side and historical-overlap decision.
- The page's refresh buttons bypass browser caching and read the latest published file. They do not themselves initiate an upstream fetch. The source timestamp and each series' observation date distinguish these operations.

The updated data is deployed as a Pages artifact, not committed daily into the repository. The public runtime file is <https://pantheon5100.github.io/csi300-wind-chart/markets-data.json>.
