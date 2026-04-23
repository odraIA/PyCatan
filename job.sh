#!/bin/bash
#SBATCH --mem=50G               # Total Memory
#SBATCH -J benchcat             # Job name
#SBATCH -N 1                    # Amount of nodes
#SBATCH -G 0                    # Num of GPUs
#SBATCH -w vrhpc4.dsic.upv.es   # Node to run
#SBATCH --time=3-00:00          # Time, Days-HH:MM format
#SBATCH --cpus-per-task=4       # Total cores
#SBATCH -o %x_%j.out            # STDOUT en el directorio desde donde se lanza sbatch
#SBATCH -e %x_%j.err            # STDERR en el directorio desde donde se lanza sbatch

set -euo pipefail

# Crear directorios necesarios
mkdir -p logs runs

# Configuración de entorno
export TOKENIZERS_PARALLELISM=false

# Crear y activar entorno virtual
VENV_DIR=".venv_bench"
python3 -m venv "$VENV_DIR"
source "$VENV_DIR/bin/activate"

# Instalar dependencias desde requirements.txt
python -m pip install --upgrade pip
if ! python -m pip install -r requirements.txt; then
  echo "[WARN] Instalación directa falló. Reintentando sin dependencias con rutas locales (file://)."
  CLEAN_REQ="$(mktemp)"
  grep -vE ' @ file://|^asttokens @|^comm @|^debugpy @|^decorator @|^executing @|^ipykernel @|^ipython @|^ipython_pygments_lexers @|^jedi @|^jupyter_client @|^jupyter_core @|^matplotlib-inline @|^nest_asyncio @|^packaging @|^parso @|^pexpect @|^platformdirs @|^prompt_toolkit @|^psutil @|^ptyprocess @|^pure_eval @|^Pygments @|^python-dateutil @|^pyzmq @|^six @|^stack_data @|^tornado @|^traitlets @|^typing_extensions @|^wcwidth @' requirements.txt > "$CLEAN_REQ"
  python -m pip install -r "$CLEAN_REQ"
  rm -f "$CLEAN_REQ"
fi

echo "==== ENV CHECK ===="
echo "HOSTNAME=$(hostname)"
echo "JOBID=$SLURM_JOB_ID"
echo "DATE=$(date)"
echo "PWD=$(pwd)"
python --version
echo "VENV=$VIRTUAL_ENV"

# ============================================================
# Benchmarks PyCatan
# ============================================================

TS="$(date +%Y%m%d_%H%M%S)"
OUT_DIR="runs/benchmarks_${SLURM_JOB_ID:-local}_${TS}"
mkdir -p "$OUT_DIR"

echo "==== Running benchmark_vs_agentes_estandar.py ===="
python -u benchmark_estandar.py \

echo "Resultados guardados en: ${OUT_DIR}"
ls -lh "${OUT_DIR}"
echo "================ BENCHMARK JOB FINISHED ================"
