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
from google.genai import types, errors
from dotenv import load_dotenv

from agent.queries import (
    get_team_results,
    get_team_roster,
    get_head_to_head,
    get_recent_form,
    get_standings,
)

load_dotenv()  # picks up .env in the current working directory

# Gemini's free-tier quota is tracked per model, per project -- so if one
# model runs out for the day, a *different* model still has its own
# untouched quota bucket. This chain tries each in order and only moves to
# the next on a 429 (quota/rate limit), effectively multiplying the usable
# free daily budget at no cost.
#
# Override with a comma-separated list via GEMINI_MODEL_CHAIN, e.g.:
#   GEMINI_MODEL_CHAIN=gemini-3.5-flash-lite,gemini-3.6-flash
#
# GEMINI_MODEL (singular, legacy) is still honored and takes priority as
# the first model tried, for backwards compatibility.
_default_chain = "gemini-3.5-flash-lite,gemini-3.6-flash,gemini-2.5-flash-lite,gemini-3.1-flash-lite"
_chain_env = os.environ.get("GEMINI_MODEL_CHAIN", _default_chain)
MODEL_CHAIN = [m.strip() for m in _chain_env.split(",") if m.strip()]

_legacy_model = os.environ.get("GEMINI_MODEL")
if _legacy_model and _legacy_model not in MODEL_CHAIN:
    MODEL_CHAIN.insert(0, _legacy_model)
elif _legacy_model in MODEL_CHAIN:
    MODEL_CHAIN.remove(_legacy_model)
    MODEL_CHAIN.insert(0, _legacy_model)

MODEL = MODEL_CHAIN[0]  # kept for backwards compatibility / display purposes

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

`get_team_roster` returns player name, position, height, weight, age, and
nationality only. It does NOT include individual performance statistics
(kills, blocks, aces, digs, points, etc.) -- that data isn't available in
this system. If asked for a player's stats, say so plainly rather than
guessing or estimating.
"""

TOOLS = [get_team_results, get_head_to_head, get_recent_form, get_standings, get_team_roster]


def _get_client():
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError(
            "GEMINI_API_KEY environment variable is not set. "
            "Get a free key at https://aistudio.google.com/apikey and set it before running this script."
        )
    return genai.Client(api_key=api_key)


def new_chat(client, model=None):
    """Start a new chat session with the agent's tools and system instruction
    wired up. Automatic function calling runs through chat.send_message,
    per Google's current recommendation (calling AFC via generate_content
    directly is deprecated).

    `client` must be kept alive (e.g. via a `with` block) for as long as
    this chat is used -- see the module-level note on the httpx
    "client is closed" issue.

    thinking_level is set to LOW: this task is simple (pick 1 of 4 known
    tools, format a short answer), so the model's default "medium" reasoning
    effort is mostly wasted latency here. LOW keeps enough reasoning to
    reliably pick the right tool while cutting response time noticeably.
    """
    model = model or MODEL
    return client.chats.create(
        model=model,
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM_INSTRUCTION,
            tools=TOOLS,
            thinking_config=types.ThinkingConfig(thinking_level="LOW"),
        ),
    )


def _raw_send(chat, question: str) -> str:
    """Send a message with no error translation -- lets ClientError (incl.
    429s) propagate so callers can decide whether to retry with a
    different model."""
    response = chat.send_message(question)
    return response.text


def _send(chat, question: str) -> str:
    """Send a message and translate a 429 into a clear, user-facing message.
    Used by main() where there's no model fallback chain to fall back on
    (a chat's conversation history is tied to one specific model)."""
    try:
        return _raw_send(chat, question)
    except errors.ClientError as e:
        if e.code == 429:
            raise RuntimeError(
                "The free Gemini quota for this model has been used up for today "
                "(or this minute). Check your live limits at https://aistudio.google.com/, "
                "or try again shortly / tomorrow."
            ) from e
        raise


def ask(question: str, chat=None) -> str:
    """Ask the agent a natural-language question. Returns the final text answer.

    If no chat session is passed in, opens a short-lived client for just
    this one question and tries each model in MODEL_CHAIN in order,
    moving to the next only on a 429 (quota exhausted for that model).
    This is what the Django view uses -- each HTTP request is independent,
    so there's no multi-turn history to preserve across a model switch.

    If a chat IS passed in (see new_chat() + main() below, for multi-turn
    CLI use), no fallback happens -- the chat's history is tied to one
    model, so switching mid-conversation isn't possible without losing
    that context.

    Note on the `with` block: recent google-genai versions (1.39.0+) close
    their underlying HTTP client when the Client object is garbage
    collected. Wrapping usage in `with genai.Client(...) as client:` is
    Google's documented fix for "Cannot send a request, as the client has
    been closed." See: https://github.com/googleapis/python-genai/issues/1763
    """
    if chat is not None:
        return _send(chat, question)

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError(
            "GEMINI_API_KEY environment variable is not set. "
            "Get a free key at https://aistudio.google.com/apikey and set it before running this script."
        )

    last_error = None
    with genai.Client(api_key=api_key) as client:
        for model in MODEL_CHAIN:
            try:
                one_off_chat = new_chat(client, model=model)
                return _raw_send(one_off_chat, question)
            except errors.ClientError as e:
                if e.code == 429:
                    last_error = e
                    continue  # try the next model in the chain
                raise

    # every model in the chain was rate-limited/out of quota
    tried = ", ".join(MODEL_CHAIN)
    raise RuntimeError(
        f"All free-tier models are currently rate-limited or out of quota today "
        f"(tried: {tried}). Check your live limits at https://aistudio.google.com/, "
        f"or try again shortly / tomorrow."
    ) from last_error


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
