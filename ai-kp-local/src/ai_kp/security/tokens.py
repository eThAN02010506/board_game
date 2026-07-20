import hashlib
import secrets


JOIN_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def generate_access_token() -> str:
    return secrets.token_urlsafe(32)


def generate_realtime_ticket() -> str:
    return secrets.token_urlsafe(24)


def generate_join_code() -> str:
    raw = "".join(secrets.choice(JOIN_CODE_ALPHABET) for _ in range(12))
    return "-".join(raw[index : index + 4] for index in range(0, len(raw), 4))


def normalize_join_code(value: str) -> str:
    return "".join(character for character in value.upper() if character.isalnum())


def hash_secret(secret: str, purpose: str) -> str:
    return hashlib.sha256(f"ai-kp-local:{purpose}:{secret}".encode()).hexdigest()


def hash_access_token(token: str) -> str:
    return hash_secret(token, "access-token")


def hash_join_code(code: str) -> str:
    return hash_secret(normalize_join_code(code), "join-code")


def hash_realtime_ticket(ticket: str) -> str:
    return hash_secret(ticket, "realtime-ticket")
