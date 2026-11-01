"""Fails if a task in the repo neither runs in a Molecule scenario nor is listed as untested.

Usage: check_task_coverage.py <task coverage file> <untested list>

The task coverage file is written by the task_coverage callback while Molecule runs.
The untested list (molecule/untested.yml) names tasks no scenario can run, each with a reason:

  - file: tasks/verify_service.yml
    reason: needs a node that syncs
  - file: tasks/get_and_save_vault_secret.yml
    task: Save the fetched key secret to the file
    reason: this repo has no key-type secret

The check also fails when a listed task runs or no longer exists, so the list stays accurate.
"""

import json
import os
import pathlib
import sys

import yaml

TASK_FILE_GLOBS = ("roles/*/tasks/**/*.yml", "roles/*/handlers/**/*.yml", "tasks/**/*.yml")
PLAYBOOK_GLOBS = ("*.yml", "playbooks/*.yml")
PLAY_SECTIONS = ("pre_tasks", "tasks", "post_tasks", "handlers")
BLOCK_SECTIONS = ("block", "rescue", "always")
# static imports and meta tasks never report a result of their own
NO_RESULT_ACTIONS = {"import_tasks", "import_role", "import_playbook", "meta"}


class Loader(yaml.SafeLoader):
    pass


def construct_mapping(loader, node):
    mapping = loader.construct_mapping(node)
    mapping["__line__"] = node.start_mark.line + 1
    return mapping


Loader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, construct_mapping)
# Ansible tags such as !unsafe and !vault don't matter here
Loader.add_multi_constructor("!", lambda loader, suffix, node: None)


def load(path):
    try:
        return yaml.load(path.read_text(), Loader=Loader)
    except yaml.YAMLError:
        return None


def leaf_tasks(items):
    for item in items or []:
        if not isinstance(item, dict):
            continue
        if any(section in item for section in BLOCK_SECTIONS):
            for section in BLOCK_SECTIONS:
                yield from leaf_tasks(item.get(section))
        else:
            yield item


def action(task):
    return next((key.split(".")[-1] for key in task if key.split(".")[-1] in NO_RESULT_ACTIONS), None)


def notified_names(tasks):
    names = set()
    for task in tasks:
        notify = task.get("notify") or []
        names.update([notify] if isinstance(notify, str) else notify)
    return names


def repo_tasks(root):
    """Returns {(file, line): (name, is_handler)} for every task in the repo that can report a result."""
    found = []
    for pattern in PLAYBOOK_GLOBS:
        for path in sorted(root.glob(pattern)):
            plays = load(path)
            if not isinstance(plays, list) or not all(isinstance(play, dict) for play in plays):
                continue
            if not any("hosts" in play for play in plays):
                continue
            for play in plays:
                for section in PLAY_SECTIONS:
                    found += [(path, task, section == "handlers") for task in leaf_tasks(play.get(section))]
    for pattern in TASK_FILE_GLOBS:
        for path in sorted(root.glob(pattern)):
            handler = "/handlers/" in path.as_posix()
            found += [(path, task, handler) for task in leaf_tasks(load(path))]

    notified = notified_names(task for _, task, _ in found)
    tasks = {}
    for path, task, handler in found:
        if action(task):
            continue
        name = task.get("name") or f"unnamed task at line {task['__line__']}"
        listen = task.get("listen") or []
        topics = {name, *([listen] if isinstance(listen, str) else listen)}
        # a handler nothing notifies can never run, so it isn't counted
        if handler and not topics & notified:
            continue
        tasks[(path.relative_to(root).as_posix(), task["__line__"])] = (name, handler)
    return tasks


def ran_tasks(coverage_file, root):
    ran = set()
    for line in pathlib.Path(coverage_file).read_text().splitlines():
        record = json.loads(line)
        if record["status"] == "skipped":
            continue
        path = pathlib.Path(record["path"])
        if not path.is_absolute():
            ran.add((path.as_posix(), record["line"]))
        elif root in path.resolve().parents:
            ran.add((path.resolve().relative_to(root).as_posix(), record["line"]))
    return ran


def table(first_column, keys, ran, listed):
    """Markdown table lines: header, divider, and one row counting the given tasks."""
    keys = set(keys)
    total, covered = len(keys), len(keys & ran)
    header = ["Tasks", "Ran in Molecule", "Listed as untested", "Not accounted for"]
    row = [str(total), f"{covered} ({covered * 100 // max(total, 1)}%)", str(len((keys & listed) - ran)), str(len(keys - ran - listed))]
    if first_column:
        header, row = [first_column, *header], [f"`{first_column}`", *row]
    return ["| " + " | ".join(header) + " |", "|" + "---|" * len(header), "| " + " | ".join(row) + " |"]


def main(coverage_file, untested_file):
    root = pathlib.Path.cwd().resolve()
    tasks = repo_tasks(root)
    ran = ran_tasks(coverage_file, root) & tasks.keys()
    untested = (load(pathlib.Path(untested_file)) or []) if os.path.exists(untested_file) else []

    errors = []
    listed = set()
    for entry in untested:
        matches = {key for key, (name, _) in tasks.items() if key[0] == entry.get("file") and entry.get("task") in (None, name)}
        where = f"{entry.get('file')}" + (f": {entry['task']}" if entry.get("task") else "")
        if not entry.get("reason"):
            errors.append((untested_file, entry["__line__"], f"'{where}' is listed as untested without a reason"))
        if not matches:
            errors.append((untested_file, entry["__line__"], f"'{where}' is listed as untested but no such task exists; remove it"))
        elif matches & ran:
            errors.append((untested_file, entry["__line__"], f"'{where}' is listed as untested but {len(matches & ran)} of its tasks ran; list only the tasks that don't run"))
        listed |= matches

    for key in sorted(tasks.keys() - ran - listed):
        errors.append((key[0], key[1], f"'{tasks[key][0]}' doesn't run in any Molecule scenario. Run it in a scenario, or list it in {untested_file} with a reason"))

    summary = ["## Task coverage", "", *table("", tasks.keys(), ran, listed)]
    # by role, playbook and shared task file, so one untested playbook doesn't hide the rest
    areas = {}
    for key in tasks:
        parts = key[0].split("/")
        areas.setdefault("/".join(parts[:2]) if parts[0] == "roles" else key[0], set()).add(key)
    rows = [table(area, keys, ran, listed)[-1] for area, keys in sorted(areas.items())]
    summary += ["", "<details><summary>By role and file</summary>", "", *table("Where", [], ran, listed)[:2], *rows, "", "</details>"]
    if errors:
        summary += ["", f"**{len(errors)} problem(s):**", ""]
        summary += [f"- `{path}:{line}` {message}" for path, line, message in errors[:30]]
        if len(errors) > 30:
            summary.append(f"- and {len(errors) - 30} more in the job log")
    print("\n".join(summary))
    for name in ("GITHUB_STEP_SUMMARY", "TASK_COVERAGE_REPORT"):
        if os.environ.get(name):
            with open(os.environ[name], "a", encoding="utf-8") as output:
                output.write("\n".join(summary) + "\n")

    for path, line, message in errors:
        print(f"::error file={path},line={line}::{message}")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:3]))
