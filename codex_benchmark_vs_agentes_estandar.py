import argparse
import math
import os
import time
import concurrent.futures
import importlib
import itertools
import csv
import traceback
from Managers.GameDirector import GameDirector

RUTAS_AGENTES_ESTANDAR = [
    "Agents.RandomAgent.RandomAgent",
    "Agents.AdrianHerasAgent.AdrianHerasAgent",
    "Agents.AlexPastorAgent.AlexPastorAgent",
    "Agents.AlexPelochoJaimeAgent.AlexPelochoJaimeAgent",
    "Agents.CarlesZaidaAgent.CarlesZaidaAgent",
    "Agents.CrabisaAgent.CrabisaAgent",
    "Agents.EdoAgent.EdoAgent",
    "PabloAleixAlexAgent.PabloAleixAlexAgent",
    "SigmaAgent.SigmaAgent",
    "TristanAgent.TristanAgent",
]

TARGET_MATCHES_POR_AGENTE = {
    "quick": 300,
    "paper": 3000,
}
MAX_ROUNDS_DEFAULT = 200
PORCENTAJE_WORKERS_DEFAULT = 0.95
BATCH_SIZE_DEFAULT = 10000
PROGRESS_EVERY = 5000

# Agentes a evaluar: (ruta_clase, params)
agentes_a_evaluar = [
    ("Agents.POLIGPTAgent.GPTAgent", None),
    ("Agents.HeuristicAgent.HeuristicAgent", None),
    ("Agents.OllamaAgent.OllamaAgent", None),
]


def parse_args():
    parser = argparse.ArgumentParser(
        description="Benchmark de agentes contra permutaciones de agentes estándar."
    )
    parser.add_argument(
        "--perfil",
        choices=tuple(TARGET_MATCHES_POR_AGENTE.keys()),
        default="paper",
        help="Perfil de ejecución. 'paper' intenta mayor volumen total por agente.",
    )
    parser.add_argument(
        "--n-matches-per-permutation",
        type=int,
        default=None,
        help="Partidas por cada permutación y posición (anula el cálculo por objetivo).",
    )
    parser.add_argument(
        "--target-matches-per-agent",
        type=int,
        default=None,
        help="Objetivo aproximado de partidas por agente (anula el perfil si no usas --n-matches-per-permutation).",
    )
    parser.add_argument(
        "--max-rounds",
        type=int,
        default=MAX_ROUNDS_DEFAULT,
        help=f"Máximo de rondas por partida (default={MAX_ROUNDS_DEFAULT}).",
    )
    parser.add_argument(
        "--workers-ratio",
        type=float,
        default=PORCENTAJE_WORKERS_DEFAULT,
        help=f"Fracción de CPU a usar (default={PORCENTAJE_WORKERS_DEFAULT}).",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=BATCH_SIZE_DEFAULT,
        help=f"Tamaño de lote de futures (default={BATCH_SIZE_DEFAULT}).",
    )
    parser.add_argument(
        "--output",
        default="benchmark_vs_estandar_resultados.csv",
        help="Ruta del CSV de salida.",
    )
    return parser.parse_args()

def cargar_agente(ruta_clase):
    modulo, clase = ruta_clase.rsplit(".", 1)
    mod = importlib.import_module(modulo)
    return getattr(mod, clase)

def crear_clase_agente_configurada(agente_clase, **kwargs):
    class AgenteConfigurado(agente_clase):
        def __init__(self, agent_id):
            super().__init__(agent_id, **kwargs)

    AgenteConfigurado.__name__ = f"{agente_clase.__name__}_ConfiguradoDict"
    return AgenteConfigurado

def crear_clase_agente_configurada_lista(agente_clase, params_list):
    class AgenteConfigurado(agente_clase):
        def __init__(self, agent_id):
            super().__init__(agent_id, *params_list)

    AgenteConfigurado.__name__ = f"{agente_clase.__name__}_ConfiguradoLista"
    return AgenteConfigurado


def etiqueta_agente(ruta_agente, params):
    if params is None:
        return ruta_agente
    return f"{ruta_agente}{params}"


def cargar_agentes_disponibles(rutas_agente):
    clases = []
    for ruta_agente in rutas_agente:
        try:
            clases.append(cargar_agente(ruta_agente))
        except Exception as e:
            print(f"[WARN] No se pudo cargar agente estándar {ruta_agente}: {e}")
    return clases


