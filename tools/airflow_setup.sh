#!/usr/bin/env bash
# Airflow local standalone for the Retail BI Platform (runs in WSL2).
# Ubuntu 26.04 ships Python 3.14; Airflow supports <=3.12, so uv provides 3.12.
set -euo pipefail

PROJECT="/mnt/c/Projects/Interactive Data visualization"
AIRFLOW_VERSION="2.10.5"
PY="3.12"
VENV="$HOME/.venvs/airflow-retailbi"

export AIRFLOW_HOME="$HOME/airflow-retailbi"
export AIRFLOW__CORE__DAGS_FOLDER="$PROJECT/dags"
export AIRFLOW__CORE__LOAD_EXAMPLES=False
export PYTHONPATH="$PROJECT:${PYTHONPATH:-}"

if ! command -v uv >/dev/null 2>&1; then
    curl -LsSf https://astral.sh/uv/install.sh | sh
fi
export PATH="$HOME/.local/bin:$PATH"

if [ ! -d "$VENV" ]; then
    uv python install "$PY"
    uv venv --python "$PY" "$VENV"
fi
source "$VENV/bin/activate"

if ! python -c "import airflow" 2>/dev/null; then
    uv pip install "apache-airflow==${AIRFLOW_VERSION}" \
      --constraint "https://raw.githubusercontent.com/apache/airflow/constraints-${AIRFLOW_VERSION}/constraints-${PY}.txt"
    uv pip install psycopg2-binary pandas pyyaml
fi

mkdir -p "$AIRFLOW_HOME"
cat > "$AIRFLOW_HOME/env.sh" <<INNER
export PATH="\$HOME/.local/bin:\$PATH"
export AIRFLOW_HOME="$AIRFLOW_HOME"
export AIRFLOW__CORE__DAGS_FOLDER="$AIRFLOW__CORE__DAGS_FOLDER"
export AIRFLOW__CORE__LOAD_EXAMPLES=False
export PYTHONPATH="$PYTHONPATH"
source "$VENV/bin/activate"
INNER

if [ "${1:-init}" = "start" ]; then
    echo "UI: http://localhost:8080"
    exec airflow standalone
fi

airflow db migrate
airflow dags list 2>/dev/null | grep retail_bi_incremental \
    && echo "DAG found" \
    || airflow dags list-import-errors
echo "Now run: bash tools/airflow_setup.sh start"
