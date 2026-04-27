import argparse
import importlib
import time

from Managers.GameDirector import GameDirector


DEFAULT_OPPONENTS = [
    "Agents.HeuristicAgent.HeuristicAgent",
    "Agents.AdrianHerasAgent.AdrianHerasAgent",
    "Agents.RandomAgent.RandomAgent",
]


def load_class(path):
    module_name, class_name = path.rsplit(".", 1)
    module = importlib.import_module(module_name)
    return getattr(module, class_name)


def configured_agent(agent_class, **kwargs):
    class ConfiguredAgent(agent_class):
        def __init__(self, agent_id):
            super().__init__(agent_id, **kwargs)

    ConfiguredAgent.__name__ = f"{agent_class.__name__}Configured"
    return ConfiguredAgent


def parse_args():
    parser = argparse.ArgumentParser(
        description="Ejecuta una sola partida con HeurOllamaAgent para diagnosticar cuelgues."
    )
    parser.add_argument("--model", default="qwen3.5:4b", help="Modelo de Ollama a usar.")
    parser.add_argument(
        "--prompt-size",
        default="SMALL",
        choices=("BIG", "MEDIUM", "SMALL"),
        help="Tamaño de prompt.",
    )
    parser.add_argument("--position", type=int, default=0, choices=range(4), help="Posicion del agente evaluado.")
    parser.add_argument("--max-rounds", type=int, default=80, help="Maximo de rondas.")
    parser.add_argument(
        "--opponents",
        nargs=3,
        default=DEFAULT_OPPONENTS,
        help="Tres rutas completas de agentes rivales.",
    )
    parser.add_argument(
        "--print-outcome",
        action="store_true",
        help="Muestra el resumen interno del GameDirector.",
    )
    return parser.parse_args()


def extract_summary(game_trace):
    last_round = max(game_trace["game"].keys(), key=lambda name: int(name.split("_")[-1]))
    last_turn = max(
        game_trace["game"][last_round].keys(),
        key=lambda name: int(name.split("_")[-1].lstrip("P")),
    )
    victory_points = game_trace["game"][last_round][last_turn]["end_turn"]["victory_points"]
    return last_round, last_turn, victory_points


def main():
    args = parse_args()
    heur_ollama_cls = load_class("Agents.HeurOllamaAgent.HeurOllamaAgent")
    agent_cls = configured_agent(
        heur_ollama_cls,
        model=args.model,
        prompt_size=args.prompt_size,
    )

    match_agents = [load_class(path) for path in args.opponents]
    match_agents.insert(args.position, agent_cls)

    print(
        f"Single-match diagnostic | model={args.model} prompt_size={args.prompt_size} "
        f"position={args.position} max_rounds={args.max_rounds}"
    )
    print(f"Opponents: {args.opponents}")

    start = time.perf_counter()
    director = GameDirector(agents=match_agents, max_rounds=args.max_rounds, store_trace=False)
    game_trace = director.game_start(print_outcome=args.print_outcome)
    elapsed = time.perf_counter() - start

    last_round, last_turn, victory_points = extract_summary(game_trace)
    winner_points = max(int(value) for value in victory_points.values())
    reached_cap = winner_points < 10

    print(f"Elapsed: {elapsed:.2f}s")
    print(f"Last round: {last_round} | Last turn: {last_turn}")
    print(f"Victory points: {victory_points}")
    print(f"Reached max rounds without winner: {reached_cap}")


if __name__ == "__main__":
    main()
