"""WHO historical disease data message model."""

from uagents import Model


class WhoUpdate(Model):
    region: str
    covid_30d_cases: int
    covid_30d_deaths: int
    covid_trend: str        # "rising" | "stable" | "falling"
    covid_trend_pct: float  # % change last 7d vs prior 7d
    source: str             # attribution (disease.sh / WHO/JHU)
