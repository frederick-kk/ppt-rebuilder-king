# Known Beta limits

- Image-provider execution, exact visual fidelity, billing and end-to-end time depend on the host and are not certified by local tests. No fresh provider call is part of release validation.
- Native body text/basic geometry remain editable; complex artwork is a movable PNG, not editable lettering.
- Coordinate labels depend on installed font metrics. Alternate-font edge-label omissions remain a known limitation; guide25/50/5 and coordinate-guide code are unchanged.
- Exact Microsoft YaHei is not bundled. Missing matching fonts use an explicitly estimated bounded fit.
- Raw private diagnostics are optional developer regressions and are not distributed. The frozen approved ZIP retains sanitized, version-labelled historic outer materials; these are neither default inputs nor current acceptance evidence.
- The official 20261025 ZIP is a frozen byte copy. The source builder produces a separate allowlisted development format, not a byte-identical replacement; use the release SHA256 and embedded manifests for the official attachment.
- The 20261025 native-space fix covers ordinary ASCII spaces in missing-font width estimation; genuine text overflow can still stop delivery. Ownership-proved transparent gutters recover more components, while unknown or crossing content remains protected.
