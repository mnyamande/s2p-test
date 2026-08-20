#!/usr/bin/env python3
"""Read-only GitHub access for the participant-progress skill.

Prefers the `gh` CLI (auth is already handled by `gh auth login`, so the
facilitator never has to put a token in a config file). Falls back to the REST
API with GH_TOKEN / GITHUB_TOKEN when `gh` is not installed.

Every request this module can make is a GET. There is no code path here that
writes to a participant's repository.
"""

import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

API_ROOT = "https://api.github.com"
USER_AGENT = "participant-progress-skill (read-only)"


class GitHubError(RuntimeError):
    """An API call failed in a way the caller may want to report, not crash on."""

    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


class GitHub:
    """Minimal read-only GitHub client with a `gh` CLI and a REST backend."""

    def __init__(self, prefer_gh=True, token=None, verbose=False):
        self.verbose = verbose
        self.token = token or os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
        self.gh_path = shutil.which("gh") if prefer_gh else None
        if self.gh_path and not self._gh_is_authenticated():
            self.gh_path = None
        self.backend = "gh" if self.gh_path else "rest"
        if self.backend == "rest" and not self.token:
            raise GitHubError(
                "No usable GitHub credentials. Either install and authenticate the "
                "`gh` CLI (`gh auth login`), or export GH_TOKEN / GITHUB_TOKEN with "
                "read access to the org."
            )
        self.calls = 0

    # -- backend selection -------------------------------------------------

    def _gh_is_authenticated(self):
        try:
            proc = subprocess.run(
                [self.gh_path, "auth", "status"],
                capture_output=True, text=True, timeout=20,
            )
            return proc.returncode == 0
        except (OSError, subprocess.SubprocessError):
            return False

    def _log(self, message):
        if self.verbose:
            print("[scan] %s" % message, file=sys.stderr)

    # -- request plumbing --------------------------------------------------

    def get(self, path, params=None, accept_missing=False):
        """GET one endpoint. Returns parsed JSON, or None on 404/409 when allowed."""
        url = path if path.startswith("http") else API_ROOT + path
        if params:
            url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
        for attempt in range(4):
            try:
                self.calls += 1
                body, _ = self._request(url)
                return json.loads(body) if body else None
            except GitHubError as exc:
                if exc.status in (404, 409, 403) and accept_missing:
                    # 404: no such resource (Actions disabled, no CLAUDE.md, ...)
                    # 409: empty repository. 403: feature disabled for the repo.
                    self._log("%s -> %s (treated as absent)" % (url, exc.status))
                    return None
                if exc.status == 429 or (exc.status == 403 and "rate limit" in str(exc).lower()):
                    wait = 2 ** (attempt + 1)
                    self._log("rate limited, sleeping %ss" % wait)
                    time.sleep(wait)
                    continue
                if exc.status is None and attempt < 3:
                    time.sleep(2 ** (attempt + 1))
                    continue
                raise
        raise GitHubError("giving up on %s after repeated failures" % url)

    def paginate(self, path, params=None, max_items=None, accept_missing=False):
        """GET a list endpoint, following pages up to `max_items`."""
        params = dict(params or {})
        params.setdefault("per_page", 100)
        page = 1
        out = []
        while True:
            params["page"] = page
            batch = self.get(path, params, accept_missing=accept_missing)
            if not batch:
                break
            if isinstance(batch, dict):
                # Some list endpoints wrap the array (e.g. actions/runs).
                for key in ("workflow_runs", "check_runs", "items", "artifacts"):
                    if key in batch:
                        batch = batch[key]
                        break
                else:
                    return batch
            out.extend(batch)
            if len(batch) < params["per_page"]:
                break
            if max_items and len(out) >= max_items:
                break
            page += 1
            if page > 20:  # hard stop; nothing we read needs 2000+ items
                break
        return out[:max_items] if max_items else out

    def _request(self, url):
        if self.backend == "gh":
            return self._request_gh(url)
        return self._request_rest(url)

    def _request_gh(self, url):
        endpoint = url[len(API_ROOT):] if url.startswith(API_ROOT) else url
        cmd = [self.gh_path, "api", "-H", "Accept: application/vnd.github+json", endpoint]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
        if proc.returncode != 0:
            stderr = (proc.stderr or "").strip()
            status = None
            for code in (404, 409, 403, 429, 401, 500, 502, 503):
                if "HTTP %d" % code in stderr or "(HTTP %d)" % code in stderr:
                    status = code
                    break
            raise GitHubError("gh api %s failed: %s" % (endpoint, stderr[:300]), status)
        return proc.stdout, {}

    def _request_rest(self, url):
        req = urllib.request.Request(url)
        req.add_header("Authorization", "Bearer %s" % self.token)
        req.add_header("Accept", "application/vnd.github+json")
        req.add_header("X-GitHub-Api-Version", "2022-11-28")
        req.add_header("User-Agent", USER_AGENT)
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return resp.read().decode("utf-8"), dict(resp.headers)
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode("utf-8")[:200]
            except Exception:
                pass
            raise GitHubError("GET %s -> %s %s" % (url, exc.code, detail), exc.code)
        except urllib.error.URLError as exc:
            raise GitHubError("GET %s failed: %s" % (url, exc.reason), None)
