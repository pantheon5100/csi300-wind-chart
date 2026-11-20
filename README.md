# 沪深300全收益 vs 万得偏债混合型基金

A simple GitHub Pages site for **沪深300全收益指数 H00300** and **万得偏债混合型基金指数 885003.WI**. It displays three charts, crossover markers with hover details, source health, and explicit source links. The page uses native SVG with no framework, CDN, or API key.

Live page: <https://pantheon5100.github.io/csi300-wind-chart/>.

## Three charts

| Chart | Y-axis calculation |
| --- | --- |
| Raw index levels | Each source's published index level in points. |
| Cumulative returns | (index level / index level on 2004-12-31 − 1) × 100, in percent. |
| Index-level difference (deviation) | H00300 raw index level − 885003.WI raw index level, in **index points**. |

The cumulative-return baseline is fixed at **2004-12-31 = 0%**. H00300 uses **1000.00** points; Wind uses **approximately 989.30** points. This normalization applies to the cumulative-return chart.

The deviation chart uses the same raw values shown in the raw-index-level chart and keeps the sign of their difference. A positive value means the H00300 level is higher; a negative value means the Wind level is higher; zero means the two published index levels are numerically equal. For example, on **2026-11-19**, the deviation is **6001.04 − 4976.58 = +1024.46 points**.

**Raw index levels use each provider's original scale.** They are index points, not currency amounts. Equal point levels do not imply equal investment gains, and the larger raw level does not establish better cumulative performance. Use the cumulative-return chart to compare performance from the common starting date. The deviation at the reconstructed first row, 2004-12-31, is approximately **1000.00 − 989.30 = +10.70 points**; it is not reset to zero.

## Crossovers and intervals

The deviation chart marks a raw-level crossover on the **first observed daily close on the opposite side of zero**. Hover over a marked observation to see its date, which index now has the higher raw level, calendar days since the previous crossover, elapsed common-data observations, and the actual point difference at confirmation. The chart's keyboard controls can also inspect these observations.

- The first raw-level difference establishes the initial side. Because the initial difference is positive, its reversal to **−2.06 points on 2005-01-04** is counted. An initial equality itself is not a crossover.
- A touch of zero followed by a return to the same side is not counted. A zero plateau counts once, when the next nonzero close confirms a reversal.
- Daily closes locate the confirmation date; they do not establish an exact intraday crossing time. A marker therefore sits on the actual close, not an invented zero observation.
- Calendar days are ordinary date differences. Common-data intervals count observations after the previous event through the current event. The first event has no previous interval.
- These markers track the ordering of the two original index levels. Their locations depend on the indices' original bases; they do not indicate equal cumulative returns, fair value, equilibrium, or a validated buy/sell rule.

On the dataset through **2026-11-19**, there are **26** raw-level crossings, most recently **2014-07-24**. That crossing occurred **94 calendar days / 65 common-data observations** after the previous one on **2014-04-21**. Markers and hover intervals are calculated from the downloaded data on every page load, so future crossings appear automatically.

## Daily refresh

- **Source data:** GitHub Actions checks both indices' primary and backup providers once daily at **09:00 Korea time / 08:00 Beijing time**. A push to the default branch or a manual workflow run also refreshes and deploys the site.
- **Webpage:** loads the published dataset when opened, checks again every **24 hours** while open, and offers a manual check button. These browser checks read the site's data file; they do not trigger a source fetch or a GitHub workflow.
- **Failure handling:** verified backup providers can append data when a primary is unavailable or slower. If no valid aligned pair is available, the workflow publishes the **last verified dataset** with a visible cached-data notice and a source-check timestamp. It never substitutes a different index or moves the data cutoff backward. An invalid previous snapshot still stops deployment. A failed browser check preserves the chart already displayed.
- **Dates:** the latest common source date is shown on the page. Weekends and holidays do not create new observations. A notice appears when the data date is more than five calendar days old, which may also occur during long market holidays.

The schedule in .github/workflows/update-and-deploy.yml is UTC:

~~~yaml
schedule:
  - cron: '0 0 * * *'
~~~

GitHub schedules may be delayed or skipped under load. **Scheduled workflows in public repositories can be disabled after 60 days without repository activity.** Check the Actions tab and re-enable a disabled workflow when necessary. This workflow does not create keep-alive commits.

## Sources and precision

