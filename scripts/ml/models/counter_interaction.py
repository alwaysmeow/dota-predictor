"""Hero plus counter-pick logistic regression model for Dota 2 drafts."""

from __future__ import annotations

import torch
from torch import LongTensor, Tensor, nn


class CounterInteractionDraftModel(nn.Module):
    """Predict Dire-win logits from hero strengths and directed counters.

    This is a smaller pairwise logistic regression than
    ``PairInteractionDraftModel``. It has no allied synergy parameters; every
    cross-team directed hero matchup still has its own learned scalar
    coefficient.

    The returned logit is for ``P(Dire win)``:

    ``bias + dire_strength - radiant_strength
    + dire_counters - radiant_counters``.
    """

    team_size: int = 5

    def __init__(
        self,
        num_heroes: int,
        *,
        include_strength: bool = True,
        include_counters: bool = True,
    ) -> None:
        super().__init__()

        self.num_heroes = num_heroes
        self.include_strength = include_strength
        self.include_counters = include_counters

        self.bias = nn.Parameter(torch.zeros(()))
        self.hero_strength = nn.Embedding(num_heroes, 1)
        self.counter = nn.Parameter(torch.zeros(num_heroes, num_heroes))

        self.reset_parameters()

    def reset_parameters(self) -> None:
        nn.init.zeros_(self.hero_strength.weight)
        nn.init.zeros_(self.counter)
        nn.init.zeros_(self.bias)

    def forward(self, radiant_ids: LongTensor, dire_ids: LongTensor) -> Tensor:
        """Return raw Dire-win logits with shape ``(batch_size,)``.

        ``radiant_ids`` and ``dire_ids`` must both have shape ``(batch_size, 5)``.
        Apply ``torch.sigmoid(logits)`` outside this model to get Dire-win
        probabilities, and train with ``nn.BCEWithLogitsLoss``.
        """
        self._validate_inputs(radiant_ids, dire_ids)

        logits = self.bias.expand(radiant_ids.shape[0])

        if self.include_strength:
            logits = logits + self._team_strength(dire_ids) - self._team_strength(radiant_ids)

        if self.include_counters:
            logits = logits + self._counter_score(dire_ids, radiant_ids) - self._counter_score(radiant_ids, dire_ids)

        return logits

    def _team_strength(self, team_ids: LongTensor) -> Tensor:
        return self.hero_strength(team_ids).squeeze(-1).sum(dim=1)

    def _counter_score(self, attacker_ids: LongTensor, victim_ids: LongTensor) -> Tensor:
        pair_weights = self.counter[attacker_ids[:, :, None], victim_ids[:, None, :]]
        return pair_weights.sum(dim=(1, 2))

    def _validate_inputs(self, radiant_ids: LongTensor, dire_ids: LongTensor) -> None:
        expected_shape_suffix = (self.team_size,)
        if radiant_ids.shape[1:] != expected_shape_suffix:
            raise ValueError(f"radiant_ids must have shape (batch_size, 5), got {tuple(radiant_ids.shape)}")
        if dire_ids.shape[1:] != expected_shape_suffix:
            raise ValueError(f"dire_ids must have shape (batch_size, 5), got {tuple(dire_ids.shape)}")
        if radiant_ids.dtype != torch.long:
            raise TypeError(f"radiant_ids must be a LongTensor, got {radiant_ids.dtype}")
        if dire_ids.dtype != torch.long:
            raise TypeError(f"dire_ids must be a LongTensor, got {dire_ids.dtype}")


__all__ = ["CounterInteractionDraftModel"]
