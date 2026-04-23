import argparse
import csv
import importlib
import os
import time
import concurrent.futures
import traceback

from Agents.RandomAgent import RandomAgent as ra
from Managers.GameDirector import GameDirector

MATCHES_POR_PERFIL = {
    "quick": 60,
    "paper": 600,
}
MAX_ROUNDS_DEFAULT = 200
PORCENTAJE_WORKERS_DEFAULT = 0.95
PROGRESS_EVERY = 500

# Agentes a evaluar: (ruta_clase, params)
agentes_a_evaluar = [
    ("Agents.GPTAgent.GPTAgent", None),
    ("Agents.HeuristicAgent.HeuristicAgent", None),
]


def parse_args():
    parser = argparse.ArgumentParser(
        description="Benchmark de agentes contra 3 RandomAgent."
    )
    parser.add_argument(
        "--perfil",
        choices=tuple(MATCHES_POR_PERFIL.keys()),
        default="paper",
        help="Perfil de ejecución. 'paper' usa más partidas.",
    )
    parser.add_argument(
        "--n-matches",
        type=int,
        default=None,
        help="Partidas por posición (anula el perfil).",
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
        "--output",
        default="benchmark_vs_random_resultados.csv",
        help="Ruta del CSV de salida.",
    )
    return parser.parse_args()


def cargar_agente(ruta_clase):
    modulo, clase = ruta_clase.rsplit(".", 1)
    mod = importlib.import_module(modulo)
    return getattr(mod, clase)

def crear_clase_agente_configurada(agente_clase, model, **kwargs):
    class AgenteConfigurado(agente_clase):
        def __init__(self, agent_id):
            super().__init__(agent_id, model=model, **kwargs)

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


def simulate_match(position, agente_alumno_clase, max_rounds, params=None):
    try:
        if params is not None:
            if isinstance(params, (list, tuple)):
                agente_final = crear_clase_agente_configurada_lista(agente_alumno_clase, params)
            elif isinstance(params, dict):
                agente_final = crear_clase_agente_configurada(agente_alumno_clase, **params)
            else:
                raise TypeError("params debe ser lista/tupla o dict")
        else:
            agente_final = agente_alumno_clase

        match_agents = [ra, ra, ra]
        match_agents.insert(position, agente_final)

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
        rank = 4  # Default rank if player not found
        for idx, (jugador, _) in enumerate(ordenados, start=1):
            if jugador == agent_id:
                rank = idx
                break

        return (victory, points, rank)
    except Exception as e:
        print("\n=== EXCEPCIÓN EN simulate_match ===")
        print("Agente clase:", agente_alumno_clase, "name:", getattr(agente_alumno_clase, "__name__", None))
        print("Posición:", position, "params type:", type(params), "params:", params)
        print("Exception:", repr(e))
        print(traceback.format_exc())
        return (0, 0, 4)


if __name__ == "__main__":
    args = parse_args()
    n_matches = args.n_matches if args.n_matches is not None else MATCHES_POR_PERFIL[args.perfil]
    porcentaje_workers = args.workers_ratio

    if n_matches <= 0:
        raise ValueError("n_matches debe ser > 0")
    if args.max_rounds <= 0:
        raise ValueError("max_rounds debe ser > 0")
    if not 0 < porcentaje_workers <= 1:
        raise ValueError("workers_ratio debe estar en (0, 1]")

    agentes_cargados = []
    for ruta_agente, params_agente in agentes_a_evaluar:
        try:
            agentes_cargados.append((ruta_agente, cargar_agente(ruta_agente), params_agente))
        except Exception as e:
            print(f"[WARN] No se pudo cargar {ruta_agente}: {e}")

    if not agentes_cargados:
        raise RuntimeError("No hay agentes válidos para evaluar.")

    total_workers = os.cpu_count() or 1
    workers_a_utilizar = max(1, int(total_workers * porcentaje_workers))
    total_partidas = len(agentes_cargados) * 4 * n_matches

    print(f"Workers a utilizar ({porcentaje_workers*100:.1f}%): {workers_a_utilizar}")
    print(f"Perfil: {args.perfil} | Partidas por posición: {n_matches} | Max rounds: {args.max_rounds}")
    print(f"Agentes a evaluar: {[ruta for ruta, _, _ in agentes_cargados]}")
    print(f"Total de partidas a simular: {total_partidas}\n")

    start_time = time.time()
    resumen_csv = []
    partidas_completadas = 0

    for ruta_agente, agente_alumno, params_agente in agentes_cargados:
        agent_name = agente_alumno.__name__
        agent_key = etiqueta_agente(ruta_agente, params_agente)
        print(f"\n==== Evaluando agente: {agent_name} ====\n")

        partial_start_time = time.time()
        position_results = {pos: 0 for pos in range(4)}
        total_wins = 0
        total_points = 0
        total_rank = 0

        with concurrent.futures.ProcessPoolExecutor(max_workers=workers_a_utilizar) as executor:
            for pos in range(4):
                futures = [
                    executor.submit(simulate_match, pos, agente_alumno, args.max_rounds, params_agente)
                    for _ in range(n_matches)
                ]
                for f in concurrent.futures.as_completed(futures):
                    victory, points, rank = f.result()
                    position_results[pos] += victory
                    total_wins += victory
                    total_points += points
                    total_rank += rank
                    partidas_completadas += 1
                    if partidas_completadas % PROGRESS_EVERY == 0 or partidas_completadas == total_partidas:
                        print(
                            f"Progreso global: {partidas_completadas}/{total_partidas} "
                            f"({partidas_completadas/total_partidas:.2%})"
                        )

        for pos in range(4):
            wins = position_results[pos]
            percentage = 100 * wins / n_matches
            print(f"- Posición {pos+1}: {wins} victorias de {n_matches} partidas ({percentage:.2f}%)")

        total_partidas = n_matches * 4
        ratio_victorias = total_wins / total_partidas
        media_puntos = total_points / total_partidas
        puesto_medio = total_rank / total_partidas

        print(f"\nTotal para {agent_name}: {total_wins} victorias de {total_partidas} partidas ({ratio_victorias:.2%})")
        print(f"Media de puntos: {media_puntos:.2f}")
        print(f"Puesto medio: {puesto_medio:.2f}")

        resumen_csv.append(
            [
                agent_key,
                total_wins,
                total_points,
                total_partidas,
                f"{ratio_victorias:.4f}",
                f"{media_puntos:.2f}",
                f"{puesto_medio:.2f}",
                args.perfil,
                n_matches,
                args.max_rounds,
            ]
        )

        partial_end_time = time.time()
        horas, resto = divmod(partial_end_time - partial_start_time, 3600)
        minutos, segundos = divmod(resto, 60)
        print(f"Tiempo parcial: {int(horas)}h {int(minutos)}m {int(segundos)}s\n")

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
                "PartidasPorPosicion",
                "MaxRounds",
            ]
        )
        for row in resumen_csv:
            writer.writerow(row)

    print(f"\nResultados guardados en: {csv_filename}")

    end_time = time.time()
    horas, resto = divmod(end_time - start_time, 3600)
    minutos, segundos = divmod(resto, 60)
    print(f"\nTiempo total: {int(horas)}h {int(minutos)}m {int(segundos)}s\n")
