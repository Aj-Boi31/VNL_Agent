from django.urls import path
from . import views

app_name = "tracker"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("ask/", views.ask_agent, name="ask_agent"),
    path("team/<str:team_name>/", views.team_detail, name="team_detail"),
]
