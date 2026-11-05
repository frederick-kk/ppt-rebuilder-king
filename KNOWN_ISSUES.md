# Known limits

- Image-provider execution, exact visual fidelity, billing and end-to-end time depend on the host and are not certified by local tests. This distribution-only revision adds no provider, quality, timing or Office acceptance results.
- Native body text/basic geometry remain editable; complex artwork is a movable PNG, not editable lettering.
- Coordinate labels depend on installed font metrics. Alternate-font edge-label omissions remain a known limitation; guide25/50/5 and coordinate-guide code are unchanged.
- Exact Microsoft YaHei is not bundled. Missing matching fonts use an explicitly estimated bounded fit.
- Raw private diagnostics are optional developer regressions and are not distributed. The ordinary runtime archive excludes development and historical materials; source-only historical validation records are not new runtime acceptance evidence.
- The 20261105 archive is newly packaged. Its 423 runtime files and internal package identity remain unchanged; verify the current archive SHA256 and embedded manifests rather than the previous archive hash.
- The preserved 20261025 native-space fix covers ordinary ASCII spaces in missing-font width estimation; genuine text overflow can still stop delivery. Ownership-proved transparent gutters recover more components, while unknown or crossing content remains protected.
