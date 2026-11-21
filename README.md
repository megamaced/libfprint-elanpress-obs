# Elan press driver backport for OBS

GitHub Actions checks libfprint [MR !654](https://gitlab.freedesktop.org/libfprint/libfprint/-/merge_requests/654) daily. When a driver-only update applies cleanly to the current TOD base, Actions regenerates the patch and pushes it to `release`. It triggers [home:megamaced/libfprint](https://build.opensuse.org/package/show/home:megamaced/libfprint) with the package-scoped `runservice` token in `OBS_PRODUCTION_TOKEN`.

The manifest in `release` pins the upstream Git commit and packaging input digests. Changes to the TOD base, device routing, shared code, or rewritten upstream history fail the refresh for review.

For interrupted jobs, rerun the workflow. It verifies the patch, fetched Git revision, and build histories across the configured OBS targets and reports any repositories still publishing. GitHub can disable the daily schedule in a public repo after 60 days of inactivity. Manual dispatch also runs the check.
