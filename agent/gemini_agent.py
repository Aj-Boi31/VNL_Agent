"""
The Gemini agent layer. Takes a natural-language question, lets Gemini pick
the right query function from agent/queries.py, executes it, and returns a
natural-language answer grounded in the real result.

Uses the google-genai SDK's automatic function calling: pass plain Python
functions as `tools`, and the SDK reads each function's type hints and
docstring to build the schema, decides which (if any) to call, runs it,
and feeds the result back to the model for a final answer.

Requires a Gemini API key (free tier): https://aistudio.google.com/apikey
Set it as an environment variable before running:
    export GEMINI_API_KEY="your-key-here"

Run interactively:
    python agent/gemini_agent.py
"""

import os
from google import genai
from google.genai import types
from dotenv import load_dotenv

from agent.queries import (
    get_team_results,
    get_head_to_head,
    get_recent_form,
    get_standings,
)

load_dotenv()  # picks up .env in the current working directory

MODEL = "gemini-2.5-flash"

SYSTEM_INSTRUCTION = """You are a VNL (FIVB Volleyball Men's Nations League) data
assistant. You answer questions about the 2026 season using the provided
tools -- never guess or make up match results, scores, or standings.

If a tool returns an error (e.g. team not found), tell the user clearly and
suggest checking the spelling of the team name.

Keep answers concise and conversational.
"""

TOOLS = [get_team_results, get_head_to_head, get_recent_form, get_standings]


def _get_client():
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError(
            "GEMINI_API_KEY environment variable is not set. "
            "Get a free key at https://aistudio.google.com/apikey and set it before running this script."
        )
    return genai.Client(api_key=api_key)


def ask(question: str, client=None) -> str:
    """Ask the agent a natural-language question. Returns the final text answer.

    Automatic function calling handles the full loop internally: Gemini may
    call zero, one, or multiple tools before producing a final answer.
    """
    client = client or _get_client()

    response = client.models.generate_content(
        model=MODEL,
        contents=question,
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM_INSTRUCTION,
            tools=TOOLS,
        ),
    )
    return response.text


def main():
    client = _get_client()
    print("VNL Agent (2026 season). Type a question, or 'quit' to exit.\n")

    while True:
        question = input("> ").strip()
        if question.lower() in {"quit", "exit", "q"}:
            break
        if not question:
            continue
        try:
            answer = ask(question, client=client)
            print(f"\n{answer}\n")
        except Exception as e:
            print(f"\nError: {e}\n")


if __name__ == "__main__":
    main()
