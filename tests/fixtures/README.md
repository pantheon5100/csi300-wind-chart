# Source fixtures

These gzip files preserve the actual source observations retrieved on 2026-11-20,
ending on 2026-11-19. They are not generated prices. Unused fields were removed to
keep the tests small; values, dates, series identity and source notes are retained.

- `stock.json.gz`: the official CSI response for `H00300`, requested from
  `https://www.csindex.com.cn/csindex-home/perf/index-perf?indexCode=H00300&startDate=20041231&endDate=20261120`.
- `wind.json.gz`: the exact `885003.WI` series in
  `https://ashare.laoqianriritan.com/data/fund_index.json`. This is a third-party
  public mirror, not a direct authenticated Wind feed. Its top-level source note
  concerns the mixed-index file and is retained verbatim.

The November 24 review found that this global source note mentions an H20269
fallback without establishing the Wind series' provenance. The unchanged
archive is now a negative parser test. Tests that need a previously accepted
snapshot explicitly replace only the in-memory source note with synthetic
trusted metadata; this is a test setup, not authentication of the original feed.

The Wind point for 2026-11-13 (4990.73) was independently checked against the
public Jiucaishuo index details on the retrieval date. This historical check does
not imply independent verification of every future daily update.

The known stock-only date 2018-06-18 is deliberately retained in the stock fixture
to exercise the common-date alignment. The updater does not fill that missing
Wind observation.
