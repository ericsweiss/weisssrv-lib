# plugins/

Empty scaffold. Every role in this collection is built from `ansible.builtin`
plus the collections pinned in `galaxy.yml`.

Add a plugin only when a role needs behavior a task cannot express. Ansible
loads plugins from the type-named subdirectory (`modules/`, `filter/`,
`lookup/`, `action/`, `module_utils/`, …); the plugin is then addressable as
`weisssrv.infra.<plugin_name>` from any playbook, and is public API from its
first release — see [docs/VERSIONING.md](../../../../docs/VERSIONING.md).
