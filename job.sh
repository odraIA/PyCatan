#!/bin/bash
#SBATCH --mem=50G               # Total Memory
#SBATCH -J benchcat             # Job name
#SBATCH -N 1                    # Amount of nodes
#SBATCH -G 0                    # Num of GPUs
#SBATCH -w vrhpc4.dsic.upv.es   # Node to run
#SBATCH --time=3-00:00          # Time, Days-HH:MM format
#SBATCH --cpus-per-task=4       # Total cores
#SBATCH -o logs/%x_%j.log       # STDOUT (Usando la carpeta logs para consistencia)

set -u

# Crear directorios necesarios
mkdir -p logs runs

# Configuración de entorno
export TOKENIZERS_PARALLELISM=false

# Crear y activar entorno virtual
VENV_DIR=".venv_bench"
python3 -m venv "$VENV_DIR"
source "$VENV_DIR/bin/activate"

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

echo "==== Running benchmark_vs_random.py ===="
python -u benchmark_vs_random.py \
  --perfil paper \
  --workers-ratio 0.95 \
  --output "${OUT_DIR}/benchmark_vs_random_resultados.csv"

echo "==== Running benchmark_vs_agentes_estandar.py ===="
python -u benchmark_vs_agentes_estandar.py \
  --perfil paper \
  --workers-ratio 0.95 \
  --output "${OUT_DIR}/benchmark_vs_estandar_resultados.csv"

echo "Resultados guardados en: ${OUT_DIR}"
ls -lh "${OUT_DIR}"
echo "================ BENCHMARK JOB FINISHED ================"
