# Full cleaned scan review — 18 November 2026

This candidate is based on the installed laptop snapshot `d31b91b`, with the existing laptop changes preserved. The review branch is `full-scan-ring-evidence`. It has not been installed over the normal application.

## Changes

- Detect bright/dark continuous circumferential evidence rather than periodic bolt rows. Keep competing/isolated traces unresolved.
- Automatically recheck a bounded, single missing-ring gap at 10 mm using retained original points, the unchanged measured reference and the existing spatial index. Accept only a unique measured candidate compatible with both observed neighbours. No assumed midpoint or reconstructed wall points.
- Preserve the original analysis scope, exclusion mask and original IDs during the additional read. Large gaps/maps remain unresolved rather than allocating unbounded memory. Fine guides use 10 mm evidence; the parent classification map retains the requested map resolution.
- Persist the exact existing intensity percentiles in a small revision-keyed cache. Cold percentile reads now honour viewport cancellation. Shorten camera debounce from 350 to 150 ms and cancel superseded zoom requests even if they remain within the previous view.
- Add opt-in local preview timings through `TUNNEL_PREVIEW_METRICS`; disabled unless a path is supplied.

## Actual scan evidence

918,833,727 stored observations; **915,963,158 retained points**. The original project SHA-256 remains `dd37e5d79b2e6b385d7fd34baa55e1f11d41d97ec323f5721eefee6f454f2ac2`. Source XYZ, attributes and mask sizes/timestamps remain unchanged. No duplicate XYZ cloud was created. Separate test manifests and existing hardlinked caches were used.

The previous exact-source 20 mm run found 73 candidates and one 3.976 m gap, taking 310.61 s with the measured reference reused. The automatic run reread every retained point and found **74 candidates**, with one isolated end trace still unresolved. Its result timer was **261.12 s** (261.39 s including preservation checks). The extra indexed gap check read 138,117,120 candidate source rows, retained 19,292,218 points and took **21.91 s**. Cache state and other desktop load differ: these full-run timings are not a controlled speedup claim. The automatic station differs by about 6 mm from the earlier manual 10 mm check, while the other 73 stations are preserved.

The limit box in the separate test project is perpendicular to the local design tangent at chainage 59.059 m. Fresh box analysis used 29,999,770 retained points and found three guides separated by 1.995 and 1.999 m. Ring guides follow visible measured joints in the unwrapped evidence; pipe, ventilation and setup gaps are not treated as seams. The user's original saved box is unchanged. The pipe design does not independently locate individual ring joints.

## Viewer measurements and limitations

A fixed close-up on the full cleaned source had 27,884,192 eligible retained points; the deterministic display contained 150,000 points. Before/after selected IDs and packed coordinates/colours were byte-identical (packed SHA-256 `4678af42171c85991930a24973c3f404107806a5a6c06ccd9cf4036040aa20e7`). Final warm sampler runs were 3.75/3.56 s before and 3.58/3.55 s after: **no material warm sampler speedup demonstrated**. The final sampler is unchanged by this refinement patch.

The exact intensity-range restart cache reduced a separately timed, OS-warm percentile lookup from 0.187 s to 0.000432 s, producing the same range [10, 93]. This does not establish cold-disk end-to-end zoom latency. Mapping-only, hash-prefilter and wider read-coalescing experiments were slower in warm comparisons and were discarded. No sampling reduction was retained.

The normal Windows launcher reopened the separate full cleaned project. Its recorded overview request took 0.035 s; an uncached wider plan view took **33.45 s**, a closer view **9.86 s**, and the next wall pan **7.68 s**. Those desktop requests displayed 193,355 points at the existing 35% display setting and retained the full measured source. Wall returns remained points rather than scan-line bands. These actual-app delays are a remaining failure, not superseded by the faster isolated warm benchmark. The old display stayed visible during refinement; no sparse replacement stage was added.

## Tests and remaining work

38 Python regressions passed, plus 3 occlusion/cache checks. JavaScript preview-flow and navigation-cancellation checks passed. Scope intersection, deleted points, blank gaps, ambiguous gaps, exact percentile reuse/invalidation and cancellation are covered. The full scan audit ran detection rather than exporting a complete new set of 916 million labels.

Warm close-up latency is still an open performance issue. Large missing intervals stay unresolved; only a bounded single-gap refinement is automatic. Panel boundaries in the test box are not all resolved. No installer or release approval is implied.

## Comparison with 911efe7

[`911efe71bb7ce3bb1f99c97baa4bff1f7c59b06a`](https://github.com/sfy22qrxvw-ux/Tunnel-studio/commit/911efe71bb7ce3bb1f99c97baa4bff1f7c59b06a) adds measured-scan/fitted-guide review controls, measured-review density handling and joint terminology. This branch instead verifies full-scan measured detection and automatic gap refinement on the preserved laptop baseline. Those upstream review controls have **not** been merged here. Reconcile that UI commit and the laptop-specific changes before release.

The PR targets a published `codex/laptop-ring-validation-baseline` snapshot branch because this laptop checkout has independent history. This keeps the review focused on the tested changes; it is not ready to merge directly into main. Local evidence, scripts, detailed timings and screenshots are under `Documentation/Full scan ring audit/Automatic refinement` in the user's central Tunnel Studio project folder.
