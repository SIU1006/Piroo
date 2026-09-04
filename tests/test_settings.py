import settings


def test_with_password_injects_password_when_absent():
    assert (
        settings.with_password("redis://redis-service:6379/0", "hunter2")
        == "redis://:hunter2@redis-service:6379/0"
    )


def test_with_password_preserves_preexisting_credentials():
    assert (
        settings.with_password("redis://user:old@redis:6379/0", "hunter2")
        == "redis://user:old@redis:6379/0"
    )


def test_with_password_noop_without_password():
    assert (
        settings.with_password("redis://redis-service:6379/0", None)
        == "redis://redis-service:6379/0"
    )
