# Known Beta limits

- Image-provider execution, exact visual fidelity, billing and end-to-end time depend on the host and are not certified by local tests. No fresh provider call is part of release validation.
- Native body text/basic geometry remain editable; complex artwork is a movable PNG, not editable lettering.
- Coordinate labels depend on installed font metrics. Alternate-font edge-label omissions remain a known limitation; guide25/50/5 and recognized production code are unchanged.
- Exact Microsoft YaHei is not bundled. Missing matching fonts use an explicitly estimated bounded fit.
- Private historic diagnostics are optional developer regressions and are not distributed.
