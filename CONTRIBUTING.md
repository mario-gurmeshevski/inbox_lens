# Contributing

## Development setup

  ```bash
    make dev-install # For Mac/Linux
    ./commands.ps1 dev-install # For Windows
  ```

Formatting and linting (Ruff for Python, djlint for templates) are enforced in CI via the **Lint** GitHub Actions workflow (`.github/workflows/lint.yaml`), which runs `make lint` on every pull request.

## Python

- Format/lint Python with Ruff:
  ```bash
    make format # For Mac/Linux
  ./commands.ps1 format # For Windows

  make lint # For Mac/Linux
  ./commands.ps1 lint # For Windows
  ```
- Run tests:
  ```bash
    make test # For Mac/Linux
  ./commands.ps1 test # For Windows

  make test-cov # For Mac/Linux
  ./commands.ps1 test-cov # For Windows
  ```

## HTML / Jinja2 templates

Templates live in `src/web/templates/` and are formatted with [djlint](https://www.djlint.com/) (configured under `[tool.djlint]` in `pyproject.toml`, using the `jinja` profile).

```bash
   make format # For Mac/Linux
  ./commands.ps1 format # For Windows

  make lint # For Mac/Linux
  ./commands.ps1 lint # For Windows
```

### Why not just use your editor's HTML formatter?

Jinja2 `{% %}` / `{{ }}` tags are not valid HTML, so editors' built-in HTML formatters (Prettier, the VSCode/IntelliJ HTML formatter, Zed's prettier) mangle them — e.g. `{% if timezone == tz_id %}` gets split into `=""` and `="tz_id"`. That is why this project formats templates with djlint instead.

The **Lint** GitHub Actions workflow runs `make lint` (Ruff + `djlint --check`) on every pull request. If it fails, run `make format` locally and push again. This catches mangled or unformatted templates before they merge, regardless of which editor a contributor uses.

## Editor-specific setup

Format-on-save is inherently editor-specific, so it is configured per editor below. The CLI (`make format`) works the same in every editor; the Lint workflow enforces it on pull requests.

### VS Code / Cursor

A committed `.vscode/settings.json` disables HTML format-on-save for this workspace, so saving a template no longer mangles it. No extension is required.

Optional: install the [djlint extension](https://marketplace.visualstudio.com/items?itemName=monosans.djlint) (`monosans.djlint`) and set it as the default formatter for HTML to get format-on-save with djlint.

### JetBrains (WebStorm / PyCharm)

- Do **not** run _Reformat Code_ on `.html` templates — IntelliJ's HTML formatter does not understand Jinja.
- Turn off "Reformat on save" for HTML (_Settings → Tools → Actions on Save_).
- Optional: add djlint as an **External Tool** (Settings → Tools → External Tools) with program `.venv/bin/djlint` and arguments `--reformat $FilePath$`, then bind it to a key. Indentation follows the committed `.editorconfig`.

### Neovim

With [conform.nvim](https://github.com/stevearc/conform.nvim):

```lua
require("conform").setup({
  formatters_by_ft = {
    html = { "djlint" },
    htmldjango = { "djlint" },
  },
  format_on_save = {
    timeout_ms = 500,
    lsp_fallback = false,
  },
})
```

djlint must be on `$PATH` (or point `formatters.djlint.command` at
`.venv/bin/djlint`).

### Vim

Use djlint as the format program:

```vim
autocmd FileType html setlocal formatprg=djlint\ --reformat\ -
```

Then `gq` reformats. Ensure `djlint` is on `$PATH`.

### Zed / other editors

No first-party djlint integration. Disable HTML format-on-save for this project so the built-in prettier does not mangle Jinja, and rely on `make format` plus the Lint workflow on pull requests.
