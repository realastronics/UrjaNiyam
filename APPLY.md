# Apply the Urja Niyam refactor

Prepared against commit 9acf55166c2963095ae2de7b2e431f1aea63b401.
GitHub publishing was unavailable: the integration returned HTTP 403 for repository writes.
No remote branch, pull request or merge was created.

From a clean checkout of the repository, create a review branch and apply the extracted patch:

```sh
git switch -c refactor/simplify-orchestration
git apply --check /path/to/urja-niyam-refactor.patch
git apply --index /path/to/urja-niyam-refactor.patch
pip install -e ".[dev,data,oracle]"
python -m pytest -q
git diff --cached --stat
```

Replace /path/to with the extracted patch location (quote paths containing spaces).
If the check fails because main has changed, do not force it; use a clean branch at the base commit
or reconcile the changes manually. Inspect and commit the staged changes when satisfied.

The patch includes the audit, tests, code changes, and the Data-to-data directory rename.
It was applied to a pristine checkout and its resulting Git tree exactly matched the tested changes.
The included audit lists unresolved correctness issues; this is not a claim of production readiness.
