from pydantic import BaseModel, Field

class ThiefMoveModel(BaseModel):
    long_term_plan: str = Field(..., description="Updated long-term plan.")
    short_term_plan: str = Field(..., description="Updated short-term plan.")
    terrain: int = Field(..., ge=0, le=18, description="Terrain id where the thief should move.")
    player: int = Field(..., ge=-1, le=3, description="Player id to steal from, or -1.")


class GameStartModel(BaseModel):
    long_term_plan: str = Field(..., description="Initial long-term strategy selected for this game.")
    short_term_plan: str = Field(..., description="Initial short-term tactical plan.")
    node_id: int = Field(..., ge=0, le=53, description="Settlement node id for initial placement.")
    road_to: int = Field(..., ge=0, le=53, description="Adjacent node id for initial road.")
