# Role: qol

Opinionated shell + editor tooling for one admin user: zsh with Oh My Zsh
(pinned commit), Neovim with Vundle, and the fzf/ripgrep/fd CLI set.

Purely a convenience layer — nothing else in the collection depends on it, so
compose it into a play only where you want it.

## Variables

| Variable | Default | Meaning |
|---|---|---|
| `qol_admin_user` | `{{ admin_user }}` | User whose dotfiles the role owns. `root` self-skips every user-scoped task (packages still install). |
| `qol_packages` | zsh, neovim, fzf, ripgrep, fd-find | apt packages |
| `qol_omz_commit` | pinned sha | Oh My Zsh has no release tags, so the clone is pinned to a commit and self-update is disabled in `.zshrc` |
| `qol_omz_theme` | `risto` | `ZSH_THEME` |
| `qol_omz_plugins` | 15 bundled plugins | **OMZ-bundled only** — the role templates the `plugins=()` array but never clones external plugins, so listing e.g. `zsh-autosuggestions` prints "plugin not found" at every shell start |
| `qol_vundle_version` | `v0.10.2` | Vundle tag |
| `qol_nvim_plugins` | vim-fugitive, vim-polyglot, onedark.vim | Vundle plugin list |
| `qol_nvim_colorscheme` | `onedark` | applied with `silent!`, so a scheme without its plugin degrades instead of erroring |

`qol_admin_user` is deliberately an alias of the inventory-wide `admin_user`:
this role has a `meta` dependency on `weisssrv.infra.base`, and the two must
agree on the user.

Vundle itself is pinned, but `+PluginInstall` clones `qol_nvim_plugins` at their
current HEAD — plugin *contents* are not reproducible across hosts or time. Pin
via forks or explicit checkouts if that ever matters.

## Files it owns

`~/.zshrc`, `~/.zprofile`, `~/.alias.zsh`, `~/.local.zsh` (created once, never
overwritten), `~/.config/nvim/init.vim`, `~/.oh-my-zsh/`, `~/.vim/bundle/`.

## Idempotency

- Oh My Zsh converges to `qol_omz_commit` via the git module, and the network is
  only touched when the local HEAD differs from the pin (or the clone is
  missing) — the role stays offline-safe after first install.
- A legacy `--depth=1` clone left by upstream's `install.sh` is removed once, so
  the pinned commit is fetchable.
- `+PluginInstall` is guarded by a marker keyed to the **sha1 of the plugin
  list**, so adding a plugin re-runs it (a fixed marker filename silently
  no-op'd) while a converged host does not.
- The login shell is set unconditionally; the `user` module reports changed only
  on a real change.
