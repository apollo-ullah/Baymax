"""
Shared message schema for the weather agent.

Other agents import WeatherUpdate to receive weather data over the uAgent
network. Keeping models separate from agent.py lets the receiving agent
import them without importing the agent itself.
"""

from uagents import Model


class WeatherUpdate(Model):
    temperature: float
    windspeed: float
    winddirection: float
    weathercode: int
    time: str
