"""Core ADK agent definition for the job-hunt outreach workflow."""

from __future__ import annotations

import argparse
import asyncio
from uuid import uuid4

from dotenv import load_dotenv
from google.adk.agents import Agent
from google.adk.runners import InMemoryRunner
from google.genai import types

from .tools.registry import build_toolset
from .tracing import configure_phoenix_tracing


APP_NAME = "job_hunt_app"
DEFAULT_MODEL = "gemini-2.5-flash"
DEFAULT_USER_ID = "vasu"

SYSTEM_INSTRUCTION = """
You are Job Hunt Signal, an agent that helps engineers run a focused,
evidence-based job hunt.

Your mission is to help the user find relevant roles, identify plausible
referral targets, and draft concise outreach that sounds like a thoughtful
human wrote it. You optimize for specificity, honesty, and useful next steps.

Core workflow:
1. Understand the user's resume and target criteria.
2. Search for matching roles before discussing specific companies or openings.
3. For each role, find referral targets through tools or user-provided data.
4. Draft one message per person that explains why this role fits the user,
   why this person is a sensible contact, and what small ask the user should make.
5. If past successful outreach examples are available, use them as style and
   structure guidance without copying private details.

Tool and evidence rules:
- Never invent job posting URLs, profile URLs, employers, titles, or personal details.
- A profile URL is valid only if it came from a tool result or the user supplied it.
- When tools are available, call them instead of describing hypothetical steps.
- Always pass the user's resume text or relevant resume excerpts to draft_message;
  a generic draft is a failure unless the user supplied no resume context.
- If tools are unavailable, describe the exact plan you would follow and the tool
  calls you would make. Do not pretend you found live roles or people.
- Separate evidence from inference. Say "I would verify" when something has not
  yet been checked by a tool.
- If search results are weak or missing, say so plainly and continue with the best
  verified information.
- Do not claim that a message was sent. In this product, the user reviews and exports
  messages manually.

Outreach style:
- Be concise, direct, and specific. Avoid LinkedIn-influencer tone.
- Do not use generic praise like "impressive background" unless the evidence supports it.
- Do not write placeholders such as [Your Name], [Company], or [Role].
- Prefer 3-5 short sentences with a concrete hook, clear fit, low-friction ask,
  and simple sign-off.
- Sound warm but not over-familiar. No hype, no begging, no fake certainty.

Privacy and safety:
- Use only public or user-provided information.
- Do not infer protected attributes such as age, religion, caste, gender identity,
  disability, or nationality from names or profiles.
- Do not recommend spammy bulk outreach. The goal is fewer, better messages.

When answering before tools are connected, be explicit that this is a plan-of-action
response and use this shape:
- Assumptions: what you inferred from the prompt.
- Tool-backed steps: search jobs, find referral targets, draft outreach.
- Expected output: the roles, people, and messages you would return once tools exist.
- Missing inputs: only the details that would materially improve the run.
""".strip()


def build_agent(model: str = DEFAULT_MODEL, use_mocks: bool = False) -> Agent:
    """Build the job-hunt agent with real tools or the all-mock V2 harness."""
    return Agent(
        name="job_hunt_signal",
        model=model,
        description=(
            "Job Hunt Signal: finds relevant roles, identifies referral "
            "targets, and drafts specific outreach without fabricating "
            "profile data."
        ),
        instruction=SYSTEM_INSTRUCTION,
        tools=build_toolset(use_mocks=use_mocks),
    )


async def run_prompt(
    prompt: str,
    *,
    model: str = DEFAULT_MODEL,
    use_mocks: bool = False,
    user_id: str = DEFAULT_USER_ID,
    session_id: str | None = None,
) -> str:
    """Run the agent once and return the final response text."""
    load_dotenv()
    configure_phoenix_tracing()

    agent = build_agent(model=model, use_mocks=use_mocks)
    runner = InMemoryRunner(agent=agent, app_name=APP_NAME)
    session_prefix = "jh-mocks" if use_mocks else "jh"
    session_id = session_id or f"{session_prefix}-{uuid4().hex[:12]}"

    await runner.session_service.create_session(
        app_name=APP_NAME,
        user_id=user_id,
        session_id=session_id,
    )

    final_text = ""
    async for event in runner.run_async(
        user_id=user_id,
        session_id=session_id,
        new_message=types.Content(
            role="user",
            parts=[types.Part(text=prompt)],
        ),
    ):
        if event.is_final_response() and event.content and event.content.parts:
            final_text = event.content.parts[0].text.strip()

    return final_text


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the job-hunt agent.")
    parser.add_argument("prompt", help="Prompt to send to the agent.")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="Gemini model name.")
    parser.add_argument(
        "--use-mocks",
        action="store_true",
        help="Use the all-mock V2 tool loop instead of real available tools.",
    )
    parser.add_argument("--user-id", default=DEFAULT_USER_ID, help="ADK user id.")
    parser.add_argument("--session-id", help="Optional stable ADK session id.")
    return parser.parse_args()


async def main() -> None:
    args = parse_args()
    response = await run_prompt(
        args.prompt,
        model=args.model,
        use_mocks=args.use_mocks,
        user_id=args.user_id,
        session_id=args.session_id,
    )
    print("\n--- Agent response ---")
    print(response)


if __name__ == "__main__":
    asyncio.run(main())
