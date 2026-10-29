"""Password strength rules shared by registration and password changes.

Length is the primary control (NIST SP 800-63B); a small common-password
denylist blocks the most abused choices without imposing arbitrary character
classes. A full breached-password service can be layered on later.
"""

# Lowercase values. Keep this compact; it is a floor, not a full breach corpus.
COMMON_PASSWORDS: frozenset[str] = frozenset(
    {
        "123456789012",
        "1234567890123",
        "12345678901234",
        "password1234",
        "password12345",
        "passwordpassword",
        "qwertyuiop12",
        "qwertyuiop123",
        "qwertyuiop1234",
        "letmein12345",
        "welcome12345",
        "admin123456",
        "iloveyou1234",
        "pitchpilot123",
        "changeme1234",
        "aaaaaaaaaaaa",
    }
)


def validate_password_strength(password: str) -> str:
    """Raise ValueError when a password is trivially guessable."""
    lowered = password.lower()
    if lowered in COMMON_PASSWORDS:
        raise ValueError("Choose a less common password")
    if len(set(password)) < 4:
        raise ValueError("Choose a password with more variety")
    return password


def password_contains_identity(password: str, email: str) -> bool:
    """True when the password embeds the email local part (e.g. alice123456)."""
    local = email.split("@", 1)[0].strip().lower()
    return len(local) >= 3 and local in password.lower()
