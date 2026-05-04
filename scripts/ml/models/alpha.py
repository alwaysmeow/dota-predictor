"""Alpha PyTorch model for Dota 2 draft winner prediction."""

from __future__ import annotations

import torch
from torch import LongTensor, Tensor, nn


class AlphaDraftModel(nn.Module):
    """Predict Radiant win logits from Radiant and Dire hero picks.

    The model is permutation-invariant inside each team because all team,
    counter-pick, and synergy features are aggregated with sums.
    """

    team_size: int = 5

    def __init__(self, num_heroes: int, embedding_dim: int = 32) -> None:
        super().__init__()
        self.num_heroes = num_heroes
        self.embedding_dim = embedding_dim

        self.hero_embedding = nn.Embedding(num_heroes, embedding_dim)

        pair_i, pair_j = torch.triu_indices(self.team_size, self.team_size, offset=1)
        self.register_buffer("synergy_pair_i", pair_i, persistent=False)
        self.register_buffer("synergy_pair_j", pair_j, persistent=False)

        self.classifier = nn.Sequential(
            nn.Linear(6 * embedding_dim, 128),
            nn.ReLU(),
            nn.Dropout(0.2),
            nn.Linear(128, 64),
            nn.ReLU(),
            nn.Dropout(0.1),
            nn.Linear(64, 1),
        )

    def forward(self, radiant_ids: LongTensor, dire_ids: LongTensor) -> Tensor:
        """Return raw Radiant-win logits with shape ``(batch_size,)``.

        ``radiant_ids`` and ``dire_ids`` must both have shape ``(batch_size, 5)``.
        Apply ``torch.sigmoid(logits)`` outside this model to get probabilities,
        and train with ``nn.BCEWithLogitsLoss``.
        """
        self._validate_inputs(radiant_ids, dire_ids)

        radiant_emb = self.hero_embedding(radiant_ids)
        dire_emb = self.hero_embedding(dire_ids)

        r_sum = radiant_emb.sum(dim=1)
        d_sum = dire_emb.sum(dim=1)
        diff = r_sum - d_sum

        counter_pairs = radiant_emb[:, :, None, :] * dire_emb[:, None, :, :]
        counter = counter_pairs.sum(dim=(1, 2))

        r_synergy = self._team_synergy(radiant_emb)
        d_synergy = self._team_synergy(dire_emb)

        x = torch.cat([r_sum, d_sum, diff, counter, r_synergy, d_synergy], dim=1)
        logits = self.classifier(x)
        return logits.squeeze(-1)

    def _team_synergy(self, team_emb: Tensor) -> Tensor:
        synergy_pairs = team_emb[:, self.synergy_pair_i, :] * team_emb[:, self.synergy_pair_j, :]
        return synergy_pairs.sum(dim=1)

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

__all__ = ["AlphaDraftModel"]