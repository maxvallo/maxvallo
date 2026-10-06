import re
from dataclasses import dataclass, field

from .models import Offer


def normalize(text: str) -> str:
    text = text.lower().replace("”", '"').replace("″", '"').replace(" ", " ")
    text = re.sub(r"[\-_/+|]+", " ", text)
    return re.sub(r"\s+", " ", text)


def compile_term(term: str) -> re.Pattern:
    if term.startswith("re:"):
        return re.compile(term[3:], re.I)
    words = [re.escape(w) for w in normalize(term).split()]
    return re.compile(r"(?<![a-z0-9])" + r"\s+".join(words) + r"(?![a-z0-9])", re.I)


@dataclass
class Watch:
    id: str
    name: str
    alert_below: float | None = None
    require: list[list[re.Pattern]] = field(default_factory=list)
    exclude: list[re.Pattern] = field(default_factory=list)

    @classmethod
    def from_config(cls, cfg: dict) -> "Watch":
        require = [[compile_term(t) for t in ([group] if isinstance(group, str) else group)]
                   for group in cfg.get("require", [])]
        return cls(
            id=cfg["id"],
            name=cfg.get("name", cfg["id"]),
            alert_below=cfg.get("alert_below"),
            require=require,
            exclude=[compile_term(t) for t in cfg.get("exclude", [])],
        )

    def matches(self, offer: Offer) -> bool:
        text = normalize(offer.match_text())
        if any(p.search(text) for p in self.exclude):
            return False
        return all(any(p.search(text) for p in group) for group in self.require)
