from __future__ import annotations

from _common import load_settings


def check() -> int:
    load_settings()

    from bot.services.topics import STATUS_CLOSED, STATUS_OPEN, format_topic_name

    cases = (
        (STATUS_OPEN, "Mr. Vlad", "vlad_nitteosca", 5054034063, "🟢 Mr. Vlad (@vlad_nitteosca)"),
        (STATUS_CLOSED, "Mr. Vlad", "vlad_nitteosca", 5054034063, "🔴 Mr. Vlad (@vlad_nitteosca)"),
        (STATUS_OPEN, "Mr. Vlad", None, 5054034063, "🟢 Mr. Vlad (5054034063)"),
        (STATUS_CLOSED, None, "vlad_nitteosca", 5054034063, "🔴 @vlad_nitteosca"),
        (STATUS_OPEN, None, None, 5054034063, "🟢 5054034063"),
    )

    for status, full_name, username, user_id, expected in cases:
        actual = format_topic_name(status, full_name, username, user_id)
        if actual != expected:
            print(f"topic_title: failed expected={expected!r} actual={actual!r}")
            return 1

    print("topic_title: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(check())
