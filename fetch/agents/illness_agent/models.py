"""
Shared message schema for the illness agent.

The intelligence agent imports IllnessUpdate to receive illness levels and map
each illness -> inventory items.
"""

from uagents import Model


class IllnessUpdate(Model):
    region: str
    # illness name -> level, e.g. {"influenza": "High", "covid": "Moderate"}
    illnesses: dict
