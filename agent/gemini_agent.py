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

# Gemini model names get retired periodically -- override via env var if this
# one stops working (e.g. export GEMINI_MODEL="gemini-X-flash").
MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.6-flash")

SYSTEM_INSTRUCTION = """You are a VNL (FIVB Volleyball Men's Nations League) data
assistant. You answer questions about the 2026 season using the provided
tools -- never guess or make up match results, scores, or standings.

If a tool returns an error (e.g. team not found), tell the user clearly and
suggest checking the spelling of the team name.

Keep answers concise and conversational. When relevant, mention specific
scores, dates, or records from the tool result rather than vague summaries.

Note: `get_team_results` includes finals-bracket matches, so a team's
record there may look different from `get_standings` (which only reflects
the 12-game preliminary round). If this discrepancy is relevant to the
question, briefly explain why.
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


def new_chat(client):
    """Start a new chat session with the agent's tools and system instruction
    wired up. Automatic function calling runs through chat.send_message,
    per Google's current recommendation (calling AFC via generate_content
    directly is deprecated).

    `client` must be kept alive (e.g. via a `with` block) for as long as
    this chat is used -- see the module-level note on the httpx
    "client is closed" issue.
    """
    return client.chats.create(
        model=MODEL,
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM_INSTRUCTION,
            tools=TOOLS,
        ),
    )


def ask(question: str, chat=None) -> str:
    """Ask the agent a natural-language question. Returns the final text answer.

    If no chat session is passed in, opens a short-lived client + chat for
    just this one question (used by the Django view, where each HTTP
    request is independent). For multi-turn conversations, create a chat
    with new_chat() once and reuse it across calls -- see main() below.

    Note on the `with` block: recent google-genai versions (1.39.0+) close
    their underlying HTTP client when the Client object is garbage
    collected. If the client isn't kept alive for the full duration of the
    request, you can hit "Cannot send a request, as the client has been
    closed." Wrapping usage in `with genai.Client(...) as client:` is
    Google's documented fix. See: https://github.com/googleapis/python-genai/issues/1763
    """
    if chat is not None:
        response = chat.send_message(question)
        return response.text

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError(
            "GEMINI_API_KEY environment variable is not set. "
            "Get a free key at https://aistudio.google.com/apikey and set it before running this script."
        )

    with genai.Client(api_key=api_key) as client:
        one_off_chat = new_chat(client)
        response = one_off_chat.send_message(question)
        return response.text


def main():
    """Interactive CLI loop for manual testing before wiring into Django.
    Keeps one client + chat alive for the whole session (multi-turn memory)."""
    client = _get_client()
    with client:
        chat = new_chat(client)
        print("VNL Agent (2026 season). Type a question, or 'quit' to exit.\n")
        print("Examples:")
        print("  - What's Poland's record this season?")
        print("  - Who's leading the standings?")
        print("  - How has Japan played in their last 5 games?")
        print("  - Poland vs Japan head to head\n")

        while True:
            question = input("> ").strip()
            if question.lower() in {"quit", "exit", "q"}:
                break
            if not question:
                continue

            try:
                answer = ask(question, chat=chat)
                print(f"\n{answer}\n")
            except Exception as e:
                print(f"\nError: {e}\n")


if __name__ == "__main__":
    main()
