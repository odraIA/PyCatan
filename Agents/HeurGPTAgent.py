import random
import json
import os
import openai
from dotenv import load_dotenv
from copy import deepcopy

from Classes.Constants import (
    BuildConstants,
    DevelopmentCardConstants,
    HarborConstants,
    MaterialConstants,
    TerrainConstants,
)
from Classes.Materials import Materials
from Classes.TradeOffer import TradeOffer
from Interfaces.AgentInterface import AgentInterface

from llm_assets import models, prompts

load_dotenv()

class HeurGPTAgent(AgentInterface):

    PIPS_BY_NUMBER = {
        2: 1,
        3: 2,
        4: 3,
        5: 4,
        6: 5,
        7: 0,
        8: 5,
        9: 4,
        10: 3,
        11: 2,
        12: 1,
    }

    BASE_MATERIAL_VALUES = {
        MaterialConstants.CEREAL: 1.25,
        MaterialConstants.MINERAL: 1.35,
        MaterialConstants.CLAY: 1.05,
        MaterialConstants.WOOD: 1.00,
        MaterialConstants.WOOL: 0.90,
    }

    BUILD_PRIORITY = {
        BuildConstants.CITY: 4.0,
        BuildConstants.TOWN: 3.4,
        BuildConstants.CARD: 2.2,
        BuildConstants.ROAD: 1.7,
    }

    def __init__(self, agent_id, model="gpt-oss-120b", prompt_size="BIG"):
        super().__init__(agent_id)
        self._commerce_actions = 0
        self.api_key = os.getenv("POLIGPT_API_KEY")
        self.base_url = os.getenv("POLIGPT_BASE_URL")
        self.model = model
        self.prompt_size = prompt_size

        try:
            self.client = openai.OpenAI(api_key=self.api_key, base_url=self.base_url)
        except Exception as e:
            print(f"Error creating OpenAI client: {e}")
            self.client = None

    # -- -- -- -- helpers -- -- -- --
    def _pips(self, number):
        return self.PIPS_BY_NUMBER.get(number, 0)

    def _resources_as_list(self, resources=None):
        resources = self.hand.resources if resources is None else resources
        return [resources.cereal, resources.mineral, resources.clay, resources.wood, resources.wool]

    def _prompt_by_size(self, prompt_base_name):
        size = str(self.prompt_size).strip().upper()
        if size not in {"BIG", "MEDIUM", "SMALL"}:
            size = "MEDIUM"
        return getattr(prompts, f"{prompt_base_name}_{size}", getattr(prompts, prompt_base_name))

    def _build_shortage(self, building, resources=None):
        resources = self.hand.resources if resources is None else resources
        required = Materials.from_building(building)
        return [max(0, required[i] - resources[i]) for i in range(5)]

    def _missing_count(self, building, resources=None):
        return sum(self._build_shortage(building, resources))

    def _is_build_legal(self, building, board_instance=None):
        board_instance = self.board if board_instance is None else board_instance
        if building == BuildConstants.CITY:
            return len(board_instance.valid_city_nodes(self.id)) > 0
        if building == BuildConstants.TOWN:
            return len(board_instance.valid_town_nodes(self.id)) > 0
        if building == BuildConstants.ROAD:
            return len(board_instance.valid_road_nodes(self.id)) > 0
        return True

    def _node_resource_profile(self, node_id, board_instance=None, include_blocked=True):
        board_instance = self.board if board_instance is None else board_instance
        resource_pips = [0, 0, 0, 0, 0]
        numbers = []

        for terrain_id in board_instance.nodes[node_id]["contacting_terrain"]:
            terrain = board_instance.terrain[terrain_id]
            terrain_type = terrain["terrain_type"]
            if terrain_type == TerrainConstants.DESERT:
                continue
            if not include_blocked and terrain["has_thief"]:
                continue

            pips = self._pips(terrain["probability"])
            if pips <= 0:
                continue
            resource_pips[terrain_type] += pips
            numbers.append(terrain["probability"])

        return resource_pips, set(numbers)

    def _node_score_for_settlement(
        self,
        node_id,
        board_instance=None,
        existing_resource_pips=None,
        existing_numbers=None,
    ):
        board_instance = self.board if board_instance is None else board_instance
        resource_pips, numbers = self._node_resource_profile(node_id, board_instance=board_instance, include_blocked=True)
        total_pips = sum(resource_pips)
        unique_resources = sum(1 for value in resource_pips if value > 0)

        score = 2.8 * total_pips
        score += 1.8 * unique_resources
        score += 0.8 * len(numbers)
        score += 1.4 * resource_pips[MaterialConstants.MINERAL]
        score += 1.2 * resource_pips[MaterialConstants.CEREAL]
        score += 0.9 * min(resource_pips[MaterialConstants.WOOD], resource_pips[MaterialConstants.CLAY])
        score -= 0.3 * abs(resource_pips[MaterialConstants.WOOD] - resource_pips[MaterialConstants.CLAY])

        if existing_resource_pips is not None:
            missing_before = sum(1 for pips in existing_resource_pips if pips == 0)
            combined = [existing_resource_pips[i] + resource_pips[i] for i in range(5)]
            missing_after = sum(1 for pips in combined if pips == 0)
            score += 4.0 * (missing_before - missing_after)

            if existing_numbers is not None:
                score += 0.9 * len(numbers - existing_numbers)
                score -= 0.6 * len(numbers & existing_numbers)

            if existing_resource_pips[MaterialConstants.MINERAL] == 0:
                score += 1.4 * resource_pips[MaterialConstants.MINERAL]
            if existing_resource_pips[MaterialConstants.CEREAL] == 0:
                score += 1.3 * resource_pips[MaterialConstants.CEREAL]

        harbor = board_instance.nodes[node_id]["harbor"]
        if harbor == HarborConstants.ALL:
            score += 2.4
        elif harbor != HarborConstants.NONE:
            score += 1.9

        if total_pips > 0 and max(resource_pips) > 0.75 * total_pips and harbor == HarborConstants.NONE:
            score -= 1.6

        return score

    def _city_node_score(self, node_id, board_instance=None):
        board_instance = self.board if board_instance is None else board_instance
        resource_pips, _ = self._node_resource_profile(node_id, board_instance=board_instance, include_blocked=True)
        total_pips = sum(resource_pips)
        score = 2.6 * total_pips
        score += 1.8 * resource_pips[MaterialConstants.MINERAL]
        score += 1.6 * resource_pips[MaterialConstants.CEREAL]
        score += 0.6 * resource_pips[MaterialConstants.WOOL]
        return score

    def _player_resource_pips(self, player_id, board_instance=None, include_blocked=True):
        board_instance = self.board if board_instance is None else board_instance
        totals = [0, 0, 0, 0, 0]
        for node in board_instance.nodes:
            if node["player"] != player_id:
                continue
            multiplier = 2 if node["has_city"] else 1
            profile, _ = self._node_resource_profile(node["id"], board_instance=board_instance, include_blocked=include_blocked)
            for material_id in range(5):
                totals[material_id] += profile[material_id] * multiplier
        return totals

    def _player_numbers(self, player_id, board_instance=None):
        board_instance = self.board if board_instance is None else board_instance
        numbers = set()
        for node in board_instance.nodes:
            if node["player"] != player_id:
                continue
            _, node_numbers = self._node_resource_profile(node["id"], board_instance=board_instance, include_blocked=True)
            numbers |= node_numbers
        return numbers

    def _estimate_player_strength(self, player_id, board_instance=None):
        board_instance = self.board if board_instance is None else board_instance
        if player_id == -1:
            return -1.0

        settlements_points = 0.0
        for node in board_instance.nodes:
            if node["player"] == player_id:
                settlements_points += 2.0 if node["has_city"] else 1.0

        production = self._player_resource_pips(player_id, board_instance=board_instance, include_blocked=True)
        production_score = sum(production) * 0.12

        roads = set()
        for node in board_instance.nodes:
            for road in node["roads"]:
                if road["player_id"] == player_id:
                    roads.add(tuple(sorted((node["id"], road["node_id"]))))

        roads_score = min(len(roads), 12) * 0.16
        return settlements_points + production_score + roads_score

    def _strongest_opponent(self, board_instance=None):
        board_instance = self.board if board_instance is None else board_instance
        opponents = {node["player"] for node in board_instance.nodes if node["player"] not in (-1, self.id)}
        if not opponents:
            return -1
        return max(opponents, key=lambda pid: self._estimate_player_strength(pid, board_instance=board_instance))

    def _blocked_pips_by_thief(self, board_instance=None):
        board_instance = self.board if board_instance is None else board_instance
        blocked = 0
        for terrain in board_instance.terrain:
            if not terrain["has_thief"]:
                continue
            pips = self._pips(terrain["probability"])
            for node_id in terrain["contacting_nodes"]:
                node = board_instance.nodes[node_id]
                if node["player"] == self.id:
                    blocked += pips * (2 if node["has_city"] else 1)
        return blocked

    def _material_values(self):
        values = dict(self.BASE_MATERIAL_VALUES)
        shortage_city = self._build_shortage(BuildConstants.CITY)
        shortage_town = self._build_shortage(BuildConstants.TOWN)
        shortage_card = self._build_shortage(BuildConstants.CARD)
        amounts = self._resources_as_list()

        for material_id in range(5):
            values[material_id] += 0.60 * shortage_city[material_id]
            values[material_id] += 0.40 * shortage_town[material_id]
            values[material_id] += 0.25 * shortage_card[material_id]

            if amounts[material_id] >= 5:
                values[material_id] -= 0.25
            elif amounts[material_id] == 0:
                values[material_id] += 0.20

            if values[material_id] < 0.2:
                values[material_id] = 0.2

        return values

    def _materials_value(self, materials):
        values = self._material_values()
        return sum(materials[i] * values[i] for i in range(5))

    def _build_readiness_score(self, resources):
        score = 0.0
        for building, weight in self.BUILD_PRIORITY.items():
            if not self._is_build_legal(building):
                continue
            required = Materials.from_building(building)
            missing = sum(max(0, required[i] - resources[i]) for i in range(5))
            completeness = 1.0 - (missing / max(sum(required), 1))
            score += weight * completeness
            if missing == 0:
                score += 0.35
        return score

    def _preferred_build_goal(self):
        candidates = []
        for building in (BuildConstants.CITY, BuildConstants.TOWN, BuildConstants.CARD, BuildConstants.ROAD):
            if not self._is_build_legal(building):
                continue
            missing = self._missing_count(building)
            candidates.append((self.BUILD_PRIORITY[building] - 0.75 * missing, building))

        if not candidates:
            return BuildConstants.CARD
        return max(candidates, key=lambda item: item[0])[1]

    def _best_city_node(self, board_instance=None):
        board_instance = self.board if board_instance is None else board_instance
        valid_nodes = board_instance.valid_city_nodes(self.id)
        if not valid_nodes:
            return None
        return max(valid_nodes, key=lambda nid: self._city_node_score(nid, board_instance=board_instance))

    def _best_town_node(self, board_instance=None):
        board_instance = self.board if board_instance is None else board_instance
        valid_nodes = board_instance.valid_town_nodes(self.id)
        if not valid_nodes:
            return None

        existing_pips = self._player_resource_pips(self.id, board_instance=board_instance, include_blocked=True)
        existing_numbers = self._player_numbers(self.id, board_instance=board_instance)
        return max(
            valid_nodes,
            key=lambda nid: self._node_score_for_settlement(
                nid,
                board_instance=board_instance,
                existing_resource_pips=existing_pips,
                existing_numbers=existing_numbers,
            ),
        )

    def _owns_harbor(self, harbor_type, board_instance=None):
        board_instance = self.board if board_instance is None else board_instance
        for node in board_instance.nodes:
            if node["player"] == self.id and node["harbor"] == harbor_type:
                return True
        return False

    def _road_score(self, road_obj, board_instance=None):
        board_instance = self.board if board_instance is None else board_instance
        start = road_obj["starting_node"]
        end = road_obj["finishing_node"]
        score = 0.0

        if board_instance.nodes[end]["player"] == -1 and board_instance.empty_adjacent_nodes(end):
            score += 1.25 * self._node_score_for_settlement(end, board_instance=board_instance)

        lookahead = 0.0
        branches = 0
        for next_node in board_instance.nodes[end]["adjacent"]:
            if next_node == start:
                continue
            if board_instance.nodes[next_node]["player"] == -1 and board_instance.empty_adjacent_nodes(next_node):
                branches += 1
                lookahead = max(
                    lookahead,
                    self._node_score_for_settlement(next_node, board_instance=board_instance),
                )

        score += 0.75 * lookahead
        score += 1.3 * branches

        harbor = board_instance.nodes[end]["harbor"]
        if harbor == HarborConstants.ALL and not self._owns_harbor(HarborConstants.ALL, board_instance=board_instance):
            score += 5.5
        elif harbor not in (HarborConstants.NONE, HarborConstants.ALL) and not self._owns_harbor(
            harbor, board_instance=board_instance
        ):
            score += 4.5

        return score

    def _best_road(self, board_instance=None):
        board_instance = self.board if board_instance is None else board_instance
        valid_roads = board_instance.valid_road_nodes(self.id)
        if not valid_roads:
            return None, -1.0
        best = max(valid_roads, key=lambda road: self._road_score(road, board_instance=board_instance))
        return best, self._road_score(best, board_instance=board_instance)

    def _first_card_by_effect(self, effect):
        cards = self.development_cards_hand.find_card_by_effect(effect)
        if cards:
            return cards[0]
        return None

    def _best_monopoly_material(self):
        target = self._preferred_build_goal()
        shortage = self._build_shortage(target)
        opponents_pips = [0, 0, 0, 0, 0]
        for node in self.board.nodes:
            owner = node["player"]
            if owner in (-1, self.id):
                continue
            profile, _ = self._node_resource_profile(node["id"], include_blocked=True)
            multiplier = 2 if node["has_city"] else 1
            for material_id in range(5):
                opponents_pips[material_id] += profile[material_id] * multiplier

        best_material = MaterialConstants.MINERAL
        best_score = -1.0
        for material_id in range(5):
            score = 1.45 * opponents_pips[material_id] + 4.0 * shortage[material_id]
            score += self.BASE_MATERIAL_VALUES[material_id]
            if material_id in (MaterialConstants.MINERAL, MaterialConstants.CEREAL):
                score += 0.5
            if score > best_score:
                best_score = score
                best_material = material_id
        return best_material, best_score

    def _best_year_of_plenty_pair(self):
        target = self._preferred_build_goal()
        shortage = self._build_shortage(target)
        materials = []
        values = self._material_values()

        for material_id in sorted(range(5), key=lambda mid: (shortage[mid], values[mid]), reverse=True):
            while shortage[material_id] > 0 and len(materials) < 2:
                materials.append(material_id)
                shortage[material_id] -= 1
            if len(materials) == 2:
                break

        if len(materials) < 2:
            for material_id in sorted(range(5), key=lambda mid: values[mid], reverse=True):
                materials.append(material_id)
                if len(materials) == 2:
                    break

        if not materials:
            return MaterialConstants.MINERAL, MaterialConstants.CEREAL
        if len(materials) == 1:
            return materials[0], materials[0]
        return materials[0], materials[1]

    def _best_harbor_trade(self):
        target = self._preferred_build_goal()
        shortage = self._build_shortage(target)
        if sum(shortage) == 0:
            return None

        values = self._material_values()
        resources = self._resources_as_list()
        required_target = list(Materials.from_building(target))

        best = None
        best_score = 0.0
        for receives in range(5):
            if shortage[receives] <= 0:
                continue
            for gives in range(5):
                if gives == receives:
                    continue

                harbor_type = self.board.check_for_player_harbors(self.id, gives)
                rate = 2 if harbor_type == gives else 3 if harbor_type == HarborConstants.ALL else 4
                reserve = required_target[gives]
                available_surplus = resources[gives] - reserve
                if available_surplus < rate:
                    continue

                score = 4.0 * shortage[receives]
                score += 2.0 * values[receives]
                score -= 1.4 * rate * values[gives]
                score += 0.45 * (available_surplus - rate)
                if score > best_score:
                    best_score = score
                    best = {"gives": gives, "receives": receives}

        return best if best_score > 0 else None

    def _best_player_trade_offer(self):
        target = self._preferred_build_goal()
        shortage = self._build_shortage(target)
        if sum(shortage) == 0:
            return None

        values = self._material_values()
        resources = self._resources_as_list()
        required_target = list(Materials.from_building(target))

        wanted = max(range(5), key=lambda material_id: (shortage[material_id], values[material_id]))
        if shortage[wanted] == 0:
            return None

        offer_candidates = []
        for gives in range(5):
            if gives == wanted:
                continue
            reserve = required_target[gives]
            if resources[gives] - reserve <= 0:
                continue
            if gives in (MaterialConstants.MINERAL, MaterialConstants.CEREAL) and resources[gives] <= reserve + 1:
                continue
            offer_candidates.append(gives)

        if not offer_candidates:
            return None

        offered = min(offer_candidates, key=lambda mid: values[mid])
        gives = Materials.from_ids(offered, 1)
        receives = Materials.from_ids(wanted, 1)
        return TradeOffer(gives=gives, receives=receives)

    def _best_starting_road(self, node_id, board_instance=None):
        board_instance = self.board if board_instance is None else board_instance
        candidates = board_instance.nodes[node_id]["adjacent"]
        if not candidates:
            return None

        best_target = None
        best_score = -1.0
        for target in candidates:
            score = 0.0
            if board_instance.nodes[target]["player"] == -1 and board_instance.empty_adjacent_nodes(target):
                score += 1.2 * self._node_score_for_settlement(target, board_instance=board_instance)

            best_next = 0.0
            open_count = 0
            for next_node in board_instance.nodes[target]["adjacent"]:
                if next_node == node_id:
                    continue
                if board_instance.nodes[next_node]["player"] == -1 and board_instance.empty_adjacent_nodes(next_node):
                    open_count += 1
                    best_next = max(
                        best_next,
                        self._node_score_for_settlement(next_node, board_instance=board_instance),
                    )
            score += 0.7 * best_next
            score += 1.2 * open_count

            harbor = board_instance.nodes[target]["harbor"]
            if harbor == HarborConstants.ALL:
                score += 2.6
            elif harbor != HarborConstants.NONE:
                score += 2.2

            if score > best_score:
                best_score = score
                best_target = target

        return best_target
    
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

            terrain_score = self.PIPS_BY_NUMBER.get(terrain["probability"], 0)
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

    # -- -- -- -- interface triggers -- -- -- --
    def on_trade_offer(self, board_instance, offer=TradeOffer(), player_id=int):
        self.board = board_instance

        if not self.hand.resources.has_more(offer.receives):
            return False

        hand_after = self.hand.resources + offer.gives - offer.receives
        if hand_after.check_negative():
            return False

        before_readiness = self._build_readiness_score(self.hand.resources)
        after_readiness = self._build_readiness_score(hand_after)
        readiness_gain = after_readiness - before_readiness

        incoming_value = self._materials_value(offer.gives)
        outgoing_value = self._materials_value(offer.receives)

        strongest = self._strongest_opponent(board_instance=board_instance)
        if player_id == strongest:
            if offer.receives[MaterialConstants.MINERAL] > 0 and self.hand.resources.mineral <= 2:
                return False
            if offer.receives[MaterialConstants.CEREAL] > 0 and self.hand.resources.cereal <= 2:
                return False

        if readiness_gain > 0.20 and incoming_value >= 0.90 * outgoing_value:
            return True
        if incoming_value >= 1.25 * outgoing_value and readiness_gain >= -0.05:
            return True

        return False

    def on_turn_start(self):
        self._commerce_actions = 0

        # Mover ladrón fuera de nuestros mejores nodos al inicio del turno suele ser óptimo.
        knight = self._first_card_by_effect(DevelopmentCardConstants.KNIGHT_EFFECT)
        blocked_pips = self._blocked_pips_by_thief()
        if knight is not None and blocked_pips >= 4 and self.hand.get_total() != 7:
            return knight

        vp_card = self._first_card_by_effect(DevelopmentCardConstants.VICTORY_POINT_EFFECT)
        own_board_points = sum(2 if node["has_city"] else 1 for node in self.board.nodes if node["player"] == self.id)
        hidden_vp_cards = len(self.development_cards_hand.find_card_by_effect(DevelopmentCardConstants.VICTORY_POINT_EFFECT))
        if vp_card is not None and own_board_points + hidden_vp_cards >= 10:
            return vp_card

        return None

    def on_having_more_than_7_materials_when_thief_is_called(self):
        # El motor actual descarta de forma aleatoria tras este callback.
        # Devolvemos la mano real para no introducir comportamientos inválidos.
        return self.hand

    def on_moving_thief(self):
        strongest = self._strongest_opponent()
        my_resources = self._resources_as_list()
        lacking = min(range(5), key=lambda material_id: my_resources[material_id])
        pips_by_number = dict(self.PIPS_BY_NUMBER)
        pips_by_number[7] = 0

        current_terrain = next((terrain["id"] for terrain in self.board.terrain if terrain["has_thief"]), 0)
        
        heuristic_candidates = []
        for terrain in self.board.terrain:
            if terrain["terrain_type"] == TerrainConstants.DESERT:
                continue

            pips = pips_by_number.get(terrain["probability"], 0)

            touching_own = False
            enemy_nodes = []
            for node_id in terrain["contacting_nodes"]:
                owner = self.board.nodes[node_id]["player"]
                if owner == self.id:
                    touching_own = True
                elif owner != -1:
                    enemy_nodes.append(node_id)

            if not enemy_nodes:
                continue

            own_penalty = 2.4 * pips if touching_own else 0.0
            best_player = -1
            best_score = -1e9
            player_set = sorted({self.board.nodes[node_id]["player"] for node_id in enemy_nodes})
            for owner in player_set:
                has_city_contact = any(
                    self.board.nodes[node_id]["player"] == owner and self.board.nodes[node_id]["has_city"]
                    for node_id in enemy_nodes
                )
                city_factor = 1.2 if has_city_contact else 1.0
                strength = self._estimate_player_strength(owner)
                score = pips * city_factor * (1.5 if owner == strongest else 1.0)
                score += 0.35 * strength
                score -= own_penalty
                if terrain["terrain_type"] == lacking:
                    score -= 1.5

                if score > best_score:
                    best_score = score
                    best_player = owner

            heuristic_candidates.append(
                {
                    "terrain": terrain["id"],
                    "terrain_type": terrain["terrain_type"],
                    "probability": terrain["probability"],
                    "touching_own": touching_own,
                    "current_thief_terrain": terrain["id"] == current_terrain,
                    "best_player": best_player,
                    "heuristic_score": round(best_score, 3),
                }
            )

        heuristic_candidates.sort(key=lambda item: item["heuristic_score"], reverse=True)
        heuristic_context = {
            "strongest_opponent": strongest,
            "lacking_resource": lacking,
            "current_thief_terrain": current_terrain,
            "top_candidates": heuristic_candidates[:8],
        }
        thief_targets_payload = {
            "candidates": self._thief_targets_context(),
            "heuristics": heuristic_context,
        }

        prompt = self._prompt_by_size("MOVE_THIEF_PROMPT").format(
            board_state=self._json_dump(self._board_state()),
            hand_resources=self._json_dump(self.hand.resources.__to_object__()),
            thief_targets_payload=self._json_dump(thief_targets_payload),
            pydantic_move_model=self._schema_json(models.ThiefMoveModel),
        )
        response_content = self._request_llm(prompt)
        parsed_response = self._parse_model(models.ThiefMoveModel, response_content)

        if parsed_response is None:
            return self._default_move_thief()

        terrain = parsed_response.terrain
        player = parsed_response.player

        if terrain < 0 or terrain >= len(self.board.terrain):
            return self._default_move_thief()
        if self.board.terrain[terrain]["has_thief"]:
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
        vp_card = self._first_card_by_effect(DevelopmentCardConstants.VICTORY_POINT_EFFECT)
        own_board_points = sum(2 if node["has_city"] else 1 for node in self.board.nodes if node["player"] == self.id)
        hidden_vp_cards = len(self.development_cards_hand.find_card_by_effect(DevelopmentCardConstants.VICTORY_POINT_EFFECT))
        if vp_card is not None and own_board_points + hidden_vp_cards >= 10:
            return vp_card

        knight = self._first_card_by_effect(DevelopmentCardConstants.KNIGHT_EFFECT)
        if knight is not None and self._blocked_pips_by_thief() >= 6:
            return knight

        return None

    def on_commerce_phase(self):
        if self._commerce_actions >= 3:
            return None

        if self.hand.resources.has_more(BuildConstants.CITY) and self._is_build_legal(BuildConstants.CITY):
            return None
        if self.hand.resources.has_more(BuildConstants.TOWN) and self._is_build_legal(BuildConstants.TOWN):
            return None

        knight = self._first_card_by_effect(DevelopmentCardConstants.KNIGHT_EFFECT)
        if knight is not None and self._blocked_pips_by_thief() >= 5:
            self._commerce_actions += 1
            return knight

        year_of_plenty = self._first_card_by_effect(DevelopmentCardConstants.YEAR_OF_PLENTY_EFFECT)
        if year_of_plenty is not None:
            for building in (BuildConstants.CITY, BuildConstants.TOWN, BuildConstants.CARD):
                if self._is_build_legal(building) and 0 < self._missing_count(building) <= 2:
                    self._commerce_actions += 1
                    return year_of_plenty

        monopoly = self._first_card_by_effect(DevelopmentCardConstants.MONOPOLY_EFFECT)
        if monopoly is not None:
            _, mono_score = self._best_monopoly_material()
            if mono_score >= 7.5:
                self._commerce_actions += 1
                return monopoly

        road_building = self._first_card_by_effect(DevelopmentCardConstants.ROAD_BUILDING_EFFECT)
        best_road, best_road_score = self._best_road()
        if road_building is not None and best_road is not None:
            if best_road_score >= 28 and not self.hand.resources.has_more(BuildConstants.TOWN):
                self._commerce_actions += 1
                return road_building

        harbor_trade = self._best_harbor_trade()
        if harbor_trade is not None:
            self._commerce_actions += 1
            return harbor_trade

        player_trade = self._best_player_trade_offer()
        if player_trade is not None:
            self._commerce_actions += 1
            return player_trade

        return None

    def on_build_phase(self, board_instance):
        self.board = board_instance

        if self.hand.resources.has_more(BuildConstants.CITY):
            city_node = self._best_city_node(board_instance=board_instance)
            if city_node is not None:
                return {"building": BuildConstants.CITY, "node_id": city_node}

        if self.hand.resources.has_more(BuildConstants.TOWN):
            town_node = self._best_town_node(board_instance=board_instance)
            if town_node is not None:
                return {"building": BuildConstants.TOWN, "node_id": town_node}

        # Si no podemos ejecutar jugadas premium, intentamos usar cartas de desarrollo para desbloquearlas.
        knight = self._first_card_by_effect(DevelopmentCardConstants.KNIGHT_EFFECT)
        if knight is not None and self._blocked_pips_by_thief(board_instance=board_instance) >= 5:
            return knight

        year_of_plenty = self._first_card_by_effect(DevelopmentCardConstants.YEAR_OF_PLENTY_EFFECT)
        if year_of_plenty is not None:
            for building in (BuildConstants.CITY, BuildConstants.TOWN, BuildConstants.CARD):
                if self._is_build_legal(building, board_instance=board_instance) and 0 < self._missing_count(building) <= 2:
                    return year_of_plenty

        monopoly = self._first_card_by_effect(DevelopmentCardConstants.MONOPOLY_EFFECT)
        if monopoly is not None:
            _, mono_score = self._best_monopoly_material()
            if mono_score >= 8:
                return monopoly

        road_building = self._first_card_by_effect(DevelopmentCardConstants.ROAD_BUILDING_EFFECT)
        best_road, best_road_score = self._best_road(board_instance=board_instance)
        if road_building is not None and best_road is not None and best_road_score >= 30:
            return road_building

        can_buy_card = self.hand.resources.has_more(BuildConstants.CARD)
        can_build_road = self.hand.resources.has_more(BuildConstants.ROAD) and best_road is not None

        if can_buy_card and can_build_road:
            production = self._player_resource_pips(self.id, board_instance=board_instance, include_blocked=True)
            ore_focus = production[MaterialConstants.MINERAL] + production[MaterialConstants.CEREAL] + production[MaterialConstants.WOOL]
            road_focus = production[MaterialConstants.WOOD] + production[MaterialConstants.CLAY]
            if ore_focus >= road_focus or best_road_score < 25:
                return {"building": BuildConstants.CARD}

        if can_build_road:
            return {
                "building": BuildConstants.ROAD,
                "node_id": best_road["starting_node"],
                "road_to": best_road["finishing_node"],
            }

        if can_buy_card:
            return {"building": BuildConstants.CARD}

        return None

    def on_game_start(self, board_instance):
        self.board = board_instance
        valid_nodes = self.board.valid_starting_nodes()
        if not valid_nodes:
            return super().on_game_start(board_instance)
        valid_roads = {node_id: self.board.nodes[node_id]["adjacent"] for node_id in valid_nodes}

        existing_pips = self._player_resource_pips(self.id, board_instance=board_instance, include_blocked=True)
        existing_numbers = self._player_numbers(self.id, board_instance=board_instance)

        prompt = self._prompt_by_size("GAME_START_PROMPT").format(
            player_id=self.id,
            board_state=self._json_dump(self._board_state(board_instance)),
            valid_starting_nodes=self._json_dump(valid_nodes),
            node_road_options=self._json_dump(valid_roads),
            existing_pips=self._json_dump(existing_pips),
            existing_numbers=self._json_dump(list(existing_numbers)),
            pydantic_game_start_model=self._schema_json(models.GameStartModel),
        )

        response_content = self._request_llm(prompt)
        parsed_response = self._parse_model(models.GameStartModel, response_content)

        if parsed_response is not None:
            node_id = parsed_response.node_id
            road_to = parsed_response.road_to
            if node_id in valid_nodes and road_to in self.board.nodes[node_id]["adjacent"]:
                return node_id, road_to

    def on_monopoly_card_use(self):
        material, _ = self._best_monopoly_material()
        return material

    def on_road_building_card_use(self):
        first_road, _ = self._best_road()
        if first_road is None:
            return None

        simulated_board = deepcopy(self.board)
        simulated_board.build_road(self.id, first_road["starting_node"], first_road["finishing_node"])

        second_road, _ = self._best_road(board_instance=simulated_board)
        if second_road is None:
            return {
                "node_id": first_road["starting_node"],
                "road_to": first_road["finishing_node"],
                "node_id_2": None,
                "road_to_2": None,
            }

        return {
            "node_id": first_road["starting_node"],
            "road_to": first_road["finishing_node"],
            "node_id_2": second_road["starting_node"],
            "road_to_2": second_road["finishing_node"],
        }

    def on_year_of_plenty_card_use(self):
        first, second = self._best_year_of_plenty_pair()
        return {"material": first, "material_2": second}
