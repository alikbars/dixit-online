
from django.db import models
from django.dispatch import receiver
from django.db.models.signals import post_save, post_delete
from django.utils.translation import ugettext as _
from django.core.exceptions import ObjectDoesNotExist

from dixit import settings
from dixit.utils import ChoicesEnum
from dixit.game.exceptions import GameDeckExhausted, GameRoundIncomplete


class GameStatus(ChoicesEnum):
    NEW = 'new'
    ONGOING = 'ongoing'
    FINISHED = 'finished'
    ABANDONED = 'abandoned'


class Game(models.Model):
    """
    Describes a Dixit game.

    All games have an owner and are created as new. When a game is created
    the owner is added as a player and a new round recorded.

    A Game is `new` when the first round has not started yet. Once it starts
    it changes to `ongoing`. When a player wins it's changed to `finished`, if
    all players but one quit before the game is over, it's marked as `abandoned`
    """

    name = models.CharField(max_length=64)
    status = models.CharField(max_length=16, default='new', choices=GameStatus.choices())
    created_on = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _('game')
        verbose_name_plural = _('games')

        ordering = ('-created_on', )

    def update_status(self):
        from dixit.game.models.round import RoundStatus

        def all_rounds_complete():
            return all(r.status == RoundStatus.COMPLETE for r in self.rounds.all())

        if self.players.count() == 0:
            status = GameStatus.ABANDONED

        elif not self.current_round or all_rounds_complete():
            status = GameStatus.FINISHED

        elif self.current_round.number == 0 and self.current_round.status == RoundStatus.NEW:
            status = GameStatus.NEW

        else:
            status = GameStatus.ONGOING

        if self.status != status:
            self.status = status
            return self.save(update_fields=('status', ))

    @property
    def current_round(self):
        rounds = self.rounds.all().order_by('-number')
        if rounds:
            return rounds[0]
        return None

    def __str__(self):
        return self.name

    @property
    def storyteller(self):
        """
        Storyteller of the current round
        """
        return self.current_round.turn

    @classmethod
    def new_game(cls, name, player_name):
        """
        Bootstraps a new game with a round and a storyteller player
        """
        from dixit.game.models import Player, Round

        game = cls.objects.create(name=name)
        player = Player.objects.create(game=game, name=player_name, owner=True)

        game.add_round()
        return game

    def add_player(self, player_name):
        """
        Adds a new player to the game and deals cards if a round is available
        """
        from dixit.game.models import Player
        from dixit.game.models.round import RoundStatus

        player = Player.objects.create(game=self, name=player_name)
        if self.current_round and self.current_round.status == RoundStatus.NEW:
            self.current_round.deal()

        return player

    def add_round(self):
        """
        Adds a new round to the game for the next player's turn
        """
        from dixit.game.models import Player, Round

        nplayers = self.players.count()
        if nplayers == 0:
            return None

        if not self.current_round:
            number, turn = 0, 0
        else:
            number = self.current_round.number + 1
            turn = (self.current_round.turn.number + 1) % nplayers

        player = Player.objects.get(game=self, number=turn)
        game_round = Round(game=self, number=number, turn=player)

        try:
            game_round.deal()
            game_round.save()
        except GameDeckExhausted:
            return None

        return game_round

    def next_round(self):
        """
        Closes the current round, which updates the scoring, and adds a new round.
        """
        try:
            self.current_round.close()
        except GameRoundIncomplete:
            return None
        return self.add_round()


@receiver(post_save, sender='game.Round')
@receiver(post_delete, sender='game.Player')
def update_game_status(sender, instance, *args, **kwargs):
    return instance.game.update_status()
