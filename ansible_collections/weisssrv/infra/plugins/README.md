# plugins/

Empty scaffold. Nothing here yet — every role in this collection is built from
`ansible.builtin` plus the collections pinned in `galaxy.yml`.

Add a plugin only when a role needs behavior a task cannot express. Ansible
loads plugins from the type-named subdirectory, so the file must land in
`modules/`, `filter/`, `lookup/`, `action/`, `module_utils/`, … and is then
addressed by FQCN — `weisssrv.infra.<plugin_name>`, from anywhere in a
playbook, not just from this collection's roles.

Two consequences of that reach:

- A plugin is public API the moment it ships. Renaming or removing one is a
  MAJOR change under [docs/VERSIONING.md](../../../../docs/VERSIONING.md).
- FQCN resolution needs the collection on Ansible's collections path, which is
  what `molecule-shared/base.yml` wires for the role scenarios (see the
  collection [README](../README.md)).
