# CLAUDE.md

Guidance for Claude Code (and other agents) working in **weisssrv-lib**, the
shared CI templates, Ansible collection, Terraform module shapes, gate scripts
and project CLI that the weisssrv family pins.

Nothing here is deployed. A change reaches the running cluster and both
templates through a release tag and a pin bump, so the blast radius of an edit
is every consumer, present and future.

[AGENTS.md](AGENTS.md) carries the standing rules for this repository — the
golden rules, the traps in editing CI templates and collection roles, the
comments policy, and where each canonical doc lives. Read it before changing
anything. It is not duplicated here.
