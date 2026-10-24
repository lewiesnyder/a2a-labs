"""Dev keys, the trust store and the injected card (spec lab06 §2). Never use these in production.

``generate`` is idempotent: it creates what is missing, so ``make up LAB=lab06`` can call it every
time. Private keys are git-ignored (``*.private.jwk``); the trust store holds PUBLIC keys only.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from a2a_labs_common import config
from a2a_labs_common.a2a.trust import TrustedKey, TrustStore, sign_card
from jwcrypto import jwk

KIDS = {"lab06-a": "Translator A", "lab06-b": "Translator B"}
HOSTS = {"lab06-a": "lab06-agent-a", "lab06-b": "lab06-agent-b"}
ROGUE_KID = "rogue"


def registry_dir() -> Path:
    env = os.environ.get("LAB06_REGISTRY_DIR")
    return Path(env) if env else config.find_config_dir() / "registry"


def _private_path(root: Path, kid: str) -> Path:
    return root / "keys" / f"{kid}.private.jwk"


def public_of(private: dict[str, Any]) -> dict[str, Any]:
    key = jwk.JWK(**private)
    return dict(json.loads(key.export_public()))


def load_private(kid: str, root: Path | None = None) -> dict[str, Any]:
    return dict(json.loads(_private_path(root or registry_dir(), kid).read_text()))


def load_trust_store(root: Path | None = None) -> TrustStore:
    return TrustStore.load((root or registry_dir()) / "trust_store.json")


def generate(root: Path | None = None, hosts: dict[str, str] | None = None) -> Path:
    """Create missing keys, the trust store and ``injected/translator-b.json`` under ``root``."""
    root = root or registry_dir()
    (root / "keys").mkdir(parents=True, exist_ok=True)
    (root / "injected").mkdir(exist_ok=True)
    for kid in (*KIDS, ROGUE_KID):
        path = _private_path(root, kid)
        if not path.exists():
            path.write_text(jwk.JWK.generate(kty="EC", crv="P-256", kid=kid).export_private())
    host_of = {**HOSTS, **(hosts or {})}
    store_path = root / "trust_store.json"
    if not store_path.exists():
        store = TrustStore(
            trusted_keys=[
                TrustedKey(
                    kid=kid,
                    jwk=public_of(load_private(kid, root)),
                    allowed_names=[name],
                    allowed_hosts=[host_of[kid]],
                )
                for kid, name in KIDS.items()
            ],
            require_signature=True,
            max_card_age_s=86400,
        )
        store_path.write_text(store.model_dump_json(indent=2))
    injected = root / "injected" / "translator-b.json"
    if not injected.exists():
        injected.write_text(_injected_card_json(root))
    return root


def _injected_card_json(root: Path) -> str:
    """A pre-verified, signed copy of Translator B's card with the in-network URLs."""
    from google.protobuf.json_format import MessageToDict

    from lab06_registry.cards import translator_b_card

    card = translator_b_card()
    for iface in card.supported_interfaces:
        iface.url = str(config._endpoints()["lab06-agent-b"]["docker_url"])
    sign_card(card, load_private("lab06-b", root), "lab06-b")
    return json.dumps(MessageToDict(card), indent=2)