def simulate_match(opponents, position, agente_alumno_clase, max_rounds, params=None):
    try:
        if params is not None:
            if isinstance(params, (list, tuple)):
                agente_alumno_class = crear_clase_agente_configurada_lista(agente_alumno_clase, params)
            elif isinstance(params, dict):
                agente_alumno_class = crear_clase_agente_configurada(agente_alumno_clase, **params)
            else:
                raise TypeError("params debe ser lista/tupla o dict")
        else:
            agente_alumno_class = agente_alumno_clase

        match_agents = list(opponents)
        match_agents.insert(position, agente_alumno_class)

        game_director = GameDirector(agents=match_agents, max_rounds=max_rounds, store_trace=False)
        game_trace = game_director.game_start(print_outcome=False)

        last_round = max(game_trace["game"].keys(), key=lambda r: int(r.split("_")[-1]))
        last_turn = max(game_trace["game"][last_round].keys(), key=lambda t: int(t.split("_")[-1].lstrip("P")))
        victory_points = game_trace["game"][last_round][last_turn]["end_turn"]["victory_points"]

        agent_id = f"J{position}"
        points = int(victory_points[agent_id])
        winner = max(victory_points, key=lambda player: int(victory_points[player]))
        victory = 1 if winner == agent_id else 0

        ordenados = sorted(victory_points.items(), key=lambda item: int(item[1]), reverse=True)
        rank = 4  # Default rank if agent not found
        for idx, (jugador, _) in enumerate(ordenados, start=1):
            if jugador == agent_id:
                rank = idx
                break

        return (victory, points, rank)
    except Exception as e:
        print("Exception:", repr(e))
        print(traceback.format_exc())
        return (0, 0, 4)

