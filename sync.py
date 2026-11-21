#!/usr/bin/env python3
"""Refresh the Elan backport and verify the exact source revisions OBS builds."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tarfile
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

OBS_API = "https://api.opensuse.org"
PRODUCTION = "home:megamaced"
PACKAGE = "libfprint"
UPSTREAM = "https://gitlab.freedesktop.org/libfprint/libfprint.git"
MR_API = "https://gitlab.freedesktop.org/api/v4/projects/libfprint%2Flibfprint/merge_requests/654"
MR_REF = "refs/merge-requests/654/head"
PATCH = "libfprint-elanpress-0c63.patch"
MANIFEST = "backport.json"
SERVICE_PREFIX = "_service:obs_scm:"
SERVICE_INFO = f"{SERVICE_PREFIX}elanpress-backport.obsinfo"
ARCHIVE = "libfprint-v1.94.10+tod1.tar.bz2"
DISTRO_PATCHES = (
    "label-can-only-be-part-of-a-statement.patch",
    "libfprint-old-meson.patch",
    "focal-tech-support.patch",
)
GIT_SHA = re.compile(r"[a-f0-9]{40}")
AUTOMATIC_PATHS = {
    "libfprint/drivers/elanpress.c",
    "libfprint/drivers/elanpress.h",
    "libfprint/drivers/elanpress-match.c",
    "libfprint/drivers/elanpress-match.h",
    "libfprint/drivers/elanpress-match-sums.h",
    "tests/test-elanpress-match.c",
    "tests/test-elanpress-sums.c",
}


class SyncError(Exception):
    pass


def git(*args, cwd=None, input_data=None):
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        input=input_data,
        stdout=subprocess.PIPE,
        check=True,
    )
    return result.stdout


def get(url):
    for attempt in range(3):
        try:
            with urllib.request.urlopen(url, timeout=25) as response:
                return response.read()
        except (urllib.error.URLError, TimeoutError) as error:
            if isinstance(error, urllib.error.HTTPError) and error.code < 500 and error.code != 429:
                raise
            if attempt == 2:
                raise
            time.sleep(3 * (attempt + 1))


def obs_xml(path):
    return ET.fromstring(get(f"{OBS_API}/public/{path}"))


def source():
    return obs_xml(f"source/{PRODUCTION}/{PACKAGE}?expand=1")


def entries(directory):
    return {entry.attrib["name"]: entry.attrib["md5"] for entry in directory.findall("entry")}


def baseline(directory):
    return {
        name: md5
        for name, md5 in entries(directory).items()
        if name not in {PATCH, "_link", "_service"} and not name.startswith("_service:")
    }


def assert_baseline(directory, expected):
    if baseline(directory) != expected:
        raise SyncError(f"{PRODUCTION} packaging inputs changed. Review the OBS base before updating the backport.")


def source_file(directory, name):
    revision = urllib.parse.quote(directory.attrib["srcmd5"])
    file = urllib.parse.quote(name, safe="")
    data = get(f"{OBS_API}/public/source/{PRODUCTION}/{PACKAGE}/{file}?rev={revision}")
    if hashlib.md5(data).hexdigest() != entries(directory).get(name):
        raise SyncError(f"OBS source file checksum differs for {PRODUCTION}/{name}")
    return data


def git_sha(value):
    if not GIT_SHA.fullmatch(value):
        raise SyncError(f"Expected a full Git commit SHA, got {value!r}")
    return value


def fetch_release():
    git("fetch", "--no-tags", "origin", "refs/heads/release:refs/remotes/origin/release")
    return git_sha(git("rev-parse", "refs/remotes/origin/release").decode().strip())


def push_release(commit):
    git("push", "origin", f"{git_sha(commit)}:refs/heads/release")


def payload(commit):
    patch = git("show", f"{git_sha(commit)}:{PATCH}")
    manifest = json.loads(git("show", f"{commit}:{MANIFEST}"))
    git_sha(manifest["upstream"])
    if hashlib.sha256(patch).hexdigest() != manifest["patch_sha256"]:
        raise SyncError(f"Patch checksum differs in GitHub commit {commit}")
    return patch, manifest


def mr_head():
    mr = json.loads(get(MR_API))
    if mr["state"] != "opened":
        raise SyncError(f"libfprint MR !654 is {mr['state']}. Review the backport lifecycle.")
    return git_sha(mr["sha"])


def fetch_mr(repo, expected):
    git("fetch", "--no-tags", UPSTREAM, MR_REF, cwd=repo)
    actual = git_sha(git("rev-parse", "FETCH_HEAD", cwd=repo).decode().strip())
    if actual != expected:
        raise SyncError("GitLab MR changed during fetch. Run the refresh again.")


def distro_tree(work, directory):
    for name in (ARCHIVE, *DISTRO_PATCHES):
        data = source_file(directory, name)
        (work / name).write_bytes(data)
    with tarfile.open(work / ARCHIVE) as archive:
        archive.extractall(work, filter="data")
    tree = work / ARCHIVE.removesuffix(".tar.bz2")
    git("init", "--quiet", cwd=tree)
    for patch in DISTRO_PATCHES:
        git("apply", str(work / patch), cwd=tree)
    git("add", "--force", ".", cwd=tree)
    return tree


def commit(repo, message):
    git(
        "-c", "user.name=github-actions[bot]",
        "-c", "user.email=41898282+github-actions[bot]@users.noreply.github.com",
        "commit", "--quiet", "-m", message, cwd=repo,
    )
    return git_sha(git("rev-parse", "HEAD", cwd=repo).decode().strip())


def update_release(release, patch, manifest, directory):
    latest = mr_head()
    previous = manifest["upstream"]
    if previous == latest:
        return release
    with tempfile.TemporaryDirectory(prefix="elanpress-refresh-") as tmp:
        work = Path(tmp)
        upstream = work / "upstream"
        upstream.mkdir()
        git("init", "--quiet", cwd=upstream)
        fetch_mr(upstream, latest)
        ancestor = subprocess.run(
            ["git", "merge-base", "--is-ancestor", previous, latest], cwd=upstream
        )
        if ancestor.returncode:
            raise SyncError(f"MR !654 changed history since {previous}. Review the new head {latest}.")
        paths = set(git("diff", "--no-renames", "--name-only", "-z", previous, latest, cwd=upstream)
                    .decode().rstrip("\0").split("\0")) - {""}
        if paths - AUTOMATIC_PATHS:
            raise SyncError("MR changes require manual backport review: " + ", ".join(sorted(paths - AUTOMATIC_PATHS)))
        id_changes = git(
            "diff", "--no-ext-diff", "-G", "id_table|FpIdEntry|[.](pid|vid)|0x0[cC][0-9a-fA-F]{2}",
            previous, latest, "--", "libfprint/drivers/elanpress.h", cwd=upstream,
        )
        if id_changes:
            raise SyncError("Elan driver device table changes require manual backport review.")
        tree = distro_tree(work, directory)
        original = commit(tree, "Distribution sources with Patch0–Patch2")
        git("apply", "--index", "-", cwd=tree, input_data=patch)
        delta = git("diff", "--full-index", "--binary", previous, latest, cwd=upstream)
        if delta:
            # Put upstream's preimages in the downstream repository for three-way fallback.
            git("fetch", "--no-tags", str(upstream), latest, cwd=tree)
            git("apply", "--3way", "-", cwd=tree, input_data=delta)
        git("diff", "--cached", "--check", original, cwd=tree)
        new_patch = git("diff", "--cached", "--binary", original, cwd=tree)
        release_tree = work / "release"
        git("worktree", "add", "--detach", str(release_tree), release)
        try:
            (release_tree / PATCH).write_bytes(new_patch)
            manifest.update(
                upstream=latest,
                patch_sha256=hashlib.sha256(new_patch).hexdigest(),
            )
            (release_tree / MANIFEST).write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
            git("add", PATCH, MANIFEST, cwd=release_tree)
            updated = commit(release_tree, f"Backport libfprint MR !654 at {latest[:12]}")
            assert_baseline(source(), manifest["baseline"])
            push_release(updated)
            return updated
        finally:
            git("worktree", "remove", "--force", str(release_tree))


def trigger():
    token = os.environ.get("OBS_PRODUCTION_TOKEN")
    if not token:
        raise SyncError("Missing GitHub Actions secret OBS_PRODUCTION_TOKEN")
    request = urllib.request.Request(
        f"{OBS_API}/trigger/runservice",
        headers={"Authorization": f"Token {token}"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=25) as response:
        response.read()


def build_status(commit, patch, manifest):
    directory = source()
    assert_baseline(directory, manifest["baseline"])
    files = entries(directory)
    if PATCH in files:
        raise SyncError(f"{PRODUCTION} has a plain Patch3 alongside the source service.")
    service = directory.find("serviceinfo")
    if "_service_error" in files or service is not None and service.get("code") in {"failed", "broken"}:
        raise SyncError(f"OBS source service failed in {PRODUCTION}. See the package source service log.")
    if service is None:
        if "_service" not in files:
            raise SyncError(f"{PRODUCTION} has no OBS source service configured.")
        return "patch import needed", False
    if service.get("code") != "succeeded":
        return "import pending", False
    if files.get(SERVICE_PREFIX + PATCH) != hashlib.md5(patch).hexdigest():
        return "patch import needed", False
    obsinfo = source_file(directory, SERVICE_INFO).decode()
    if f"commit: {commit}" not in obsinfo.splitlines():
        return "Git revision import needed", False

    targets = {
        (repo.get("name"), arch.text)
        for repo in obs_xml(f"source/{PRODUCTION}/_meta").findall("repository")
        for arch in repo.findall("arch")
    }
    results = obs_xml(f"build/{PRODUCTION}/_result?package={PACKAGE}")
    actual = {(result.get("repository"), result.get("arch")) for result in results}
    if not targets:
        raise SyncError(f"{PRODUCTION} has no configured build targets.")
    if actual != targets:
        return f"waiting for configured OBS build targets (reported {sorted(actual)})", False
    pending = []
    publishing = []
    for result in results:
        repo, arch = result.get("repository"), result.get("arch")
        package_status = result.find("status")
        if package_status is None:
            pending.append(f"{repo}/{arch} waiting for package status")
            continue
        status = package_status.get("code")
        if status in {"failed", "unresolvable", "broken", "excluded", "disabled"}:
            raise SyncError(f"{PRODUCTION} {repo}/{arch}: {status}")
        if status != "succeeded":
            pending.append(f"{repo}/{arch} {status}")
            continue
        history = obs_xml(f"build/{PRODUCTION}/{repo}/{arch}/{PACKAGE}/_history").findall("entry")
        if not history or history[-1].get("srcmd5") != directory.get("srcmd5"):
            pending.append(f"{repo}/{arch} build of current revision pending")
        elif result.get("code") != "published":
            publishing.append(f"{repo}/{arch}")
    if pending:
        return ", ".join(pending), False
    verified = f"verified source {directory.attrib['srcmd5']} on all {len(targets)} targets"
    if publishing:
        verified += "; OBS repository publication pending on " + ", ".join(publishing)
    return verified, True


def ensure_build(commit):
    patch, manifest = payload(commit)
    status, done = build_status(commit, patch, manifest)
    started = time.monotonic()
    triggers = 0
    previous = None
    while True:
        if status != previous:
            print(f"{PRODUCTION} {commit[:12]}: {status}", flush=True)
            previous = status
        if done:
            return
        elapsed = time.monotonic() - started
        if elapsed >= 35 * 60:
            raise SyncError(f"OBS did not verify {PRODUCTION} at {commit}: {status}")
        needs_import = status in {"patch import needed", "Git revision import needed"}
        if needs_import and (triggers == 0 or triggers == 1 and elapsed >= 300):
            trigger()
            triggers += 1
        time.sleep(25)
        status, done = build_status(commit, patch, manifest)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    try:
        release = fetch_release()
        patch, manifest = payload(release)
        directory = source()
        assert_baseline(directory, manifest["baseline"])
        release = update_release(release, patch, manifest, directory)
        ensure_build(release)
    except (SyncError, urllib.error.URLError, TimeoutError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"Elan backport refresh failed: {error}\n")


if __name__ == "__main__":
    main()
