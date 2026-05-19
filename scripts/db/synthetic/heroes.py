"""Hand-curated hero property and counter-pick pools.

These lists are intentionally local to the project. They do not depend on
Dotabuff, OpenDota constants, or the vendored ``dotaconstants`` package.
"""

from __future__ import annotations


TEAM_SIZE = 5

BAD_DRAFT_HERO_POOLS: dict[str, tuple[str, ...]] = {
    # Герои, которые часто требуют много ресурсов/темпа команды и плохо выглядят,
    # когда таких героев в драфте слишком много.
    "greedy": (
        "anti-mage",
        "arc-warden",
        "drow-ranger",
        "faceless-void",
        "medusa",
        "morphling",
        "naga-siren",
        "phantom-assassin",
        "phantom-lancer",
        "spectre",
        "terrorblade",
        "templar-assassin",
        "troll-warlord",
    ),
    # Герои, которые почти не дают надежного контроля сами по себе.
    "no_control": (
        "abaddon",
        "anti-mage",
        "arc-warden",
        "bristleback",
        "broodmother",
        "clinkz",
        "lifestealer",
        "phantom-assassin",
        "razor",
        "sniper",
        "spectre",
        "templar-assassin",
        "timbersaw",
        "venomancer",
        "viper",
        "weaver",
    ),
    # Драфты, которые почти полностью полагаются на магический/чистый урон
    # и плохо заканчивают игру против высокой живучести, BKB и диспелов.
    "no_physical_damage": (
        "ancient-apparition",
        "crystal-maiden",
        "dark-willow",
        "disruptor",
        "earth-spirit",
        "grimstroke",
        "jakiro",
        "keeper-of-the-light",
        "leshrac",
        "lich",
        "muerta",
        "oracle",
        "pugna",
        "rubick",
        "skywrath-mage",
        "storm-spirit",
        "techies",
        "tinker",
        "venomancer",
        "warlock",
        "witch-doctor",
        "zeus",
    ),
    # Драфты, которым тяжело быстро забирать пачки крипов и отпушивать линии.
    # Такие составы отдают карту даже против обычного сбалансированного драфта.
    "slow_wave_clear": (
        "abaddon",
        "bane",
        "bounty-hunter",
        "chen",
        "clinkz",
        "clockwerk",
        "dazzle",
        "enchantress",
        "io",
        "nyx-assassin",
        "ogre-magi",
        "omniknight",
        "oracle",
        "spirit-breaker",
        "tusk",
        "undying",
        "vengeful-spirit",
        "witch-doctor",
    ),
}

# Для каждого типа плохого драфта перечисляем возможных героев по позициям.
# Хороший драфт собирается как один герой с каждой позиции 1..5.
GOOD_DRAFT_COUNTER_POOLS: dict[str, dict[int, tuple[str, ...]]] = {
    "greedy": {
        1: ("ursa", "lifestealer", "juggernaut", "slark", "monkey-king"),
        2: ("puck", "ember-spirit", "storm-spirit", "queen-of-pain", "death-prophet", "huskar", "broodmother"),
        3: ("axe", "beastmaster", "mars", "night-stalker", "slardar", "razor", "visage"),
        4: ("tusk", "earth-spirit", "clockwerk", "tiny", "spirit-breaker"),
        5: ("disruptor", "shadow-shaman", "lion", "grimstroke", "jakiro"),
    },
    "no_control": {
        1: ("weaver", "morphling", "anti-mage", "slark", "monkey-king", "bloodseeker"),
        2: ("storm-spirit", "puck", "ember-spirit", "queen-of-pain", "void-spirit", "pangolier"),
        3: ("timbersaw", "bristleback", "centaur-warrunner", "underlord", "dragon-knight"),
        4: ("spirit-breaker", "earth-spirit", "tusk", "clockwerk", "nyx-assassin"),
        5: ("chen", "enchantress", "shadow-shaman", "jakiro", "undying", "crystal-maiden"),
    },
    "no_physical_damage": {
        1: ("lifestealer", "juggernaut", "anti-mage"),
        2: ("dragon-knight", "templar-assassin", "huskar", "lone-druid", "kunkka", "viper"),
        3: ("bristleback", "centaur-warrunner", "underlord", "tidehunter", "doom", "night-stalker"),
        4: ("omniknight", "abaddon", "spirit-breaker", "clockwerk", "tusk", "marci"),
        5: ("oracle", "dazzle", "chen", "undying", "shadow-demon"),
    },
    "slow_wave_clear": {
        1: ("luna", "sven", "gyrocopter", "juggernaut", "terrorblade", "naga-siren"),
        2: ("lina", "leshrac", "death-prophet", "dragon-knight", "queen-of-pain", "puck"),
        3: ("underlord", "dark-seer", "mars", "centaur-warrunner", "tidehunter", "timbersaw"),
        4: ("rubick", "hoodwink", "mirana", "dark-willow", "earthshaker", "keeper-of-the-light"),
        5: ("crystal-maiden", "jakiro", "warlock", "lich", "shadow-shaman", "disruptor"),
    },
}
