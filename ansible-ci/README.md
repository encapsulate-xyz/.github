# ansible-ci

Shared tools for `.github/workflows/ansible-ci.yml`, the CI every Ansible repo runs.

| File | What it does |
|---|---|
| `callback_plugins/task_coverage.py` | Ansible callback that records every task that runs, to the file in `TASK_COVERAGE_FILE` |
| `check_task_coverage.py` | Fails if a task in the repo neither runs in a Molecule scenario nor is listed in `molecule/untested.yml` |

## The molecule job

It runs only in repos that have Molecule scenarios using the Docker driver (`molecule/<scenario>/molecule.yml`). For each one it:

1. replaces encrypted files with empty stubs, so scenarios supply fake values for `group_vars/vault.yml`
2. runs `molecule test`: a fresh container, the playbook, the playbook again to check nothing changes, then the scenario's `verify.yml`
3. checks task coverage across all scenarios

Scenarios that run on a real server (driver `default`) need its address and key, so CI skips them. Run those locally before merging risky changes.

## Task coverage

Every task in the repo must either run in a scenario or be listed in `molecule/untested.yml` with a reason:

```yaml
- file: tasks/verify_service.yml
  reason: needs a node that syncs; runs on every real deploy
- file: tasks/get_and_save_vault_secret.yml
  task: Save the fetched key secret to the file
  reason: this repo has no key-type secret
```

The check also fails when a listed task runs or no longer exists, so the list stays accurate. Handlers count only when something notifies them. Static imports and `meta` tasks don't count, because they never report a result.

To test a change to these tools from a branch, point a repo's caller at it:

```yaml
jobs:
  ansible:
    uses: encapsulate-xyz/.github/.github/workflows/ansible-ci.yml@<branch>
    with:
      ci-ref: <branch>
```