| Index | Primary | Backup |
| --- | --- | --- |
| H00300 | China Securities Index [official daily history](https://www.csindex.com.cn/csindex-home/perf/index-perf?indexCode=H00300&startDate=20041231&endDate=20261120) | [CharlesPai public research page](https://research.charlespai.com/cn/), exact H00300 closes; page credits Tushare Pro |
| 885003.WI | [老钱 public JSON mirror](https://ashare.laoqianriritan.com/data/fund_index.json), specifically series["885003.WI"] | [FundDB / 韭圈儿 raw-close API](https://ai2.jiucaishuo.com/api/index-valuation/history?code=885003.WI&metric=close&years2=10), cross-checked with its [identity endpoint](https://ai2.jiucaishuo.com/api/index-valuation/detail?code=885003.WI) |

The updater fills the CSI URL's endDate with the retrieval date. Mirror homepages: <https://ashare.laoqianriritan.com/> and <https://funddb.cn/>. The backup providers use different hosts from the primary providers; this reduces host-level dependence but does not prove their entire upstream data chains are independent. See [the verification record](docs/data-sources.md) for measured differences and limitations.

The Wind baseline comes from a published **−1.07%** cumulative return on 2004-12-31 relative to an inception value of 1000: 1000 × (1 − 0.0107) ≈ 989.30. The published return was rounded, so the normalized Wind cumulative returns are approximate. The raw point-difference chart subtracts the published closes directly; only its reconstructed 2004-12-31 row uses this approximate baseline. The baseline is held fixed on every refresh.

The Wind feeds are **public redistributions**, not direct authenticated Wind feeds. The updater selects the exact 885003.WI series and checks identity, dates and historical values. Both production adapters use raw closes; they do not reconstruct levels from normalized charts. Public mirrors have no verified availability guarantee and can be delayed or change format. An authorized Wind connection is a separate formal data-service option; none is assumed here.

### Failover rules

1. Validate each provider's exact index, positive finite closes, dates and source cutoff. Primary feeds must agree with all already published history within the recorded precision.
2. For a backup, verify the most recent **continuous tail of expected overlapping dates**, up to 60 and at least 20 observations. An older missing date ends that window; a mismatch inside it rejects the source. The permitted difference for the current raw-close providers is **0.01 index point**, plus a tiny floating-point epsilon.
3. Keep all previously published levels unchanged. Only append later observations; never re-import an alternate provider's older history. Record the provider for each appended historical segment.
4. Try the valid provider combinations and choose the one with the newest **shared** cutoff, preferring primaries on a tie. New dates through that cutoff must align exactly; do not skip a missing date and permanently lose it.
5. A backup older than the already observed source cutoff cannot replace it. If valid providers cannot advance or reproduce the shared data, retain the last verified snapshot and expose the failure instead of pretending it is fresh.

The source details disclose all attempted providers, their dates and validation status. On **2026-11-20**, the CSI backup ended on **2026-11-18** and the Wind backup on **2026-11-13**, while both primaries reached **2026-11-19**. Consequently neither backup moved the page backward. The Wind backup contains older missing/misaligned dates, which is why it is used only for a verified new tail.

Only dates available in both source histories are plotted, plus the reconstructed baseline row. There is no forward-filling or invented trading-day data. Straight segments connect observed points. The included snapshot ends on **2026-11-19**.

## Publish the repository

1. Use the repository **csi300-wind-chart**. A public repository works with GitHub Free.
2. Place this folder's **contents at the repository root**, including the hidden .github directory. The workflow must be at .github/workflows/update-and-deploy.yml.
3. Open **Settings → Pages → Build and deployment → Source** and select **GitHub Actions**.
4. Open **Actions → Refresh data and deploy chart → Run workflow** and choose the default branch. If an initial run occurred before Pages was enabled, run it again after step 3.
5. After deployment succeeds, open the URL in the deploy job or **Settings → Pages**. A project repository normally uses https://YOUR_USERNAME.github.io/csi300-wind-chart/.

For a fork, enable Actions and the scheduled workflow in your own repository. Existing repository or organization policies may also require an administrator to allow the listed GitHub Actions. The workflow runs only on the default branch and publishes only site/.

## Local preview and maintenance

Python 3.10 or later is sufficient; there are no third-party Python dependencies.

~~~bash
python3 -m http.server 8000 --directory site
~~~

Open <http://localhost:8000>. Use this HTTP server rather than opening the HTML file directly, so the browser can fetch data.json.

Fetch fresh source data and run validation tests:

~~~bash
python3 scripts/update_data.py --output site/data.json
python3 -m unittest discover -s tests -v
node --test tests/test_crossings.js
~~~

For offline reproduction, pass both raw source JSON files:

~~~bash
python3 scripts/update_data.py --stock-file /path/to/H00300_raw.json --wind-file /path/to/fund_index.json --output site/data.json
~~~

Use --previous /path/to/previous-data.json to validate against an earlier publication. The workflow obtains the last published dataset for this check. A first deployment returning HTTP 404 uses the included snapshot; other unexpected responses and network errors stop the run.

By default, the local updater returns an error if no valid live provider pair exists. The workflow explicitly uses **--allow-cached** so a valid previous dataset can be served with a visible degraded-source status. It does not hide that status or rewrite the original data-generation time. The implementation retains the common history, not unused individual-source observations beyond its shared cutoff; complementary outages can therefore delay catch-up.

| Path | Purpose |
| --- | --- |
| site/index.html, site/styles.css, site/app.js | Three charts, crossover markers and hover details, source links and source health |
| site/data.json | Index levels, cumulative returns, and source metadata |
| scripts/update_data.py | Provider selection, retained history, alignment and validation |
| scripts/backup_sources.py, scripts/wind_backup.py | Exact-index public backup adapters |
| tests/ | Source parsing, outage behavior, data integrity and crossover checks |
| .github/workflows/update-and-deploy.yml | Daily refresh and Pages deployment |

The workflow uses GitHub's automatically supplied token with restricted permissions and does not commit generated data to the source repository. The live dataset is available at data.json under the published site's URL.

## GitHub references

- [Creating a GitHub Pages site](https://docs.github.com/en/pages/getting-started-with-github-pages/creating-a-github-pages-site)
- [Custom workflows for GitHub Pages](https://docs.github.com/en/pages/getting-started-with-github-pages/using-custom-workflows-with-github-pages)
- [Scheduled workflow behavior and the inactivity limit](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule)
