"""Pairwise logistic regression model for Dota 2 draft winner prediction."""

from __future__ import annotations

import torch
from torch import LongTensor, Tensor, nn


class PairInteractionDraftModel(nn.Module):
    """Predict Dire-win logits from independent pair interaction weights.

    This is a logistic regression over draft features: scalar hero strengths,
    unordered allied hero pairs, and directed enemy hero pairs. Every synergy
    and counter pair has its own learned scalar coefficient.

    The returned logit is for ``P(Dire win)``:

    ``bias + dire - radiant + dire_synergy - radiant_synergy
    + dire_counters - radiant_counters``.
    """

    team_size: int = 5

    def __init__(
        self,
        num_heroes: int,
        *,
        include_strength: bool = True,
        include_synergy: bool = True,
        include_counters: bool = True,
    ) -> None:
        super().__init__()

        self.num_heroes = num_heroes
        self.include_strength = include_strength
        self.include_synergy = include_synergy
        self.include_counters = include_counters

        pair_i, pair_j = torch.triu_indices(self.team_size, self.team_size, offset=1)
        self.register_buffer("synergy_pair_i", pair_i, persistent=False)
        self.register_buffer("synergy_pair_j", pair_j, persistent=False)

        self.bias = nn.Parameter(torch.zeros(()))

        self.hero_strength = nn.Embedding(num_heroes, 1)
        self.synergy = nn.Parameter(torch.zeros(num_heroes, num_heroes))
        self.counter = nn.Parameter(torch.zeros(num_heroes, num_heroes))

        self.reset_parameters()

    def reset_parameters(self) -> None:
        nn.init.zeros_(self.hero_strength.weight)
        nn.init.zeros_(self.synergy)
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

        if self.include_synergy:
            logits = logits + self._team_synergy(dire_ids) - self._team_synergy(radiant_ids)

        if self.include_counters:
            logits = logits + self._counter_score(dire_ids, radiant_ids) - self._counter_score(radiant_ids, dire_ids)

        return logits

    def _team_strength(self, team_ids: LongTensor) -> Tensor:
        return self.hero_strength(team_ids).squeeze(-1).sum(dim=1)

    def _team_synergy(self, team_ids: LongTensor) -> Tensor:
        first = team_ids[:, self.synergy_pair_i]
        second = team_ids[:, self.synergy_pair_j]
        hero_a = torch.minimum(first, second)
        hero_b = torch.maximum(first, second)
        return self.synergy[hero_a, hero_b].sum(dim=1)

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


__all__ = ["PairInteractionDraftModel"]
