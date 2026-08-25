import json

from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from agent.queries import get_standings, get_team_roster
from agent.gemini_agent import ask as agent_ask


def dashboard(request):
    """Main page: standings table + ask-the-agent box."""
    standings_data = get_standings()
    context = {
        "standings": standings_data.get("standings", []),
        "standings_error": standings_data.get("error"),
        "season": standings_data.get("season", "2026"),
    }
    return render(request, "tracker/dashboard.html", context)


def team_detail(request, team_name):
    """Team roster page, linked from each standings row."""
    roster_data = get_team_roster(team_name)

    # Also pull this team's standings row, if available, for the W-L/
    # position line under the header -- not an error if missing, since
    # get_team_roster already validated the team name exists.
    standing = None
    standings_data = get_standings()
    for row in standings_data.get("standings", []):
        if row.get("team", "").lower() == roster_data.get("team", team_name).lower():
            standing = row
            break

    context = {
        "team_name": roster_data.get("team", team_name),
        "roster": roster_data.get("roster", []),
        "roster_error": roster_data.get("error"),
        "standing": standing,
    }
    return render(request, "tracker/team_detail.html", context)


@csrf_exempt  # simple demo endpoint; see note in README about tightening this for real deployment
@require_POST
def ask_agent(request):
    """POST endpoint the dashboard's question box calls via fetch().
    Expects JSON body: {"question": "..."}
    Returns JSON: {"answer": "..."} or {"error": "..."}
    """
    try:
        body = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid request body."}, status=400)

    question = (body.get("question") or "").strip()
    if not question:
        return JsonResponse({"error": "Question cannot be empty."}, status=400)

    if len(question) > 500:
        return JsonResponse({"error": "Question is too long (max 500 characters)."}, status=400)

    try:
        answer = agent_ask(question)
        return JsonResponse({"answer": answer})
    except RuntimeError as e:
        # e.g. missing GEMINI_API_KEY
        return JsonResponse({"error": str(e)}, status=500)
    except Exception as e:
        return JsonResponse({"error": f"Something went wrong: {e}"}, status=500)
