import asyncio
from dotenv import load_dotenv
load_dotenv()

from google.adk.agents import Agent
from google.adk.runners import InMemoryRunner
from google.genai import types

try:
    from job_hunt_agent.tracing import configure_phoenix_tracing
except ModuleNotFoundError:
    from tracing import configure_phoenix_tracing

# 1. Wire up Phoenix tracing -- BEFORE creating the agent
configure_phoenix_tracing()

# 2. Define a trivial agent (no tools yet, just to verify the pipe)
agent = Agent(
    name="job_hunt_agent",
    model="gemini-2.5-flash",
    description="Helps with the job hunt.",
    instruction="You are a helpful job-search assistant. Be concise.",
)

# 3. Run it once
async def main():
    runner = InMemoryRunner(agent=agent, app_name="job_hunt_app")
    await runner.session_service.create_session(
        app_name="job_hunt_app", user_id="vasu", session_id="s1"
    )
    async for event in runner.run_async(
        user_id="vasu",
        session_id="s1",
        new_message=types.Content(
            role="user",
            parts=[types.Part(text="In one sentence, what makes a referral request message effective?")],
        ),
    ):
        if event.is_final_response():
            print("\n--- Agent response ---")
            print(event.content.parts[0].text.strip())

if __name__ == "__main__":
    asyncio.run(main())
