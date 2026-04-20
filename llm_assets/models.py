from typing import Literal

from pydantic import BaseModel, Field

class TradeOfferModel(BaseModel):
    long_term_plan: str = Field(..., description="The new long-term plan based on the previous one after making the trade offer")
    short_term_plan: str = Field(..., description="The new short-term plan based on the previous one after making the trade offer")
    gives: list[int] = Field(..., min_length=5, max_length=5, description="The resources you want to give in the trade offer, being a string of 5 integers where each integer represents the amount of a specific resource (cereal, mineral, clay, wood, wool) you want to give. For example, '[2,0,0,0,0]' means you want to give 2 units of cereal and 0 units of the other resources.")
    receives: list[int] = Field(..., min_length=5, max_length=5, description="The resources you want to receive in the trade offer, being a string of 5 integers where each integer represents the amount of a specific resource (cereal, mineral, clay, wood, wool) you want to receive. For example, '[1,0,0,0,0]' means you want to receive 1 unit of cereal and 0 units of the other resources.")


class PlanModel(BaseModel):
    long_term_plan: str = Field(..., description="High-level strategy for the rest of the game.")
    short_term_plan: str = Field(..., description="Short horizon tactical plan for the next turns.")


class DevelopmentCardDecisionModel(BaseModel):
    long_term_plan: str = Field(..., description="Updated long-term plan.")
    short_term_plan: str = Field(..., description="Updated short-term plan.")
    play_development_card: bool = Field(..., description="Whether to play a development card now.")
    development_card_effect: int | None = Field(
        default=None,
        description=(
            "Effect id of the card to play if play_development_card is true. "
            "0 Knight, 1 Victory Point, 2 Road Building, 3 Year of Plenty, 4 Monopoly."
        ),
    )


class ThiefMoveModel(BaseModel):
    long_term_plan: str = Field(..., description="Updated long-term plan.")
    short_term_plan: str = Field(..., description="Updated short-term plan.")
    terrain: int = Field(..., ge=0, le=18, description="Terrain id where the thief should move.")
    player: int = Field(..., ge=-1, le=3, description="Player id to steal from, or -1.")


class CommerceDecisionModel(BaseModel):
    long_term_plan: str = Field(..., description="Updated long-term plan.")
    short_term_plan: str = Field(..., description="Updated short-term plan.")
    action: Literal["none", "player_trade", "harbor_trade", "play_development_card"] = Field(
        ..., description="Commerce action to execute."
    )
    gives: list[int] | None = Field(
        default=None,
        min_length=5,
        max_length=5,
        description="Required when action is player_trade. 5-length resource vector to give.",
    )
    receives: list[int] | None = Field(
        default=None,
        min_length=5,
        max_length=5,
        description="Required when action is player_trade. 5-length resource vector to receive.",
    )
    harbor_gives: int | None = Field(
        default=None,
        ge=0,
        le=4,
        description="Required when action is harbor_trade. Material id to give to bank/harbor.",
    )
    harbor_receives: int | None = Field(
        default=None,
        ge=0,
        le=4,
        description="Required when action is harbor_trade. Material id to receive from bank/harbor.",
    )
    development_card_effect: int | None = Field(
        default=None,
        description="Required when action is play_development_card. Development card effect id.",
    )


class BuildDecisionModel(BaseModel):
    long_term_plan: str = Field(..., description="Updated long-term plan.")
    short_term_plan: str = Field(..., description="Updated short-term plan.")
    action: Literal[
        "none",
        "build_town",
        "build_city",
        "build_road",
        "build_card",
        "play_development_card",
    ] = Field(..., description="Build phase action.")
    node_id: int | None = Field(default=None, description="Node id for town/city/road start.")
    road_to: int | None = Field(default=None, description="Node id for road end.")
    development_card_effect: int | None = Field(
        default=None,
        description="Required when action is play_development_card. Effect id.",
    )


class GameStartModel(BaseModel):
    long_term_plan: str = Field(..., description="Initial long-term strategy selected for this game.")
    short_term_plan: str = Field(..., description="Initial short-term tactical plan.")
    node_id: int = Field(..., ge=0, le=53, description="Settlement node id for initial placement.")
    road_to: int = Field(..., ge=0, le=53, description="Adjacent node id for initial road.")


class MonopolyDecisionModel(BaseModel):
    long_term_plan: str = Field(..., description="Updated long-term plan.")
    short_term_plan: str = Field(..., description="Updated short-term plan.")
    material: int = Field(..., ge=0, le=4, description="Material id for Monopoly card.")


class RoadBuildingDecisionModel(BaseModel):
    long_term_plan: str = Field(..., description="Updated long-term plan.")
    short_term_plan: str = Field(..., description="Updated short-term plan.")
    node_id: int | None = Field(default=None, ge=0, le=53, description="Start node of first road.")
    road_to: int | None = Field(default=None, ge=0, le=53, description="End node of first road.")
    node_id_2: int | None = Field(default=None, ge=0, le=53, description="Start node of second road.")
    road_to_2: int | None = Field(default=None, ge=0, le=53, description="End node of second road.")


class YearOfPlentyDecisionModel(BaseModel):
    long_term_plan: str = Field(..., description="Updated long-term plan.")
    short_term_plan: str = Field(..., description="Updated short-term plan.")
    material: int = Field(..., ge=0, le=4, description="First material id to collect.")
    material_2: int = Field(..., ge=0, le=4, description="Second material id to collect.")
