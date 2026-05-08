"""Linear one-hot PyTorch model for Dota 2 draft prediction."""

from __future__ import annotations

import torch
from torch import LongTensor, Tensor, nn
from torch.nn import functional as F


class LinearOneHotDraftModel(nn.Module):
    """Predict logits from Radiant and Dire hero picks.

    The input draft is encoded as ``[radiant_hero_counts, dire_hero_counts]``.
    Each half has ``num_heroes`` features, so the linear layer receives
    ``2 * num_heroes`` features.
    """

    team_size: int = 5

    def __init__(self, num_heroes: int) -> None:
        super().__init__()
        self.num_heroes = num_heroes
        self.regressor = nn.Linear(2 * num_heroes, 1)

    def forward(self, radiant_ids: LongTensor, dire_ids: LongTensor) -> Tensor:
        """Return raw logits with shape ``(batch_size,)``.

        ``radiant_ids`` and ``dire_ids`` must both have shape ``(batch_size, 5)``.
        Apply ``torch.sigmoid(logits)`` outside this model to get probabilities,
        and train with ``nn.BCEWithLogitsLoss``.
        """
        self._validate_inputs(radiant_ids, dire_ids)

        radiant_one_hot = self._team_one_hot(radiant_ids)
        dire_one_hot = self._team_one_hot(dire_ids)

        x = torch.cat([radiant_one_hot, dire_one_hot], dim=1)
        logits = self.regressor(x)
        return logits.squeeze(-1)

    def _team_one_hot(self, hero_ids: LongTensor) -> Tensor:
        one_hot = F.one_hot(hero_ids, num_classes=self.num_heroes)
        return one_hot.sum(dim=1).to(dtype=self.regressor.weight.dtype)

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


__all__ = ["LinearOneHotDraftModel"]
