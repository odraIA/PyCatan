import json
import os

import openai
from dotenv import load_dotenv

from Classes.Constants import BuildConstants, HarborConstants, MaterialConstants, TerrainConstants
from Classes.Materials import Materials
from Classes.TradeOffer import TradeOffer
from Interfaces.AgentInterface import AgentInterface

import llm_assets.models as models
import llm_assets.prompts as prompts

load_dotenv()


class GPTAgent(AgentInterface):
    PIP_WEIGHTS = {
        2: 1,
        3: 2,
        4: 3,
        5: 4,
        6: 5,
        8: 5,
        9: 4,
        10: 3,
        11: 2,
        12: 1,
    }

    """
    Es necesario poner super().nombre_de_funcion() para asegurarse de que coge la función del padre
    """

    def __init__(self, agent_id):
        super().__init__(agent_id)
        self.long_term_plan = ""
        self.short_term_plan = ""
        self._plans_initialized = False

        self.api_key = os.getenv("POLIGPT_API_KEY")
        self.base_url = os.getenv("POLIGPT_URL")
        self.model = os.getenv("POLIGPT_MODEL", "gpt-oss-120b")

        try:
            self.client = openai.OpenAI(api_key=self.api_key, base_url=self.base_url)
        except Exception as e:
            print(f"Error creating OpenAI client: {e}")
            self.client = None

    @staticmethod
    def _schema_json(model_cls):
        try:
            return model_cls.schema_json()
        except Exception:
            return json.dumps(model_cls.model_json_schema())

    @staticmethod
    def _json_dump(payload):
        return json.dumps(payload, ensure_ascii=True, default=str)

    @staticmethod
    def _clean_response(response_content):
        if response_content is None:
            return None

        if isinstance(response_content, list):
            chunks = []
            for block in response_content:
                if isinstance(block, str):
                    chunks.append(block)
                elif isinstance(block, dict):
                    chunks.append(str(block.get("text", block)))
                else:
                    chunks.append(str(getattr(block, "text", block)))
            response_content = "".join(chunks)

        cleaned_response = str(response_content).strip()
        if cleaned_response.startswith("```"):
            cleaned_response = cleaned_response.replace("```json", "").replace("```", "").strip()
        return cleaned_response

    def _parse_model(self, model_cls, response_content):
        cleaned_response = self._clean_response(response_content)
        if cleaned_response is None:
            return None

        try:
            if hasattr(model_cls, "model_validate_json"):
                return model_cls.model_validate_json(cleaned_response)
            return model_cls.parse_raw(cleaned_response)
        except Exception:
            pass

        try:
            payload = json.loads(cleaned_response)
        except Exception:
            return None

        try:
            if hasattr(model_cls, "model_validate"):
                return model_cls.model_validate(payload)
            return model_cls.parse_obj(payload)
        except Exception:
            return None

    def _request_llm(self, prompt):
        if self.client is None:
            return None

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
            )
        except Exception as e:
            print(f"Error requesting LLM response: {e}")
            return None

        return response.choices[0].message.content

    def _update_plans_from_response(self, parsed_response):
        if parsed_response is None:
            return

        new_long = getattr(parsed_response, "long_term_plan", None)
        if isinstance(new_long, str) and new_long.strip():
            self.long_term_plan = new_long.strip()

        new_short = getattr(parsed_response, "short_term_plan", None)
        if isinstance(new_short, str) and new_short.strip():
            self.short_term_plan = new_short.strip()

        if self.long_term_plan or self.short_term_plan:
            self._plans_initialized = True

    def _ensure_default_plans(self):
        if not self.long_term_plan:
            self.long_term_plan = "Build a flexible economy and secure reliable victory points quickly."
        if not self.short_term_plan:
            self.short_term_plan = "Take the highest expected-value legal action this phase."
        self._plans_initialized = True

    def _board_state(self, board_instance=None):
        if board_instance is not None:
            self.board = board_instance

        thief_terrain = -1
        for terrain in self.board.terrain:
            if terrain["has_thief"]:
                thief_terrain = terrain["id"]
                break

        my_nodes = [node["id"] for node in self.board.nodes if node["player"] == self.id]
        my_cities = [node["id"] for node in self.board.nodes if node["player"] == self.id and node["has_city"]]
        my_towns = [node_id for node_id in my_nodes if node_id not in my_cities]

        return {
            "player_id": self.id,
            "thief_terrain_id": thief_terrain,
            "my_nodes": my_nodes,
            "my_towns": my_towns,
            "my_cities": my_cities,
            "nodes": self.board.nodes,
            "terrain": self.board.terrain,
        }

    def _development_cards_context(self):
        return [
            {"index": idx, "type": card.type, "effect": card.effect}
            for idx, card in enumerate(self.development_cards_hand.hand)
        ]

    def _select_development_card_by_effect(self, effect, fallback_first=False):
        if effect is not None:
            for idx, card in enumerate(self.development_cards_hand.hand):
                if card.effect == effect:
                    return self.development_cards_hand.select_card(idx)

        if fallback_first and self.development_cards_hand.hand:
            return self.development_cards_hand.select_card(0)
        return None

    @staticmethod
    def _vector_to_materials(vector):
        if not isinstance(vector, list) or len(vector) != 5:
            return None
        if any((not isinstance(item, int)) or item < 0 for item in vector):
            return None
        return Materials.from_iterable(vector)

    def _ensure_plans(self, board_instance=None):
        if self._plans_initialized and self.long_term_plan and self.short_term_plan:
            return

        prompt = prompts.PLAN_INIT_PROMPT.format(
            long_term_plan=self.long_term_plan,
            short_term_plan=self.short_term_plan,
            player_id=self.id,
            board_state=self._json_dump(self._board_state(board_instance)),
            hand_resources=self._json_dump(self.hand.resources.__to_object__()),
            development_cards=self._json_dump(self._development_cards_context()),
            pydantic_plan_model=self._schema_json(models.PlanModel),
        )
        response_content = self._request_llm(prompt)
        parsed_response = self._parse_model(models.PlanModel, response_content)
        self._update_plans_from_response(parsed_response)
        self._ensure_default_plans()

    def _is_first_setup_call_of_match(self, board_instance):
        own_nodes = [node for node in board_instance.nodes if node["player"] == self.id]
        return len(own_nodes) == 0 and self.hand.get_total() == 0

    def _node_score(self, node_id):
        score = 0.0
        for terrain_id in self.board.nodes[node_id]["contacting_terrain"]:
            terrain = self.board.terrain[terrain_id]
            if terrain["terrain_type"] == TerrainConstants.DESERT:
                continue
            score += self.PIP_WEIGHTS.get(terrain["probability"], 0)

        harbor = self.board.nodes[node_id]["harbor"]
        if harbor == HarborConstants.ALL:
            score += 1.0
        elif harbor != HarborConstants.NONE:
            score += 1.5
        return score

    def _best_starting_move(self):
        valid_nodes = self.board.valid_starting_nodes()
        if not valid_nodes:
            return super().on_game_start(self.board)

        scored_nodes = sorted(valid_nodes, key=lambda node: self._node_score(node), reverse=True)
        chosen_node = scored_nodes[0]

        possible_roads = self.board.nodes[chosen_node]["adjacent"]
        scored_roads = sorted(possible_roads, key=lambda node: self._node_score(node), reverse=True)
        chosen_road_to = scored_roads[0]

        return chosen_node, chosen_road_to

    def _default_trade_answer(self, offer):
        if offer.gives.has_more(offer.receives):
            return True
        return False

    def _thief_targets_context(self):
        targets = []
        for terrain in self.board.terrain:
            enemy_players = []
            own_presence = False
            for node_id in terrain["contacting_nodes"]:
                player = self.board.nodes[node_id]["player"]
                if player == self.id:
                    own_presence = True
                elif player != -1:
                    enemy_players.append(player)

            targets.append(
                {
                    "terrain": terrain["id"],
                    "probability": terrain["probability"],
                    "terrain_type": terrain["terrain_type"],
                    "enemy_players": sorted(set(enemy_players)),
                    "own_presence": own_presence,
                }
            )
        return targets

    def _default_move_thief(self):
        best_target = None
        best_score = -1
        best_player = -1

        for terrain in self.board.terrain:
            if terrain["terrain_type"] == TerrainConstants.DESERT:
                continue

            own_presence = False
            player_count = {}
            for node_id in terrain["contacting_nodes"]:
                player = self.board.nodes[node_id]["player"]
                if player == self.id:
                    own_presence = True
                elif player != -1:
                    player_count[player] = player_count.get(player, 0) + 1

            if not player_count or own_presence:
                continue

            terrain_score = self.PIP_WEIGHTS.get(terrain["probability"], 0)
            if terrain_score > best_score:
                best_score = terrain_score
                best_target = terrain["id"]
                best_player = max(player_count, key=player_count.get)

        if best_target is None:
            for terrain in self.board.terrain:
                if not terrain["has_thief"]:
                    return {"terrain": terrain["id"], "player": -1}

            return {"terrain": 0, "player": -1}

        return {"terrain": best_target, "player": best_player}

    def _harbor_trade_options(self):
        options = []
        for material_to_give in range(5):
            harbor_type = self.board.check_for_player_harbors(self.id, material_to_give)
            rate = 4
            if harbor_type == HarborConstants.ALL:
                rate = 3
            elif harbor_type != HarborConstants.NONE:
                rate = 2

            if self.hand.resources.get_from_id(material_to_give) >= rate:
                for material_to_receive in range(5):
                    if material_to_receive == material_to_give:
                        continue
                    options.append(
                        {
                            "gives": material_to_give,
                            "receives": material_to_receive,
                            "rate": rate,
                        }
                    )
        return options

    @staticmethod
    def _contains_road(valid_roads, node_id, road_to):
        return any(
            road["starting_node"] == node_id and road["finishing_node"] == road_to
            for road in valid_roads
        )

    def _default_build(self, valid_town_nodes, valid_city_nodes, valid_road_nodes):
        if self.hand.resources.has_more(BuildConstants.CITY) and valid_city_nodes:
            node_id = sorted(valid_city_nodes, key=lambda node: self._node_score(node), reverse=True)[0]
            return {"building": BuildConstants.CITY, "node_id": node_id}

        if self.hand.resources.has_more(BuildConstants.TOWN) and valid_town_nodes:
            node_id = sorted(valid_town_nodes, key=lambda node: self._node_score(node), reverse=True)[0]
            return {"building": BuildConstants.TOWN, "node_id": node_id}

        if self.hand.resources.has_more(BuildConstants.ROAD) and valid_road_nodes:
            best_road = sorted(valid_road_nodes, key=lambda road: self._node_score(road["finishing_node"]), reverse=True)[0]
            return {
                "building": BuildConstants.ROAD,
                "node_id": best_road["starting_node"],
                "road_to": best_road["finishing_node"],
            }

        if self.hand.resources.has_more(BuildConstants.CARD):
            return {"building": BuildConstants.CARD}

        return None

    def _default_monopoly_material(self):
        estimated_resource_weight = {mat_id: 0 for mat_id in range(5)}
        for node in self.board.nodes:
            player_id = node["player"]
            if player_id in [-1, self.id]:
                continue
            city_multiplier = 2 if node["has_city"] else 1
            for terrain_id in node["contacting_terrain"]:
                terrain = self.board.terrain[terrain_id]
                terrain_type = terrain["terrain_type"]
                if terrain_type == TerrainConstants.DESERT:
                    continue
                estimated_resource_weight[terrain_type] += city_multiplier * self.PIP_WEIGHTS.get(
                    terrain["probability"], 0
                )

        best_material = max(estimated_resource_weight, key=estimated_resource_weight.get)
        return best_material

    def _default_year_of_plenty(self):
        city_needs = [
            max(0, 2 - self.hand.resources.cereal),
            max(0, 3 - self.hand.resources.mineral),
            0,
            0,
            0,
        ]
        if sum(city_needs) > 0:
            priorities = [MaterialConstants.MINERAL, MaterialConstants.CEREAL]
            picks = []
            for mat_id in priorities:
                if city_needs[mat_id] > 0:
                    picks.append(mat_id)
            while len(picks) < 2:
                picks.append(MaterialConstants.MINERAL)
            return {"material": picks[0], "material_2": picks[1]}

        town_needs = [
            max(0, 1 - self.hand.resources.cereal),
            0,
            max(0, 1 - self.hand.resources.clay),
            max(0, 1 - self.hand.resources.wood),
            max(0, 1 - self.hand.resources.wool),
        ]
        materials_sorted = sorted(range(5), key=lambda mat_id: town_needs[mat_id], reverse=True)
        return {"material": materials_sorted[0], "material_2": materials_sorted[1]}

    def on_trade_offer(self, board_instance, offer=TradeOffer(), player_id=int):
        self.board = board_instance
        self._ensure_plans(board_instance)

        prompt = prompts.TRADE_PROMPT.format(
            long_term_plan=self.long_term_plan,
            short_term_plan=self.short_term_plan,
            board_state=self._json_dump(self._board_state(board_instance)),
            player_id=player_id,
            trade_offer=self._json_dump(offer.__to_object__()),
            hand_resources=self._json_dump(self.hand.resources.__to_object__()),
            development_cards=self._json_dump(self._development_cards_context()),
            pydantic_trade_model=self._schema_json(models.TradeOfferModel),
        )
        response_content = self._request_llm(prompt)
        cleaned_response = self._clean_response(response_content)

        if cleaned_response is None:
            return self._default_trade_answer(offer)

        lowered_response = cleaned_response.strip().lower()
        if lowered_response in ["none", "null", "false"]:
            return False
        if lowered_response == "true":
            return True

        trade_offer_response = self._parse_model(models.TradeOfferModel, cleaned_response)
        if trade_offer_response is None:
            return self._default_trade_answer(offer)

        self._update_plans_from_response(trade_offer_response)

        gives = self._vector_to_materials(trade_offer_response.gives)
        receives = self._vector_to_materials(trade_offer_response.receives)
        if gives is None or receives is None or gives.is_empty() or receives.is_empty():
            return False

        if not self.hand.resources.has_more(gives):
            return False

        return TradeOffer(gives, receives)

    def on_turn_start(self):
        self._ensure_plans()
        playable_development_cards = self._development_cards_context()
        if not playable_development_cards:
            return None

        prompt = prompts.TURN_START_PROMPT.format(
            long_term_plan=self.long_term_plan,
            short_term_plan=self.short_term_plan,
            board_state=self._json_dump(self._board_state()),
            hand_resources=self._json_dump(self.hand.resources.__to_object__()),
            development_cards=self._json_dump(playable_development_cards),
            playable_development_cards=self._json_dump(playable_development_cards),
            pydantic_decision_model=self._schema_json(models.DevelopmentCardDecisionModel),
        )
        response_content = self._request_llm(prompt)
        parsed_response = self._parse_model(models.DevelopmentCardDecisionModel, response_content)
        self._update_plans_from_response(parsed_response)

        if parsed_response is None or not parsed_response.play_development_card:
            return None

        return self._select_development_card_by_effect(
            parsed_response.development_card_effect, fallback_first=False
        )

    def on_having_more_than_7_materials_when_thief_is_called(self):
        self._ensure_plans()
        prompt = prompts.DISCARD_PROMPT.format(
            long_term_plan=self.long_term_plan,
            short_term_plan=self.short_term_plan,
            board_state=self._json_dump(self._board_state()),
            hand_resources=self._json_dump(self.hand.resources.__to_object__()),
            pydantic_plan_model=self._schema_json(models.PlanModel),
        )
        response_content = self._request_llm(prompt)
        parsed_response = self._parse_model(models.PlanModel, response_content)
        self._update_plans_from_response(parsed_response)
        return self.hand

    def on_moving_thief(self):
        self._ensure_plans()
        prompt = prompts.MOVE_THIEF_PROMPT.format(
            long_term_plan=self.long_term_plan,
            short_term_plan=self.short_term_plan,
            board_state=self._json_dump(self._board_state()),
            hand_resources=self._json_dump(self.hand.resources.__to_object__()),
            thief_targets=self._json_dump(self._thief_targets_context()),
            pydantic_move_model=self._schema_json(models.ThiefMoveModel),
        )
        response_content = self._request_llm(prompt)
        parsed_response = self._parse_model(models.ThiefMoveModel, response_content)
        self._update_plans_from_response(parsed_response)

        if parsed_response is None:
            return self._default_move_thief()

        terrain = parsed_response.terrain
        player = parsed_response.player

        if terrain < 0 or terrain >= len(self.board.terrain):
            return self._default_move_thief()

        if player != -1:
            can_rob_player = any(
                self.board.nodes[node_id]["player"] == player
                for node_id in self.board.terrain[terrain]["contacting_nodes"]
            )
            if (not can_rob_player) or player == self.id:
                player = -1

        return {"terrain": terrain, "player": player}

    def on_turn_end(self):
        self._ensure_plans()
        playable_development_cards = self._development_cards_context()
        if not playable_development_cards:
            return None

        prompt = prompts.TURN_END_PROMPT.format(
            long_term_plan=self.long_term_plan,
            short_term_plan=self.short_term_plan,
            board_state=self._json_dump(self._board_state()),
            hand_resources=self._json_dump(self.hand.resources.__to_object__()),
            development_cards=self._json_dump(playable_development_cards),
            playable_development_cards=self._json_dump(playable_development_cards),
            pydantic_decision_model=self._schema_json(models.DevelopmentCardDecisionModel),
        )
        response_content = self._request_llm(prompt)
        parsed_response = self._parse_model(models.DevelopmentCardDecisionModel, response_content)
        self._update_plans_from_response(parsed_response)

        if parsed_response is None or not parsed_response.play_development_card:
            return None

        return self._select_development_card_by_effect(
            parsed_response.development_card_effect, fallback_first=False
        )

    def on_commerce_phase(self):
        self._ensure_plans()
        playable_development_cards = self._development_cards_context()
        harbor_trade_options = self._harbor_trade_options()

        prompt = prompts.COMMERCE_PROMPT.format(
            long_term_plan=self.long_term_plan,
            short_term_plan=self.short_term_plan,
            board_state=self._json_dump(self._board_state()),
            hand_resources=self._json_dump(self.hand.resources.__to_object__()),
            development_cards=self._json_dump(playable_development_cards),
            playable_development_cards=self._json_dump(playable_development_cards),
            harbor_trade_options=self._json_dump(harbor_trade_options),
            pydantic_commerce_model=self._schema_json(models.CommerceDecisionModel),
        )
        response_content = self._request_llm(prompt)
        parsed_response = self._parse_model(models.CommerceDecisionModel, response_content)
        self._update_plans_from_response(parsed_response)

        if parsed_response is None:
            return None

        if parsed_response.action == "play_development_card":
            return self._select_development_card_by_effect(parsed_response.development_card_effect, fallback_first=False)

        if parsed_response.action == "harbor_trade":
            gives = parsed_response.harbor_gives
            receives = parsed_response.harbor_receives
            if isinstance(gives, int) and isinstance(receives, int) and 0 <= gives <= 4 and 0 <= receives <= 4:
                if gives != receives:
                    return {"gives": gives, "receives": receives}
            return None

        if parsed_response.action == "player_trade":
            gives = self._vector_to_materials(parsed_response.gives)
            receives = self._vector_to_materials(parsed_response.receives)
            if gives is None or receives is None:
                return None
            if gives.is_empty() or receives.is_empty():
                return None
            if not self.hand.resources.has_more(gives):
                return None
            return TradeOffer(gives, receives)

        return None

    def on_build_phase(self, board_instance):
        self.board = board_instance
        self._ensure_plans(board_instance)

        playable_development_cards = self._development_cards_context()
        valid_town_nodes = self.board.valid_town_nodes(self.id)
        valid_city_nodes = self.board.valid_city_nodes(self.id)
        valid_road_nodes = self.board.valid_road_nodes(self.id)

        prompt = prompts.BUILD_PROMPT.format(
            long_term_plan=self.long_term_plan,
            short_term_plan=self.short_term_plan,
            board_state=self._json_dump(self._board_state(board_instance)),
            hand_resources=self._json_dump(self.hand.resources.__to_object__()),
            development_cards=self._json_dump(playable_development_cards),
            playable_development_cards=self._json_dump(playable_development_cards),
            valid_town_nodes=self._json_dump(valid_town_nodes),
            valid_city_nodes=self._json_dump(valid_city_nodes),
            valid_road_nodes=self._json_dump(valid_road_nodes),
            pydantic_build_model=self._schema_json(models.BuildDecisionModel),
        )
        response_content = self._request_llm(prompt)
        parsed_response = self._parse_model(models.BuildDecisionModel, response_content)
        self._update_plans_from_response(parsed_response)

        if parsed_response is None:
            return self._default_build(valid_town_nodes, valid_city_nodes, valid_road_nodes)

        if parsed_response.action == "play_development_card":
            return self._select_development_card_by_effect(parsed_response.development_card_effect, fallback_first=False)

        if parsed_response.action == "build_town":
            if (
                isinstance(parsed_response.node_id, int)
                and parsed_response.node_id in valid_town_nodes
                and self.hand.resources.has_more(BuildConstants.TOWN)
            ):
                return {"building": BuildConstants.TOWN, "node_id": parsed_response.node_id}
            return self._default_build(valid_town_nodes, valid_city_nodes, valid_road_nodes)

        if parsed_response.action == "build_city":
            if (
                isinstance(parsed_response.node_id, int)
                and parsed_response.node_id in valid_city_nodes
                and self.hand.resources.has_more(BuildConstants.CITY)
            ):
                return {"building": BuildConstants.CITY, "node_id": parsed_response.node_id}
            return self._default_build(valid_town_nodes, valid_city_nodes, valid_road_nodes)

        if parsed_response.action == "build_road":
            if (
                isinstance(parsed_response.node_id, int)
                and isinstance(parsed_response.road_to, int)
                and self._contains_road(valid_road_nodes, parsed_response.node_id, parsed_response.road_to)
                and self.hand.resources.has_more(BuildConstants.ROAD)
            ):
                return {
                    "building": BuildConstants.ROAD,
                    "node_id": parsed_response.node_id,
                    "road_to": parsed_response.road_to,
                }
            return self._default_build(valid_town_nodes, valid_city_nodes, valid_road_nodes)

        if parsed_response.action == "build_card":
            if self.hand.resources.has_more(BuildConstants.CARD):
                return {"building": BuildConstants.CARD}
            return self._default_build(valid_town_nodes, valid_city_nodes, valid_road_nodes)

        return None

    def on_game_start(self, board_instance):
        self.board = board_instance

        if self._is_first_setup_call_of_match(board_instance):
            self.long_term_plan = ""
            self.short_term_plan = ""
            self._plans_initialized = False

        valid_starting_nodes = self.board.valid_starting_nodes()
        node_road_options = {node_id: self.board.nodes[node_id]["adjacent"] for node_id in valid_starting_nodes}

        prompt = prompts.GAME_START_PROMPT.format(
            long_term_plan=self.long_term_plan,
            short_term_plan=self.short_term_plan,
            player_id=self.id,
            board_state=self._json_dump(self._board_state(board_instance)),
            valid_starting_nodes=self._json_dump(valid_starting_nodes),
            node_road_options=self._json_dump(node_road_options),
            pydantic_game_start_model=self._schema_json(models.GameStartModel),
        )
        response_content = self._request_llm(prompt)
        parsed_response = self._parse_model(models.GameStartModel, response_content)
        self._update_plans_from_response(parsed_response)

        if parsed_response is not None:
            node_id = parsed_response.node_id
            road_to = parsed_response.road_to
            if node_id in valid_starting_nodes and road_to in self.board.nodes[node_id]["adjacent"]:
                return node_id, road_to

        self._ensure_plans(board_instance)
        return self._best_starting_move()

    def on_monopoly_card_use(self):
        self._ensure_plans()
        prompt = prompts.MONOPOLY_PROMPT.format(
            long_term_plan=self.long_term_plan,
            short_term_plan=self.short_term_plan,
            board_state=self._json_dump(self._board_state()),
            hand_resources=self._json_dump(self.hand.resources.__to_object__()),
            pydantic_monopoly_model=self._schema_json(models.MonopolyDecisionModel),
        )
        response_content = self._request_llm(prompt)
        parsed_response = self._parse_model(models.MonopolyDecisionModel, response_content)
        self._update_plans_from_response(parsed_response)

        if parsed_response is not None and 0 <= parsed_response.material <= 4:
            return parsed_response.material
        return self._default_monopoly_material()

    # noinspection DuplicatedCode
    def on_road_building_card_use(self):
        self._ensure_plans()
        valid_nodes = self.board.valid_road_nodes(self.id)
        if not valid_nodes:
            return None

        prompt = prompts.ROAD_BUILDING_PROMPT.format(
            long_term_plan=self.long_term_plan,
            short_term_plan=self.short_term_plan,
            board_state=self._json_dump(self._board_state()),
            hand_resources=self._json_dump(self.hand.resources.__to_object__()),
            valid_road_nodes=self._json_dump(valid_nodes),
            pydantic_road_building_model=self._schema_json(models.RoadBuildingDecisionModel),
        )
        response_content = self._request_llm(prompt)
        parsed_response = self._parse_model(models.RoadBuildingDecisionModel, response_content)
        self._update_plans_from_response(parsed_response)

        default_first_road = valid_nodes[0]
        default_second_road = valid_nodes[1] if len(valid_nodes) > 1 else None

        node_id = default_first_road["starting_node"]
        road_to = default_first_road["finishing_node"]
        node_id_2 = default_second_road["starting_node"] if default_second_road else None
        road_to_2 = default_second_road["finishing_node"] if default_second_road else None

        if parsed_response is not None:
            if (
                parsed_response.node_id is not None
                and parsed_response.road_to is not None
                and self._contains_road(valid_nodes, parsed_response.node_id, parsed_response.road_to)
            ):
                node_id = parsed_response.node_id
                road_to = parsed_response.road_to

            if (
                parsed_response.node_id_2 is not None
                and parsed_response.road_to_2 is not None
                and self._contains_road(valid_nodes, parsed_response.node_id_2, parsed_response.road_to_2)
                and (parsed_response.node_id_2 != node_id or parsed_response.road_to_2 != road_to)
            ):
                node_id_2 = parsed_response.node_id_2
                road_to_2 = parsed_response.road_to_2

        if len(valid_nodes) == 1:
            node_id_2 = None
            road_to_2 = None

        return {
            "node_id": node_id,
            "road_to": road_to,
            "node_id_2": node_id_2,
            "road_to_2": road_to_2,
        }

    def on_year_of_plenty_card_use(self):
        self._ensure_plans()
        prompt = prompts.YEAR_OF_PLENTY_PROMPT.format(
            long_term_plan=self.long_term_plan,
            short_term_plan=self.short_term_plan,
            board_state=self._json_dump(self._board_state()),
            hand_resources=self._json_dump(self.hand.resources.__to_object__()),
            pydantic_year_of_plenty_model=self._schema_json(models.YearOfPlentyDecisionModel),
        )
        response_content = self._request_llm(prompt)
        parsed_response = self._parse_model(models.YearOfPlentyDecisionModel, response_content)
        self._update_plans_from_response(parsed_response)

        if (
            parsed_response is not None
            and 0 <= parsed_response.material <= 4
            and 0 <= parsed_response.material_2 <= 4
        ):
            return {"material": parsed_response.material, "material_2": parsed_response.material_2}

        return self._default_year_of_plenty()
