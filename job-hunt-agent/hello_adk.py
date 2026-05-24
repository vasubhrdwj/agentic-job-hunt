import asyncio
from dotenv import load_dotenv
load_dotenv()

from phoenix.otel import register
from openinference.instrumentation.google_adk import GoogleADKInstrumentor

from google.adk.agents import Agent
from google.adk.runners import InMemoryRunner
from google.genai import types

# 1. Wire up Phoenix tracing -- BEFORE creating the agent
tracer_provider = register(project_name="job-hunt-agent", auto_instrument=False, protocol='http/protobuf')
GoogleADKInstrumentor().instrument(tracer_provider=tracer_provider)

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

asyncio.run(main())