if __name__ == "__main__":
    args = parse_args()
    porcentaje_workers = args.workers_ratio
    batch_size = args.batch_size

    if args.max_rounds <= 0:
        raise ValueError("max_rounds debe ser > 0")
    if batch_size <= 0:
        raise ValueError("batch_size debe ser > 0")
    if not 0 < porcentaje_workers <= 1:
        raise ValueError("workers_ratio debe estar en (0, 1]")
    if args.target_matches_per_agent is not None and args.target_matches_per_agent <= 0:
        raise ValueError("target_matches_per_agent debe ser > 0")

    agentes_cargados = []
    for ruta_agente, params_agente in agentes_a_evaluar:
        try:
            agentes_cargados.append((ruta_agente, cargar_agente(ruta_agente), params_agente))
        except Exception as e:
            print(f"[WARN] No se pudo cargar {ruta_agente}: {e}")

    if not agentes_cargados:
        raise RuntimeError("No hay agentes válidos para evaluar.")

    benchmark_agents = cargar_agentes_disponibles(RUTAS_AGENTES_ESTANDAR)
    if len(benchmark_agents) < 3:
        raise RuntimeError("Se necesitan al menos 3 agentes estándar cargables para generar permutaciones.")

    permutations = list(itertools.permutations(benchmark_agents, 3))
    if not permutations:
        raise RuntimeError("No se han podido generar permutaciones de agentes estándar.")

    if args.n_matches_per_permutation is not None:
        n_matches_per_permutation = args.n_matches_per_permutation
    else:
        target_matches = (
            args.target_matches_per_agent
            if args.target_matches_per_agent is not None
            else TARGET_MATCHES_POR_AGENTE[args.perfil]
        )
        n_matches_per_permutation = max(1, math.ceil(target_matches / (len(permutations) * 4)))

    if n_matches_per_permutation <= 0:
        raise ValueError("n_matches_per_permutation debe ser > 0")

    results = {
        etiqueta_agente(ruta_agente, params): {'wins': 0, 'points': 0, 'rank_sum': 0}
        for ruta_agente, _, params in agentes_cargados
    }

    total_workers = os.cpu_count() or 1
    workers_a_utilizar = max(1, int(total_workers * porcentaje_workers))
    print(f"Workers a utilizar ({porcentaje_workers*100:.1f}%): {workers_a_utilizar}")

    start_time = time.time()

    partidas_por_agente = len(permutations) * 4 * n_matches_per_permutation
    total_matches = len(agentes_cargados) * partidas_por_agente
    print(
        f"Perfil: {args.perfil} | Partidas por permutación y posición: {n_matches_per_permutation} "
        f"| Max rounds: {args.max_rounds}"
    )
    print(f"Agentes a evaluar: {[ruta for ruta, _, _ in agentes_cargados]}")
    print(f"Agentes estándar cargados: {len(benchmark_agents)}")
    print(f"Permutaciones de rivales: {len(permutations)}")
    print(f"Partidas totales por agente evaluado: {partidas_por_agente}")
    print(f"Total de partidas a simular: {total_matches}")

    matches_done = 0
    futures_batch = []
    resumen_csv = []

    with concurrent.futures.ProcessPoolExecutor(max_workers=workers_a_utilizar) as executor:
        def task_generator():
            for agente_path, agente_cls, params in agentes_cargados:
                for perm in permutations:
                    for pos in range(4):
                        for _ in range(n_matches_per_permutation):
                            yield (list(perm), pos, agente_cls, params, agente_path)


        for perm, pos, agente_cls, params, agente_path in task_generator():
            fut = executor.submit(simulate_match, perm, pos, agente_cls, args.max_rounds, params=params)
            futures_batch.append((fut, etiqueta_agente(agente_path, params)))


            if len(futures_batch) >= batch_size:
                futures_dict = {fut: agente_alumno for fut, agente_alumno in futures_batch}
                for fut in concurrent.futures.as_completed(futures_dict):
                    victory, points, rank = fut.result()
                    agent = futures_dict[fut]
                    results[agent]['wins'] += victory
                    results[agent]['points'] += points
                    results[agent]['rank_sum'] += rank
                    matches_done += 1
                    if matches_done % PROGRESS_EVERY == 0 or matches_done == total_matches:
                        print(f"Progreso: {matches_done}/{total_matches} partidas completadas ({matches_done/total_matches:.2%})")
                futures_batch = []

        if futures_batch:
            futures_dict = {fut: agente_alumno for fut, agente_alumno in futures_batch}
            for fut in concurrent.futures.as_completed(futures_dict):
                victory, points, rank = fut.result()
                agent = futures_dict[fut]
                results[agent]['wins'] += victory
                results[agent]['points'] += points
                results[agent]['rank_sum'] += rank
                matches_done += 1
                if matches_done % PROGRESS_EVERY == 0 or matches_done == total_matches:
                    print(f"Progreso: {matches_done}/{total_matches} partidas completadas ({matches_done/total_matches:.2%})")

    print("\nResultados ordenados por ratio de victorias:")

    resumen = []
    for agente, stats in results.items():
        nombre = agente
        wins = stats['wins']
        points = stats['points']
        rank_sum = stats['rank_sum']
        ratio = wins / partidas_por_agente
        avg_points = points / partidas_por_agente
        puesto_medio = rank_sum / partidas_por_agente
        resumen.append((nombre, wins, points, partidas_por_agente, ratio, avg_points, puesto_medio))

    resumen.sort(key=lambda x: x[4], reverse=True)

    for nombre, wins, points, total, ratio, avg_points, puesto_medio in resumen:
        print(f"{nombre}: {wins} victorias, {points} puntos en {total} partidas — "
              f"Ratio: {ratio:.2%}, Media puntos: {avg_points:.2f}, Puesto medio: {puesto_medio:.2f}")
        resumen_csv.append(
            [
                nombre,
                wins,
                points,
                total,
                f"{ratio:.4f}",
                f"{avg_points:.2f}",
                f"{puesto_medio:.2f}",
                args.perfil,
                n_matches_per_permutation,
                args.max_rounds,
            ]
        )

    # Guardar CSV
    csv_filename = args.output
    with open(csv_filename, mode='w', newline='') as csvfile:
        writer = csv.writer(csvfile)
        writer.writerow(
            [
                "Agente",
                "Victorias",
                "Puntos",
                "Partidas",
                "Ratio Victorias",
                "Media Puntos",
                "Puesto Medio",
                "Perfil",
                "PartidasPorPermutacionPosicion",
                "MaxRounds",
            ]
        )
        writer.writerows(resumen_csv)

    print(f"\n Resultados guardados en: {csv_filename}")

    end_time = time.time()
    horas, resto = divmod(end_time - start_time, 3600)
    minutos, segundos = divmod(resto, 60)
    print(f"\n Tiempo total: {int(horas)}h {int(minutos)}m {int(segundos)}s")
