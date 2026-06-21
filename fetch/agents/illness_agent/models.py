"""
Shared message schema for the illness (CDC/WHO) agent.

The intelligence agent imports IllnessUpdate to receive illness-activity
signals that feed demand forecasting.
"""

from uagents import Model


class IllnessUpdate(Model):
    region: str
    influenza: str   # one of: Minimal | Low | Moderate | High | Very High
    covid: str
    rsv: str
    source: str
