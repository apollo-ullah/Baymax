import asyncio
import os
from datetime import datetime
from uuid import uuid4

asyncio.set_event_loop(asyncio.new_event_loop())

from uagents import Agent, Context, Protocol
from uagents_core.contrib.protocols.chat import (
    ChatAcknowledgement, ChatMessage, TextContent, chat_protocol_spec,
)

README_PATH = os.path.join(os.path.dirname(__file__), "README.md")

agent = Agent(
    name="baymax_hello",
    port=8001,
    seed=os.getenv("AGENT_SEED_PHRASE"),
    mailbox=True,
    readme_path=README_PATH,
    publish_agent_details=True,
)
chat_proto = Protocol(spec=chat_protocol_spec)

def text_msg(text):
    return ChatMessage(timestamp=datetime.utcnow(), msg_id=uuid4(),
                       content=[TextContent(type="text", text=text)])

@chat_proto.on_message(ChatMessage)
async def handle(ctx: Context, sender: str, msg: ChatMessage):
    await ctx.send(sender, ChatAcknowledgement(
        timestamp=datetime.utcnow(), acknowledged_msg_id=msg.msg_id))
    for item in msg.content:
        if isinstance(item, TextContent):
            await ctx.send(sender, text_msg(f"Baymax here. You said: {item.text}"))

@chat_proto.on_message(ChatAcknowledgement)
async def ack(ctx: Context, sender: str, msg: ChatAcknowledgement):
    ctx.logger.info(f"ack from {sender}")

agent.include(chat_proto, publish_manifest=True)

if __name__ == "__main__":
    agent.run()