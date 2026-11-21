# Elan press driver backport for OBS

GitHub Actions checks libfprint [MR !654](https://gitlab.freedesktop.org/libfprint/libfprint/-/merge_requests/654) daily and builds new backports in unpublished [OBS staging](https://build.opensuse.org/package/show/home:megamaced:libfprint-ci/libfprint). The target package is [home:megamaced/libfprint](https://build.opensuse.org/package/show/home:megamaced/libfprint).

`candidate` and `release` hold the patch and `backport.json`. The manifest pins the upstream Git commit and packaging input digests. The workflow advances `release` only to an exact commit that staging built. Each OBS package uses its own package-scoped `runservice` token in the matching Actions secret. The `OBS_PRODUCTION_ENABLED` Actions variable controls whether the production step runs.

The first update from the September backport waits for hardware verification. Install the staging build and check enrollment and matching on the Elan reader, then dispatch the workflow with the full verified **GitHub candidate commit** in `promote_commit`. Later driver-only updates promote automatically. Changes to the TOD base, routing, shared code, or rewritten upstream history fail the refresh for review.

For interrupted jobs, rerun the workflow. It verifies the patch, fetched Git revision, and build histories across all five targets before marking a revision successful. GitHub can disable the daily schedule in a public repo after 60 days of inactivity. Manual dispatch also runs the check.
