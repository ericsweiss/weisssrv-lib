#!/usr/bin/env python3
"""Read or reconcile ONE user's bcrypt password hash in AdGuardHome.yaml.

AdGuard Home has no password API (/control/profile/update takes only
name/language/theme), so the hash has to be reconciled in the config file.

Both actions resolve the target line by PARSING the YAML and locating the
`users:` entry whose `name` matches --user. A line-oriented pair (grep for the
last indented `password:` + a bare-regexp lineinfile) means "the last password
key in the file": with a second user, or another nested `password` key, it reads
and overwrites the wrong account on every deploy while never reconciling the
admin.

The file is rewritten ONE LINE at a time (temp file + atomic replace, preserving
mode/uid/gid), never re-emitted from parsed YAML: AdGuard Home owns this file at
runtime and a round-trip through a Python YAML emitter reformats everything it
did not write.

Usage:
    adguard-admin-hash.py --config PATH --user NAME read
    adguard-admin-hash.py --config PATH --user NAME reconcile   # password: stdin

read       prints the user's current hash (empty line if unset).
reconcile  reads the plaintext password from stdin (one trailing newline is
           stripped) and prints UNCHANGED when the stored hash already verifies
           against it, otherwise writes a fresh bcrypt hash and prints CHANGED.
           Compare those EXACTLY -- UNCHANGED contains CHANGED, so the usual
           `'CHANGED' in stdout` idiom matches both.

The password and the hash reach this script on stdin only: neither appears in
argv or the environment, so neither is readable in /proc by other local users.

Exit codes: 0 ok, 1 error (config unreadable/unparsable, user absent or
ambiguous, no password key, multi-line scalar, passlib missing, post-write
verification failed).
"""

import argparse
import os
import shutil
import sys
import tempfile

import yaml

BCRYPT_PREFIXES = ("$2a$", "$2b$", "$2y$")


def fail(message):
    print(f"ERROR: {message}", file=sys.stderr)
    sys.exit(1)


def find_password_node(config_path, username):
    """Return (key_node, value_node) of the target user's `password` entry.

    Returns None when the user exists but has no password key.
    """
    with open(config_path, encoding="utf-8") as handle:
        root = yaml.compose(handle)

    if root is None:
        fail(f"{config_path} is empty")

    users = None
    for key, value in getattr(root, "value", []):
        if key.value == "users":
            users = value
            break
    if users is None:
        fail(f"{config_path} has no top-level 'users' key")

    matches = []
    for user in users.value:
        entry = dict((k.value, (k, v)) for k, v in user.value)
        name = entry.get("name")
        if name is not None and name[1].value == username:
            matches.append(entry.get("password"))

    if not matches:
        fail(f"no user named {username!r} in {config_path}")
    if len(matches) > 1:
        fail(f"{len(matches)} users named {username!r} in {config_path}")
    return matches[0]


def read(config_path, username):
    node = find_password_node(config_path, username)
    print("" if node is None else node[1].value)


def replace_password_line(config_path, node, new_hash, username):
    key_node, value_node = node

    # A folded/multi-line scalar would make a one-line rewrite wrong. A bcrypt
    # hash is never written that way, so refuse rather than guess.
    if key_node.start_mark.line != value_node.end_mark.line:
        fail("password value spans multiple lines; refusing to rewrite")

    with open(config_path, encoding="utf-8") as handle:
        lines = handle.readlines()

    indent = " " * key_node.start_mark.column
    lines[key_node.start_mark.line] = f"{indent}password: {new_hash}\n"

    original = os.stat(config_path)
    directory = os.path.dirname(os.path.abspath(config_path))
    fd, tmp_path = tempfile.mkstemp(dir=directory, prefix=".AdGuardHome.yaml.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.writelines(lines)
        # Verify the rewritten file still parses AND carries the new hash on the
        # intended user before it becomes the live config.
        with open(tmp_path, encoding="utf-8") as handle:
            parsed = yaml.safe_load(handle)
        users = [u for u in parsed.get("users", []) if u.get("name") == username]
        if len(users) != 1 or users[0].get("password") != new_hash:
            fail("post-write verification failed; config left untouched")
        shutil.copystat(config_path, tmp_path)
        os.chown(tmp_path, original.st_uid, original.st_gid)
        os.replace(tmp_path, config_path)
    except BaseException:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise


def reconcile(config_path, username, password):
    try:
        from passlib.hash import bcrypt
    except ImportError:
        fail("passlib is not installed on this host")

    node = find_password_node(config_path, username)
    if node is None:
        fail(f"user {username!r} has no 'password' key to update")

    current = node[1].value or ""
    if current.startswith(BCRYPT_PREFIXES):
        try:
            # A malformed hash raises here and is treated as stale, so the next
            # branch re-hashes instead of leaving the account unreconcilable.
            if bcrypt.verify(password, current):
                print("UNCHANGED")
                return
        except ValueError:
            pass

    replace_password_line(config_path, node, bcrypt.using(rounds=10).hash(password), username)
    print("CHANGED")


def read_password():
    password = sys.stdin.read()
    if password.endswith("\n"):
        password = password[:-1]
    if not password:
        fail("no password on stdin")
    return password


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--user", required=True)
    parser.add_argument("action", choices=("read", "reconcile"))
    args = parser.parse_args()

    if not os.path.exists(args.config):
        fail(f"{args.config} does not exist")

    if args.action == "read":
        read(args.config, args.user)
    else:
        reconcile(args.config, args.user, read_password())


if __name__ == "__main__":
    main()
