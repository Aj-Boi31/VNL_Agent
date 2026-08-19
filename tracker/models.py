from django.db import models


class Team(models.Model):
    """Maps to the existing `teams` table created by the ingestion script."""
    id = models.AutoField(primary_key=True)
    name = models.CharField(max_length=200, unique=True)
    country = models.CharField(max_length=100, blank=True, null=True)
    badge_url = models.CharField(max_length=500, blank=True, null=True)

    class Meta:
        managed = False  # Django won't create/alter this table; ingestion script owns it
        db_table = "teams"

    def __str__(self):
        return self.name


class Match(models.Model):
    """Maps to the existing `matches` table created by the ingestion script."""
    id = models.AutoField(primary_key=True)
    season = models.CharField(max_length=20)          # e.g. "2026"
    round_name = models.CharField(max_length=100, blank=True, null=True, db_column="round")
    event_date = models.DateField(null=True, blank=True, db_column="date")
    home_team = models.ForeignKey(
        Team, on_delete=models.DO_NOTHING, related_name="home_matches",
        db_column="home_team_id", null=True
    )
    away_team = models.ForeignKey(
        Team, on_delete=models.DO_NOTHING, related_name="away_matches",
        db_column="away_team_id", null=True
    )
    home_score = models.IntegerField(null=True, blank=True)
    away_score = models.IntegerField(null=True, blank=True)
    status = models.CharField(max_length=50, blank=True, null=True)  # e.g. "Match Finished"

    class Meta:
        managed = False
        db_table = "matches"

    def __str__(self):
        return f"{self.home_team} vs {self.away_team} ({self.season})"


class Standing(models.Model):
    """Maps to the existing `standings` table -- official VNL table data
    pulled directly from the API rather than computed locally."""
    season = models.CharField(max_length=20)
    team = models.ForeignKey(Team, on_delete=models.DO_NOTHING, db_column="team_id")
    position = models.IntegerField(null=True, blank=True)
    wins = models.IntegerField(null=True, blank=True)
    loses = models.IntegerField(null=True, blank=True)
    points = models.IntegerField(null=True, blank=True)
    games_played = models.IntegerField(null=True, blank=True)
    scored_points = models.IntegerField(null=True, blank=True)
    received_points = models.IntegerField(null=True, blank=True)

    class Meta:
        managed = False
        db_table = "standings"
        unique_together = (("season", "team"),)

    def __str__(self):
        return f"{self.season} #{self.position} {self.team}"
