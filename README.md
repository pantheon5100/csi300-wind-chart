# 沪深300全收益 vs 万得偏债混合型基金

A simple GitHub Pages site for **沪深300全收益指数 H00300** and **万得偏债混合型基金指数 885003.WI**. It displays three charts, the latest common data date, and explicit source links. The page uses native SVG with no framework, CDN, or API key.

## Three charts

| Chart | Y-axis calculation |
| --- | --- |
| Raw index levels | Each source's published index level in points. |
| Cumulative returns | (index level / index level on 2004-12-31 − 1) × 100, in percent. |
| Return difference | H00300 cumulative return − 885003.WI cumulative return, in **percentage points**. |

The cumulative-return baseline is fixed at **2004-12-31 = 0%**. H00300 uses **1000.00** points; Wind uses **approximately 989.30** points. A positive return difference means H00300 has the larger cumulative return from that date; a negative value means the Wind index does.

**Raw index levels have different original bases.** Their absolute point values are not directly comparable as investment gains. Use the cumulative-return chart or return-difference chart to compare performance from the common starting date. The reconstructed Wind baseline is approximate in all charts.

## Daily refresh

- **Source data:** GitHub Actions checks both sources once daily at **09:00 Korea time / 08:00 Beijing time**. A push to the default branch or a manual workflow run also refreshes and deploys the site.
- **Webpage:** loads the published dataset when opened, checks again every **24 hours** while open, and offers a manual check button. These browser checks read the site's data file; they do not trigger a source fetch or a GitHub workflow.
- **Failure handling:** data is validated before deployment. A failed source or invalid update leaves the last successful live site available; a failed browser check preserves the chart already displayed.
- **Dates:** the latest common source date is shown on the page. Weekends and holidays do not create new observations. A notice appears when the data date is more than five calendar days old, which may also occur during long market holidays.

The schedule in .github/workflows/update-and-deploy.yml is UTC:

~~~yaml
schedule:
  - cron: '0 0 * * *'
~~~

GitHub schedules may be delayed or skipped under load. **Scheduled workflows in public repositories can be disabled after 60 days without repository activity.** Check the Actions tab and re-enable a disabled workflow when necessary. This workflow does not create keep-alive commits.

## Sources and precision

| Index | Data source |
| --- | --- |
| H00300 | China Securities Index official daily history: https://www.csindex.com.cn/csindex-home/perf/index-perf?indexCode=H00300&startDate=20041231&endDate=YYYYMMDD |
| 885003.WI | Public mirror: <https://ashare.laoqianriritan.com/data/fund_index.json>, specifically series["885003.WI"] |

For the CSI URL, YYYYMMDD is the retrieval date. The updater fills it automatically. Mirror homepage: <https://ashare.laoqianriritan.com/>. The initial Wind history was independently cross-checked using 韭圈儿: <https://funddb.cn/>.

The Wind baseline comes from a published **−1.07%** cumulative return on 2004-12-31 relative to an inception value of 1000: 1000 × (1 − 0.0107) ≈ 989.30. The published return was rounded, so the resulting Wind cumulative returns and return difference are approximate. The baseline is held fixed on every refresh.

The Wind feed is a **public redistribution**, not a direct authenticated Wind feed. Daily updates use this mirror and validation checks; they do not independently verify each new observation through a Wind terminal. The mirror's top-level metadata may describe fallbacks for other series. The updater selects the exact 885003.WI series and checks known historical values rather than substituting a different index. A mirror can become stale, change format, or stop responding; an authorized Wind source can replace it later without changing the chart formulas.

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
~~~

For offline reproduction, pass both raw source JSON files:

~~~bash
python3 scripts/update_data.py --stock-file /path/to/H00300_raw.json --wind-file /path/to/fund_index.json --output site/data.json
~~~

Use --previous /path/to/previous-data.json to validate against an earlier publication. The workflow obtains the last published dataset for this check. A first deployment returning HTTP 404 uses the included snapshot; other unexpected responses and network errors stop the run.

| Path | Purpose |
| --- | --- |
| site/index.html, site/styles.css, site/app.js | The three charts, source links, and page refresh |
| site/data.json | Index levels, cumulative returns, and source metadata |
| scripts/update_data.py | Source retrieval, date alignment, and validation |
| tests/test_update_data.py | Data integrity and calculation checks |
| .github/workflows/update-and-deploy.yml | Daily refresh and Pages deployment |

The workflow uses GitHub's automatically supplied token with restricted permissions and does not commit generated data to the source repository. The live dataset is available at data.json under the published site's URL.

## GitHub references

- [Creating a GitHub Pages site](https://docs.github.com/en/pages/getting-started-with-github-pages/creating-a-github-pages-site)
- [Custom workflows for GitHub Pages](https://docs.github.com/en/pages/getting-started-with-github-pages/using-custom-workflows-with-github-pages)
- [Scheduled workflow behavior and the inactivity limit](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule)
