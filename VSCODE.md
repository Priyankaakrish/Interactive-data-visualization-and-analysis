# Running this project in VS Code

The `.vscode/` folder is already configured — interpreter paths, test
discovery, debug configurations and tasks. Opening the folder is most of the
setup.

## First time

**1. Open the project folder**, not its parent:

```
File → Open Folder… → online-retail-bi
```

The folder containing `config.yaml` must be the workspace root. Opening a level
higher is the single most common cause of `ModuleNotFoundError: No module
named 'src'`.

**2. Install the recommended extensions.** VS Code will prompt on first open.
If it does not: Extensions panel → filter `@recommended`. The essential one is
**Python** (`ms-python.python`); the rest are convenience.

**3. Select the interpreter.** `Ctrl+Shift+P` → *Python: Select Interpreter* →
pick Python 3.9 or newer. If you want a virtual environment:

```powershell
python -m venv .venv
```

Then re-run *Select Interpreter* and choose the one under `.venv`. VS Code
remembers it and activates it in every new terminal.

**4. Install dependencies.** `Ctrl+Shift+P` → *Tasks: Run Task* →
**Install dependencies**. Or in the terminal (`` Ctrl+` ``):

```powershell
pip install -r requirements.txt
```

## Running it

Three equivalent ways, in order of how much you get back:

| How | What it gives you |
|---|---|
| **F5** | Runs under the debugger — breakpoints anywhere in `src/` |
| `Ctrl+Shift+B` | Runs the default build task, output in a dedicated panel |
| Terminal: `python -m src.run_pipeline` | Plain run |

`F5` uses the **Run pipeline** configuration in `launch.json`. Three others are
available from the Run and Debug dropdown: skipping dashboards, generating
sample data, and debugging whichever test file is open.

## Debugging: where to put a breakpoint

The pipeline is seven stages in `src/run_pipeline.py`, and each hands off to one
module. Useful places to stop:

| Question | Breakpoint |
|---|---|
| What did the source actually contain? | `src/ingest.py`, in `profile()` |
| Why was this row dropped? | `src/clean.py`, in `clean_transactions()` |
| Why did the load abort? | `src/validate.py`, in `run_validation()` |
| Why is this KPI wrong? | `src/analytics.py`, in `load_all()` |

`justMyCode` is set to `false`, so you can also step into pandas and SQLAlchemy
when you need to.

## Running the tests

Click the **flask icon** in the sidebar. VS Code discovers all 29 tests from
`pytest`, and you can run or debug any single one by clicking the arrow beside
it. That is much faster than re-running the whole pipeline when you are changing
a KPI calculation.

`Ctrl+Shift+P` → *Tasks: Run Task* → **Run tests** runs them all in a terminal.

## Viewing the output

**The dashboards are ~5 MB HTML files.** VS Code's built-in preview struggles at
that size. Right-click `outputs/retail_dashboard.html` → *Reveal in File
Explorer* → open it in a browser.

**The CSVs** in `data/processed/` open directly in the editor. With the Rainbow
CSV extension they are colour-aligned and you can run queries against them.

**The Excel workbook** needs Excel or LibreOffice — VS Code will not render it.

## Browsing the database

The default `database.mode: embedded` starts a PostgreSQL that lives and dies
with the pipeline process, so external tools cannot connect to it.

To browse the warehouse in the SQLTools sidebar, switch to a real server:

1. Install PostgreSQL locally and `CREATE DATABASE retail_bi;`
2. In `config.yaml`, set `database.mode: external`
3. Run the pipeline once to populate it
4. Click the SQLTools icon → the pre-configured **Retail BI** connection

Then you can browse `core`, `analytics` and `monitoring`, and run ad-hoc queries
against the views the dashboards use.

## The .env file

`.env` in the project root is read automatically by the Python extension. Two
settings are worth knowing about, both commented out by default:

```bash
# PGDATA_DIR=C:\pgdata\retail_bi
# DATABASE_URL=postgresql+psycopg2://postgres:postgres@localhost:5432/retail_bi
```

Uncomment `PGDATA_DIR` if the project ever ends up in OneDrive or on a network
share — PostgreSQL needs to `chmod` its own data directory and those locations
forbid it. `DATABASE_URL` overrides `config.yaml` entirely, which is handy for
pointing at a different server without editing tracked files.

## Common problems

**`ModuleNotFoundError: No module named 'src'`**
The workspace root is wrong, or you ran `python src/run_pipeline.py` instead of
`python -m src.run_pipeline`. The module form is what puts the project root on
the path.

**Tests are not discovered**
`Ctrl+Shift+P` → *Python: Configure Tests* → pytest → `tests`. Then
*Test: Refresh Tests*. Usually it means no interpreter is selected yet.

**Terminal says `pip` or `python` is not recognised (Windows)**
Use `py -m pip install -r requirements.txt` and `py -m src.run_pipeline`, or
reinstall Python with "Add to PATH" ticked.

**The first run seems frozen**
It is parsing 1.07M rows out of a 45 MB Excel file — about three minutes. The
result is cached to `data/raw/.cache/`, so later runs start in under a second.
