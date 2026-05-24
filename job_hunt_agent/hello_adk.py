import asyncio

from .agent import run_prompt


async def main() -> None:
    response = await run_prompt(
        "In one sentence, what makes a referral request message effective?",
        session_id="hello-adk",
    )
    print("\n--- Agent response ---")
    print(response)


if __name__ == "__main__":
    asyncio.run(main())
